"""Project API routes.

REST endpoints for project management.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, status

from server.app.api.models import (
    ProjectCreate,
    ProjectResponse,
    ProjectList,
    ErrorResponse,
)
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/projects", tags=["projects"])


def get_workspace_root(settings: Settings = Depends(get_settings)) -> Path:
    """Get the workspace root directory."""
    return settings.workspace_root


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        409: {"model": ErrorResponse, "description": "Project already exists"},
    },
)
async def create_project(
    request: ProjectCreate,
    workspace_root: Path = Depends(get_workspace_root),
) -> ProjectResponse:
    """Create a new project.

    Creates a new project with its own workspace directory.
    """
    # Generate project ID
    project_id = str(uuid.uuid4())

    # Determine project path
    if request.path:
        project_path = Path(request.path).resolve()
    else:
        # Sanitize name for directory
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.name)
        project_path = workspace_root / f"{safe_name}-{project_id[:8]}"

    # Check if path already exists
    if project_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project path already exists: {project_path}",
        )

    # Create project directory
    try:
        project_path.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create project directory: {str(e)}",
        )

    now = datetime.utcnow()

    # In a real implementation, this would persist to a database
    # For now, we just return the response
    return ProjectResponse(
        id=project_id,
        name=request.name,
        description=request.description,
        path=str(project_path),
        created_at=now,
        updated_at=now,
    )


@router.get(
    "",
    response_model=ProjectList,
    responses={
        500: {"model": ErrorResponse, "description": "Server error"},
    },
)
async def list_projects(
    workspace_root: Path = Depends(get_workspace_root),
) -> ProjectList:
    """List all projects.

    Returns a list of all projects in the workspace.
    """
    projects = []

    # Scan workspace for project directories
    if workspace_root.exists():
        for item in workspace_root.iterdir():
            if item.is_dir():
                # In a real implementation, we'd read project metadata from a database
                # For now, create a basic project entry
                stat = item.stat()
                projects.append(
                    ProjectResponse(
                        id=str(uuid.uuid4()),  # Would be stored in metadata
                        name=item.name,
                        description=None,
                        path=str(item),
                        created_at=datetime.fromtimestamp(stat.st_ctime),
                        updated_at=datetime.fromtimestamp(stat.st_mtime),
                    )
                )

    return ProjectList(projects=projects, total=len(projects))


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Project not found"},
    },
)
async def get_project(project_id: str) -> ProjectResponse:
    """Get project details.

    Returns detailed information about a specific project.
    """
    # In a real implementation, this would look up in a database
    # For now, raise 404
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Project not found: {project_id}",
    )


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Project not found"},
    },
)
async def delete_project(project_id: str) -> None:
    """Delete a project.

    Deletes a project and all associated data.
    """
    # In a real implementation:
    # 1. Look up project in database
    # 2. Delete project directory
    # 3. Delete all associated sessions
    # 4. Remove from database

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Project not found: {project_id}",
    )
