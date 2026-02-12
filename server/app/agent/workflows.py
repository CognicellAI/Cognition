"""Agent workflows for Phase 4.

- Multi-step planning and execution
- Subtask decomposition
- Parallel tool execution
- Human-in-the-loop for destructive operations
- Undo/redo capability
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A task in a workflow."""

    id: str
    description: str
    tool_name: Optional[str] = None
    tool_args: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    requires_approval: bool = False
    approved: bool = False


@dataclass
class Plan:
    """A plan consisting of multiple tasks."""

    id: str
    description: str
    tasks: list[Task] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class TaskPlanner:
    """Plans and breaks down complex tasks into subtasks."""

    def create_plan(self, description: str, steps: list[dict]) -> Plan:
        """Create a plan from a list of step descriptions."""
        plan_id = str(uuid.uuid4())
        tasks = []

        for i, step in enumerate(steps):
            task = Task(
                id=f"{plan_id}_{i}",
                description=step.get("description", f"Step {i + 1}"),
                tool_name=step.get("tool"),
                tool_args=step.get("args", {}),
                requires_approval=step.get("requires_approval", False),
            )
            tasks.append(task)

        return Plan(
            id=plan_id,
            description=description,
            tasks=tasks,
        )

    def estimate_complexity(self, description: str) -> dict:
        """Estimate the complexity of a task."""
        # Simple heuristic-based estimation
        indicators = {
            "high": ["refactor", "restructure", "migrate", "architecture", "redesign"],
            "medium": ["add", "implement", "create", "update", "modify"],
            "low": ["fix", "debug", "test", "check", "search"],
        }

        desc_lower = description.lower()
        for level, keywords in indicators.items():
            if any(kw in desc_lower for kw in keywords):
                return {
                    "level": level,
                    "estimated_steps": {"high": 8, "medium": 4, "low": 2}[level],
                }

        return {"level": "medium", "estimated_steps": 4}


class WorkflowOrchestrator:
    """Orchestrates multi-step workflows.

    Handles:
    - Task execution order (respecting dependencies)
    - Parallel execution where safe
    - Error handling and retries
    - Progress tracking
    """

    def __init__(self, tool_executor: Callable[[str, dict], Any]):
        self.tool_executor = tool_executor
        self.active_plans: dict[str, Plan] = {}

    async def execute_plan(
        self,
        plan: Plan,
        on_task_start: Optional[Callable[[Task], None]] = None,
        on_task_complete: Optional[Callable[[Task, Any], None]] = None,
        on_task_error: Optional[Callable[[Task, Exception], None]] = None,
    ) -> Plan:
        """Execute a plan, running tasks in dependency order."""
        self.active_plans[plan.id] = plan

        # Build dependency graph
        task_map = {t.id: t for t in plan.tasks}
        completed_tasks: set[str] = set()

        try:
            while len(completed_tasks) < len(plan.tasks):
                # Find tasks that are ready to run
                ready_tasks = [
                    t
                    for t in plan.tasks
                    if t.status == TaskStatus.PENDING
                    and all(dep in completed_tasks for dep in t.depends_on)
                ]

                if not ready_tasks:
                    # Check for deadlock (exclude waiting for approval)
                    stuck_tasks = [
                        t
                        for t in plan.tasks
                        if t.status
                        not in (
                            TaskStatus.COMPLETED,
                            TaskStatus.FAILED,
                            TaskStatus.CANCELLED,
                            TaskStatus.WAITING_APPROVAL,
                        )
                    ]
                    if stuck_tasks:
                        for t in stuck_tasks:
                            t.status = TaskStatus.FAILED
                            t.error = "Dependency resolution failed"
                    break

                # Execute ready tasks (can be parallelized)
                for task in ready_tasks:
                    if task.requires_approval and not task.approved:
                        task.status = TaskStatus.WAITING_APPROVAL
                        continue

                    await self._execute_task(
                        task,
                        on_task_start,
                        on_task_complete,
                        on_task_error,
                    )
                    completed_tasks.add(task.id)

            plan.completed_at = time.time()
            return plan

        finally:
            del self.active_plans[plan.id]

    async def _execute_task(
        self,
        task: Task,
        on_start: Optional[Callable[[Task], None]],
        on_complete: Optional[Callable[[Task, Any], None]],
        on_error: Optional[Callable[[Task, Exception], None]],
    ) -> None:
        """Execute a single task."""
        task.status = TaskStatus.IN_PROGRESS

        if on_start:
            on_start(task)

        try:
            if task.tool_name:
                result = self.tool_executor(task.tool_name, task.tool_args)
            else:
                result = None

            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()

            if on_complete:
                on_complete(task, result)

        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED

            if on_error:
                on_error(task, e)

    def get_plan_status(self, plan_id: str) -> dict:
        """Get the current status of a plan."""
        plan = self.active_plans.get(plan_id)
        if not plan:
            return {"error": "Plan not found"}

        total = len(plan.tasks)
        completed = sum(1 for t in plan.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)
        pending = sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in plan.tasks if t.status == TaskStatus.IN_PROGRESS)

        return {
            "plan_id": plan_id,
            "description": plan.description,
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "in_progress": in_progress,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
        }


@dataclass
class Change:
    """A single change made to a file."""

    id: str
    file_path: str
    change_type: str  # "create", "modify", "delete"
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class ChangeTracker:
    """Tracks file changes for undo/redo capability.

    Creates snapshots before destructive operations
    and allows rolling back changes.
    """

    def __init__(self, sandbox: Any):
        self.sandbox = sandbox
        self.changes: list[Change] = []
        self.redo_stack: list[Change] = []
        self.snapshots: dict[str, str] = {}  # path -> content hash

    def snapshot_file(self, file_path: str) -> Optional[str]:
        """Create a snapshot of a file before modification."""
        result = self.sandbox.execute(f'cat "{file_path}"')
        if result.exit_code == 0:
            content = result.output
            file_hash = hashlib.md5(content.encode()).hexdigest()
            self.snapshots[file_path] = content
            return file_hash
        return None

    def record_change(
        self,
        file_path: str,
        change_type: str,
        before_hash: Optional[str] = None,
    ) -> Change:
        """Record a change that was made."""
        # Get after hash
        result = self.sandbox.execute(f'cat "{file_path}"')
        after_hash = None
        if result.exit_code == 0:
            after_hash = hashlib.md5(result.output.encode()).hexdigest()

        change = Change(
            id=str(uuid.uuid4()),
            file_path=file_path,
            change_type=change_type,
            before_hash=before_hash,
            after_hash=after_hash,
        )

        self.changes.append(change)
        self.redo_stack.clear()  # Clear redo on new change

        return change

    def undo(self) -> Optional[Change]:
        """Undo the last change."""
        if not self.changes:
            return None

        change = self.changes.pop()

        # Restore from snapshot
        if change.file_path in self.snapshots:
            content = self.snapshots[change.file_path]
            # Write back (this is simplified - in practice use proper file write)
            escaped_content = content.replace('"', '\\"')
            self.sandbox.execute(f'echo "{escaped_content}" > "{change.file_path}"')

        self.redo_stack.append(change)
        return change

    def redo(self) -> Optional[Change]:
        """Redo the last undone change."""
        if not self.redo_stack:
            return None

        change = self.redo_stack.pop()
        # In practice, would re-apply the change
        self.changes.append(change)
        return change

    def get_history(self) -> list[Change]:
        """Get the full change history."""
        return list(self.changes)


class ApprovalManager:
    """Manages human-in-the-loop approvals for destructive operations.

    Handles:
    - Queueing operations requiring approval
    - Approval/rejection workflow
    - Timeout handling
    """

    def __init__(self, timeout_seconds: float = 300.0):
        self.timeout_seconds = timeout_seconds
        self.pending_approvals: dict[str, dict] = {}

    def request_approval(
        self,
        operation_type: str,
        description: str,
        details: Optional[dict] = None,
    ) -> str:
        """Request approval for an operation.

        Returns:
            Approval ID that can be used to check status.
        """
        approval_id = str(uuid.uuid4())

        self.pending_approvals[approval_id] = {
            "id": approval_id,
            "operation_type": operation_type,
            "description": description,
            "details": details or {},
            "status": "pending",
            "requested_at": time.time(),
            "responded_at": None,
            "response": None,
        }

        return approval_id

    def approve(self, approval_id: str) -> bool:
        """Approve a pending operation."""
        if approval_id not in self.pending_approvals:
            return False

        approval = self.pending_approvals[approval_id]
        approval["status"] = "approved"
        approval["responded_at"] = time.time()
        approval["response"] = "approved"

        return True

    def reject(self, approval_id: str, reason: Optional[str] = None) -> bool:
        """Reject a pending operation."""
        if approval_id not in self.pending_approvals:
            return False

        approval = self.pending_approvals[approval_id]
        approval["status"] = "rejected"
        approval["responded_at"] = time.time()
        approval["response"] = reason or "rejected"

        return True

    def get_status(self, approval_id: str) -> Optional[dict]:
        """Get the status of an approval request."""
        if approval_id not in self.pending_approvals:
            return None

        approval = self.pending_approvals[approval_id].copy()

        # Check for timeout
        if approval["status"] == "pending":
            elapsed = time.time() - approval["requested_at"]
            if elapsed > self.timeout_seconds:
                approval["status"] = "timeout"
                approval["response"] = f"Timed out after {self.timeout_seconds}s"

        return approval

    def is_approved(self, approval_id: str) -> bool:
        """Check if an operation has been approved."""
        status = self.get_status(approval_id)
        return status is not None and status["status"] == "approved"

    def cleanup_expired(self) -> int:
        """Remove expired approval requests."""
        expired = []
        for approval_id, approval in self.pending_approvals.items():
            if approval["status"] == "pending":
                elapsed = time.time() - approval["requested_at"]
                if elapsed > self.timeout_seconds:
                    expired.append(approval_id)

        for approval_id in expired:
            self.pending_approvals[approval_id]["status"] = "timeout"

        return len(expired)
