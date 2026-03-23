from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, cast

import httpx


DEFAULT_PROMPT = (
    "Use the write_file tool to create a file named hitl_test.txt containing exactly "
    "the word approved. Do not answer in prose. Attempt the tool call."
)


class HitlCheckError(RuntimeError):
    pass


async def _request_with_scope(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    scope_headers: dict[str, str],
    **kwargs: Any,
) -> httpx.Response:
    headers = {**scope_headers, **kwargs.pop("headers", {})}
    return await client.request(method, url, headers=headers, **kwargs)


async def _get_scope_headers(client: httpx.AsyncClient, base_url: str) -> dict[str, str]:
    response = await client.get(f"{base_url}/config")
    response.raise_for_status()
    config = response.json()
    if config.get("server", {}).get("scoping_enabled", False):
        return {"X-Cognition-Scope-User": "manual-hitl-check"}
    return {}


async def _create_session(
    client: httpx.AsyncClient,
    base_url: str,
    scope_headers: dict[str, str],
    agent_name: str,
) -> str:
    response = await _request_with_scope(
        client,
        "POST",
        f"{base_url}/sessions",
        scope_headers,
        json={"title": "manual-hitl-check", "agent_name": agent_name},
    )
    response.raise_for_status()
    return str(response.json()["id"])


async def _stream_events(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    prompt: str,
    scope_headers: dict[str, str],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    async with client.stream(
        "POST",
        f"{base_url}/sessions/{session_id}/messages",
        headers={
            **scope_headers,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        },
        json={"content": prompt},
        timeout=httpx.Timeout(timeout_seconds),
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
                continue
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                payload["event"] = current_event
                events.append(payload)
                if current_event in {"interrupt", "done", "error"}:
                    break
                current_event = None
    return events


async def _get_session(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    scope_headers: dict[str, str],
) -> dict[str, Any]:
    response = await _request_with_scope(
        client,
        "GET",
        f"{base_url}/sessions/{session_id}",
        scope_headers,
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def _resume_session(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    tool_call_id: str,
    decision: str,
    scope_headers: dict[str, str],
) -> dict[str, Any]:
    response = await _request_with_scope(
        client,
        "POST",
        f"{base_url}/sessions/{session_id}/resume",
        scope_headers,
        json={"decision": decision, "tool_call_id": tool_call_id},
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def _stream_resume_events(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    tool_call_id: str,
    decision: str,
    scope_headers: dict[str, str],
    timeout_seconds: float,
    args: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    async with client.stream(
        "POST",
        f"{base_url}/sessions/{session_id}/resume",
        headers={
            **scope_headers,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "decision": decision,
            "tool_call_id": tool_call_id,
            "tool_name": (args or {}).get("_tool_name", "write_file"),
            "args": args,
        },
        timeout=httpx.Timeout(timeout_seconds),
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
                continue
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                payload["event"] = current_event
                events.append(payload)
                if current_event in {"done", "error"}:
                    break
                current_event = None
    return events


async def _delete_session(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    scope_headers: dict[str, str],
) -> None:
    await _request_with_scope(
        client,
        "DELETE",
        f"{base_url}/sessions/{session_id}",
        scope_headers,
    )


async def run_check(
    base_url: str,
    agent_name: str,
    prompt: str,
    decision: str,
    timeout_seconds: float,
    cleanup: bool,
) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0)) as client:
        scope_headers = await _get_scope_headers(client, base_url)
        session_id = await _create_session(client, base_url, scope_headers, agent_name)
        print(f"session_id={session_id}")

        try:
            events = await _stream_events(
                client,
                base_url,
                session_id,
                prompt,
                scope_headers,
                timeout_seconds,
            )

            print("stream_events=")
            print(json.dumps(events, indent=2))

            interrupt_event = next(
                (event for event in events if event.get("event") == "interrupt"), None
            )
            if interrupt_event is None:
                raise HitlCheckError(
                    "No interrupt event observed. The model likely did not attempt a protected tool call."
                )

            session = await _get_session(client, base_url, session_id, scope_headers)
            print("session_after_interrupt=")
            print(json.dumps(session, indent=2))

            if session.get("status") != "waiting_for_approval":
                raise HitlCheckError(
                    f"Expected waiting_for_approval status, got {session.get('status')!r}"
                )

            tool_call_id = interrupt_event.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise HitlCheckError("Interrupt event did not include a valid tool_call_id")

            resume_events = await _stream_resume_events(
                client,
                base_url,
                session_id,
                tool_call_id,
                decision,
                scope_headers,
                timeout_seconds,
                (
                    {
                        **interrupt_event.get("args", {}),
                        "_tool_name": interrupt_event.get("tool_name") or "write_file",
                    }
                )
                if decision == "edit"
                else {"_tool_name": interrupt_event.get("tool_name") or "write_file"},
            )
            print("resume_events=")
            print(json.dumps(resume_events, indent=2))

            if not any(event.get("event") == "done" for event in resume_events):
                raise HitlCheckError("Resume stream did not complete successfully")

            session_after_resume = await _get_session(client, base_url, session_id, scope_headers)
            print("session_after_resume=")
            print(json.dumps(session_after_resume, indent=2))

            print("HITL check passed")
            return 0
        finally:
            if cleanup:
                await _delete_session(client, base_url, session_id, scope_headers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual HITL verification script")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--agent-name", default="hitl_test")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--decision", choices=["approve", "edit", "reject"], default="approve")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-cleanup", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(
            run_check(
                base_url=args.base_url,
                agent_name=args.agent_name,
                prompt=args.prompt,
                decision=args.decision,
                timeout_seconds=args.timeout,
                cleanup=not args.no_cleanup,
            )
        )
    except HitlCheckError as exc:
        print(f"HITL check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
