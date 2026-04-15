# Cognition K8s Sandbox runtime server
#
# Implements the agent-sandbox HTTP protocol on port 8888:
#   POST /execute        - run a shell command, return stdout/stderr/exit_code
#   POST /upload         - upload a file into the sandbox
#   GET  /download/{path}- download a file from the sandbox
#   GET  /list/{path}    - list directory contents
#   GET  /exists/{path}  - check if a path exists
#   GET  /               - health check
#
# Working directory is /workspace (emptyDir mount from SandboxTemplate).
# Path security is enforced by CognitionKubernetesSandboxBackend upstream;
# this server does not restrict paths within the sandbox itself.
#
# Compatible with k8s-agent-sandbox==0.3.10

import os
import subprocess
import urllib.parse

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

WORKSPACE = os.environ.get("COGNITION_WORKSPACE_ROOT", "/workspace")


class ExecuteRequest(BaseModel):
    command: str


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


app = FastAPI(
    title="Cognition Sandbox Runtime",
    description="K8s sandbox runtime for Cognition agent sessions",
    version="1.0.0",
)


@app.get("/", summary="Health check")
async def health_check():
    return {"status": "ok", "workspace": WORKSPACE}


@app.post("/execute", summary="Execute a shell command", response_model=ExecuteResponse)
async def execute_command(request: ExecuteRequest):
    try:
        process = subprocess.run(
            ["sh", "-c", request.command],
            capture_output=True,
            text=True,
            cwd=WORKSPACE,
            env={**os.environ, "HOME": "/home/sandbox"},
        )
        return ExecuteResponse(
            stdout=process.stdout,
            stderr=process.stderr,
            exit_code=process.returncode,
        )
    except Exception as e:
        return ExecuteResponse(stdout="", stderr=str(e), exit_code=1)


@app.post("/upload", summary="Upload a file")
async def upload_file(file: UploadFile = File(...)):
    try:
        dest = os.path.join(WORKSPACE, file.filename)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(await file.read())
        return JSONResponse(status_code=200, content={"message": f"Uploaded {file.filename}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": str(e)})


@app.get("/download/{encoded_path:path}", summary="Download a file")
async def download_file(encoded_path: str):
    path = urllib.parse.unquote(encoded_path)
    full = os.path.join(WORKSPACE, path.lstrip("/")) if not os.path.isabs(path) else path
    if os.path.isfile(full):
        return FileResponse(path=full, media_type="application/octet-stream", filename=os.path.basename(full))
    return JSONResponse(status_code=404, content={"message": "File not found"})


@app.get("/list/{encoded_path:path}", summary="List directory")
async def list_files(encoded_path: str):
    path = urllib.parse.unquote(encoded_path)
    full = os.path.join(WORKSPACE, path.lstrip("/")) if not os.path.isabs(path) else path
    if not os.path.isdir(full):
        return JSONResponse(status_code=404, content={"message": "Not a directory"})
    try:
        entries = []
        with os.scandir(full) as it:
            for entry in it:
                s = entry.stat()
                entries.append({
                    "name": entry.name,
                    "size": s.st_size,
                    "type": "directory" if entry.is_dir() else "file",
                    "mod_time": s.st_mtime,
                })
        return JSONResponse(status_code=200, content=entries)
    except Exception as e:
        return JSONResponse(status_code=500, content={"message": str(e)})


@app.get("/exists/{encoded_path:path}", summary="Check path exists")
async def exists(encoded_path: str):
    path = urllib.parse.unquote(encoded_path)
    full = os.path.join(WORKSPACE, path.lstrip("/")) if not os.path.isabs(path) else path
    return JSONResponse(status_code=200, content={"path": path, "exists": os.path.exists(full)})
