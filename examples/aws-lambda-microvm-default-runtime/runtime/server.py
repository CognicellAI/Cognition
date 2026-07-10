from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# The runtime listens on the same port configured in the Cognition
# SandboxProfile. AWS Lambda MicroVM proxy auth controls access to this port.
PORT = int(os.environ.get("PORT", "8080"))

# All file operations and default command execution are confined to this
# workspace root. Builders can change the path through image environment
# variables, but Cognition profiles should match that choice.
WORKSPACE_ROOT = Path(os.environ.get("COGNITION_WORKSPACE_ROOT", "/workspace")).resolve()

# Keep request bodies and command output bounded. These limits prevent accidental
# large uploads or stdout floods from making Cognition streams and logs painful.
MAX_BODY_BYTES = int(os.environ.get("COGNITION_MAX_BODY_BYTES", str(50 * 1024 * 1024)))
MAX_CAPTURE_BYTES = int(os.environ.get("COGNITION_MAX_CAPTURE_BYTES", str(2 * 1024 * 1024)))

# AWS can call lifecycle hooks while building, running, resuming, suspending, or
# terminating a MicroVM. The default runtime acknowledges them without side
# effects. Builders may add setup/cleanup behavior here when needed.
LIFECYCLE_HOOKS = {
    "/aws/lambda-microvms/runtime/v1/ready",
    "/aws/lambda-microvms/runtime/v1/validate",
    "/aws/lambda-microvms/runtime/v1/run",
    "/aws/lambda-microvms/runtime/v1/resume",
    "/aws/lambda-microvms/runtime/v1/suspend",
    "/aws/lambda-microvms/runtime/v1/terminate",
    "/ready",
    "/validate",
    "/run",
    "/resume",
    "/suspend",
    "/terminate",
}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    """Encode compact JSON responses so Content-Length is exact."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_workspace_path(raw_path: str | None) -> Path:
    """Resolve a request path and reject traversal outside the workspace."""
    if not raw_path:
        raise ValueError("path is required")
    relative = raw_path.lstrip("/")
    resolved = (WORKSPACE_ROOT / relative).resolve()
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        raise ValueError("path escapes workspace root")
    return resolved


def _trim_output(data: bytes) -> str:
    """Decode command output and mark it when capture limits truncate it."""
    truncated = data[:MAX_CAPTURE_BYTES]
    text = truncated.decode("utf-8", errors="replace")
    if len(data) > MAX_CAPTURE_BYTES:
        text += "\n[truncated]"
    return text


class Handler(BaseHTTPRequestHandler):
    """Small JSON HTTP server implementing Cognition's sandbox protocol."""

    server_version = "cognition-lambda-microvm-command-server/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Runtime logs may be forwarded to CloudWatch depending on the
        # SandboxProfile. Keep logs operational and avoid printing request bodies.
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if content_length == 0:
            return {}
        data = self.rfile.read(content_length)
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "workspace_root": str(WORKSPACE_ROOT),
                    "time": time.time(),
                },
            )
            return
        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            self._handle_download({"path": query.get("path", [None])[0]})
            return
        if parsed.path in LIFECYCLE_HOOKS:
            self._send_json(HTTPStatus.OK, {"status": "ok", "hook": parsed.path})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in LIFECYCLE_HOOKS:
                self._send_json(HTTPStatus.OK, {"status": "ok", "hook": parsed.path})
                return
            body = self._read_json()
            if parsed.path == "/execute":
                self._handle_execute(body)
            elif parsed.path == "/upload":
                self._handle_upload(body)
            elif parsed.path == "/download":
                self._handle_download(body)
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception as exc:
            # Cognition treats non-2xx responses as command-server failures. Keep
            # this response structured and token-free.
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": type(exc).__name__, "message": str(exc)},
            )

    def _handle_execute(self, body: dict[str, Any]) -> None:
        # Cognition currently sends {"command": ["sh", "-c", "..."], "cwd": ".",
        # "timeout_seconds": N}. This also accepts a command string for manual
        # testing and future-compatible clients.
        command = body.get("command", body.get("cmd"))
        if isinstance(command, list) and command:
            argv = [str(part) for part in command]
        elif isinstance(command, str) and command.strip():
            args = body.get("args", [])
            argv = [command, *[str(arg) for arg in args]] if isinstance(args, list) else shlex.split(command)
        else:
            raise ValueError("command must be a non-empty string or list")

        cwd = _safe_workspace_path(str(body.get("cwd", ".")))
        cwd.mkdir(parents=True, exist_ok=True)
        timeout = float(body.get("timeout_seconds", body.get("timeout", 60)))

        # Optional env values are trusted runtime input from Cognition, not model
        # output. Builders who expose this server to other callers should add
        # policy checks before accepting arbitrary env overrides.
        extra_env = body.get("env", {})
        if extra_env is not None and not isinstance(extra_env, dict):
            raise ValueError("env must be an object")
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in (extra_env or {}).items()})

        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = exc.stdout or b""
            stderr = (exc.stderr or b"") + b"\ncommand timed out"

        self._send_json(
            HTTPStatus.OK,
            {
                "exit_code": exit_code,
                "stdout": _trim_output(stdout),
                "stderr": _trim_output(stderr),
                "timed_out": timed_out,
                "duration_ms": int((time.monotonic() - start) * 1000),
            },
        )

    def _handle_upload(self, body: dict[str, Any]) -> None:
        # Uploads are relative to WORKSPACE_ROOT. Cognition sends base64 so binary
        # files round-trip without JSON encoding loss.
        destination = _safe_workspace_path(str(body.get("path", "")))
        destination.parent.mkdir(parents=True, exist_ok=True)

        if "content_base64" in body:
            raw = base64.b64decode(str(body["content_base64"]), validate=True)
        elif "content" in body:
            raw = str(body["content"]).encode(str(body.get("encoding", "utf-8")))
        else:
            raise ValueError("content_base64 or content is required")

        destination.write_bytes(raw)
        self._send_json(
            HTTPStatus.OK,
            {"status": "ok", "path": str(destination.relative_to(WORKSPACE_ROOT)), "bytes": len(raw)},
        )

    def _handle_download(self, body: dict[str, Any]) -> None:
        # Downloads return base64 for exact binary preservation. Cognition decodes
        # this into Deep Agents file download responses.
        source = _safe_workspace_path(str(body.get("path", "")))
        raw = source.read_bytes()
        self._send_json(
            HTTPStatus.OK,
            {
                "path": str(source.relative_to(WORKSPACE_ROOT)),
                "bytes": len(raw),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            },
        )


def main() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"cognition Lambda MicroVM command server listening on {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
