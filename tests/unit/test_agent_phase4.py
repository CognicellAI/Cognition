"""Tests for Phase 4: Advanced Agent Capabilities.

Tests:
- Enhanced tool system (Git, search, tests, lint)
- Context management (indexing, relevance)
- Workflows (planning, orchestration, approvals, undo)
- Output formatting (diffs, highlighting)
"""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path
import time

from server.app.agent.tools import (
    ToolRegistry,
    ToolCategory,
    Tool,
    GitTools,
    SearchTools,
    TestTools,
    LintTools,
    register_enhanced_tools,
)
from server.app.agent.context import (
    ContextManager,
    ProjectIndex,
    FileInfo,
    FileRelevanceScorer,
)
from server.app.agent.workflows import (
    WorkflowOrchestrator,
    TaskPlanner,
    Task,
    Plan,
    TaskStatus,
    ChangeTracker,
    ApprovalManager,
)
from server.app.agent.output import (
    OutputFormatter,
    DiffFormatter,
    SyntaxHighlighter,
)


class TestToolRegistry:
    """Test suite for ToolRegistry."""

    def test_registry_initialization(self):
        """Test creating a tool registry."""
        registry = ToolRegistry()
        assert registry._tools == {}
        assert len(registry._categories) == len(ToolCategory)

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = Tool(
            name="test_tool",
            category=ToolCategory.FILESYSTEM,
            description="A test tool",
            func=lambda: "result",
        )

        registry.register(tool)
        assert "test_tool" in registry._tools
        assert "test_tool" in registry._categories[ToolCategory.FILESYSTEM]

    def test_get_tool(self):
        """Test retrieving a tool."""
        registry = ToolRegistry()
        tool = Tool(
            name="gettable_tool",
            category=ToolCategory.GIT,
            description="A gettable tool",
            func=lambda: "got it",
        )
        registry.register(tool)

        retrieved = registry.get("gettable_tool")
        assert retrieved is not None
        assert retrieved.name == "gettable_tool"

        missing = registry.get("nonexistent")
        assert missing is None

    def test_list_tools(self):
        """Test listing tools."""
        registry = ToolRegistry()

        # Register tools in different categories
        registry.register(Tool("tool1", ToolCategory.GIT, "desc1", lambda: None))
        registry.register(Tool("tool2", ToolCategory.GIT, "desc2", lambda: None))
        registry.register(Tool("tool3", ToolCategory.TEST, "desc3", lambda: None))

        all_tools = registry.list_tools()
        assert len(all_tools) == 3

        git_tools = registry.list_tools(category=ToolCategory.GIT)
        assert len(git_tools) == 2

    def test_call_tool(self):
        """Test executing a tool."""
        registry = ToolRegistry()

        def sample_func(x, y):
            return x + y

        tool = Tool("adder", ToolCategory.FILESYSTEM, "Adds numbers", sample_func)
        registry.register(tool)

        result = registry.call("adder", x=5, y=3)
        assert result == 8

    def test_call_unknown_tool(self):
        """Test calling an unknown tool raises error."""
        registry = ToolRegistry()

        with pytest.raises(ValueError, match="Unknown tool"):
            registry.call("nonexistent")


class TestGitTools:
    """Test suite for GitTools."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        sandbox = Mock()
        return sandbox

    def test_git_status_clean(self, mock_sandbox):
        """Test git_status with clean repo."""
        mock_sandbox.execute.return_value = Mock(
            output="",
            exit_code=0,
        )

        git = GitTools(mock_sandbox)
        result = git.git_status()

        assert result["clean"] is True
        assert result["files"] == []

    def test_git_status_dirty(self, mock_sandbox):
        """Test git_status with changes."""
        mock_sandbox.execute.return_value = Mock(
            output=" M modified.py\nA  added.py\nD  deleted.py",
            exit_code=0,
        )

        git = GitTools(mock_sandbox)
        result = git.git_status()

        assert result["clean"] is False
        assert len(result["files"]) == 3
        assert result["files"][0]["status"] == "M"
        assert result["files"][0]["file"] == "modified.py"

    def test_git_diff(self, mock_sandbox):
        """Test git_diff."""
        mock_sandbox.execute.return_value = Mock(
            output="diff --git a/file.py b/file.py\n+added line",
            exit_code=0,
        )

        git = GitTools(mock_sandbox)
        result = git.git_diff()

        assert "diff --git" in result

    def test_git_log(self, mock_sandbox):
        """Test git_log."""
        mock_sandbox.execute.return_value = Mock(
            output="abc123|Initial commit|Author|2024-01-01",
            exit_code=0,
        )

        git = GitTools(mock_sandbox)
        result = git.git_log(n=1)

        assert len(result) == 1
        assert result[0]["hash"] == "abc123"
        assert result[0]["message"] == "Initial commit"

    def test_git_branch(self, mock_sandbox):
        """Test git_branch."""
        mock_sandbox.execute.return_value = Mock(
            output="* main\n  feature-branch",
            exit_code=0,
        )

        git = GitTools(mock_sandbox)
        result = git.git_branch()

        assert result["current"] == "main"
        assert len(result["branches"]) == 2


class TestSearchTools:
    """Test suite for SearchTools."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        return Mock()

    def test_grep(self, mock_sandbox):
        """Test grep functionality."""
        mock_sandbox.execute.return_value = Mock(
            output="file1.py:10:def hello():\nfile2.py:20:hello world",
            exit_code=0,
        )

        search = SearchTools(mock_sandbox)
        results = search.grep("hello", path=".")

        assert len(results) == 2
        assert results[0]["file"] == "file1.py"
        assert results[0]["line"] == 10

    def test_find_files(self, mock_sandbox):
        """Test find_files."""
        mock_sandbox.execute.return_value = Mock(
            output="./test1.py\n./test2.py\n./dir/test3.py",
            exit_code=0,
        )

        search = SearchTools(mock_sandbox)
        results = search.find_files("*.py")

        assert len(results) == 3
        assert "./test1.py" in results


class TestContextManager:
    """Test suite for ContextManager."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        sandbox = Mock()
        sandbox.root_dir = Path("/test/project")
        return sandbox

    def test_build_index(self, mock_sandbox):
        """Test building project index."""
        mock_sandbox.execute.side_effect = [
            # find command
            Mock(output="./file1.py\n./file2.js\n./README.md", exit_code=0),
            # stat commands
            Mock(output="100", exit_code=0),
            Mock(output="200", exit_code=0),
            Mock(output="50", exit_code=0),
        ]

        manager = ContextManager(mock_sandbox)
        index = manager.build_index()

        assert index.file_count == 3
        assert "./file1.py" in index.files
        assert index.files["./file1.py"].language == "python"

    def test_get_relevant_files(self, mock_sandbox):
        """Test getting relevant files for a query."""
        # Setup mock index
        manager = ContextManager(mock_sandbox)
        manager.index = ProjectIndex(root_path="/test")
        manager.index.add_file(
            FileInfo(
                path="./auth.py",
                size=100,
                language="python",
                importance_score=0.5,
            )
        )
        manager.index.add_file(
            FileInfo(
                path="./login.py",
                size=100,
                language="python",
                importance_score=0.5,
            )
        )

        relevant = manager.get_relevant_files("auth", limit=2)

        assert len(relevant) == 2
        # auth.py should be more relevant
        assert relevant[0].path == "./auth.py"

    def test_format_context_for_llm(self, mock_sandbox):
        """Test formatting context for LLM."""
        manager = ContextManager(mock_sandbox)
        manager.index = ProjectIndex(root_path="/test")
        manager.index.add_file(
            FileInfo(
                path="./app.py",
                size=100,
                language="python",
                importance_score=0.5,
            )
        )

        context = manager.format_context_for_llm("query")

        assert "Project has 1 files" in context
        assert "Languages: python" in context


class TestTaskPlanner:
    """Test suite for TaskPlanner."""

    def test_create_plan(self):
        """Test creating a plan."""
        planner = TaskPlanner()
        steps = [
            {"description": "Step 1", "tool": "read"},
            {"description": "Step 2", "tool": "write"},
        ]

        plan = planner.create_plan("Test plan", steps)

        assert plan.description == "Test plan"
        assert len(plan.tasks) == 2
        assert plan.tasks[0].description == "Step 1"
        assert plan.tasks[1].tool_name == "write"

    def test_estimate_complexity_high(self):
        """Test complexity estimation for high complexity task."""
        planner = TaskPlanner()
        result = planner.estimate_complexity("Refactor the entire architecture")

        assert result["level"] == "high"
        assert result["estimated_steps"] == 8

    def test_estimate_complexity_low(self):
        """Test complexity estimation for low complexity task."""
        planner = TaskPlanner()
        result = planner.estimate_complexity("Fix the typo")

        assert result["level"] == "low"
        assert result["estimated_steps"] == 2


class TestWorkflowOrchestrator:
    """Test suite for WorkflowOrchestrator."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock tool executor."""
        return Mock(return_value="success")

    @pytest.mark.asyncio
    async def test_execute_plan_success(self, mock_executor):
        """Test executing a simple plan."""
        orchestrator = WorkflowOrchestrator(mock_executor)
        planner = TaskPlanner()

        steps = [
            {"description": "Task 1", "tool": "read", "args": {"path": "file1"}},
            {"description": "Task 2", "tool": "write", "args": {"path": "file2"}},
        ]
        plan = planner.create_plan("Test", steps)

        completed_plan = await orchestrator.execute_plan(plan)

        assert completed_plan.tasks[0].status == TaskStatus.COMPLETED
        assert completed_plan.tasks[1].status == TaskStatus.COMPLETED
        assert mock_executor.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_plan_with_approval(self, mock_executor):
        """Test plan with approval required."""
        orchestrator = WorkflowOrchestrator(mock_executor)
        planner = TaskPlanner()

        steps = [
            {"description": "Safe task", "tool": "read"},
            {"description": "Dangerous task", "tool": "delete", "requires_approval": True},
        ]
        plan = planner.create_plan("Test", steps)

        completed_plan = await orchestrator.execute_plan(plan)

        # First task should complete
        assert completed_plan.tasks[0].status == TaskStatus.COMPLETED
        # Second task should be waiting for approval
        assert completed_plan.tasks[1].status == TaskStatus.WAITING_APPROVAL

    def test_get_plan_status(self, mock_executor):
        """Test getting plan status."""
        orchestrator = WorkflowOrchestrator(mock_executor)
        planner = TaskPlanner()

        plan = planner.create_plan(
            "Test",
            [
                {"description": "Task 1"},
                {"description": "Task 2"},
            ],
        )

        # Manually set status
        plan.tasks[0].status = TaskStatus.COMPLETED
        plan.tasks[1].status = TaskStatus.IN_PROGRESS
        orchestrator.active_plans[plan.id] = plan

        status = orchestrator.get_plan_status(plan.id)

        assert status["total_tasks"] == 2
        assert status["completed"] == 1
        assert status["in_progress"] == 1
        assert status["progress_percent"] == 50.0


class TestChangeTracker:
    """Test suite for ChangeTracker."""

    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox."""
        return Mock()

    def test_snapshot_file(self, mock_sandbox):
        """Test creating a file snapshot."""
        mock_sandbox.execute.return_value = Mock(
            output="file content",
            exit_code=0,
        )

        tracker = ChangeTracker(mock_sandbox)
        file_hash = tracker.snapshot_file("test.py")

        assert file_hash is not None
        assert "test.py" in tracker.snapshots
        assert tracker.snapshots["test.py"] == "file content"

    def test_record_change(self, mock_sandbox):
        """Test recording a change."""
        mock_sandbox.execute.return_value = Mock(
            output="new content",
            exit_code=0,
        )

        tracker = ChangeTracker(mock_sandbox)
        change = tracker.record_change("test.py", "modify", "old_hash")

        assert len(tracker.changes) == 1
        assert change.file_path == "test.py"
        assert change.change_type == "modify"

    def test_undo(self, mock_sandbox):
        """Test undo functionality."""
        mock_sandbox.execute.side_effect = [
            Mock(output="original", exit_code=0),  # snapshot
            Mock(output="new", exit_code=0),  # after change
            Mock(output="", exit_code=0),  # undo write
        ]

        tracker = ChangeTracker(mock_sandbox)
        tracker.snapshot_file("test.py")
        tracker.record_change("test.py", "modify")

        undone = tracker.undo()

        assert undone is not None
        assert undone.file_path == "test.py"
        assert len(tracker.changes) == 0
        assert len(tracker.redo_stack) == 1


class TestApprovalManager:
    """Test suite for ApprovalManager."""

    def test_request_approval(self):
        """Test requesting approval."""
        manager = ApprovalManager(timeout_seconds=60)

        approval_id = manager.request_approval(
            operation_type="delete",
            description="Delete file.py",
            details={"file": "file.py"},
        )

        assert approval_id is not None
        assert approval_id in manager.pending_approvals

        approval = manager.pending_approvals[approval_id]
        assert approval["operation_type"] == "delete"
        assert approval["status"] == "pending"

    def test_approve(self):
        """Test approving a request."""
        manager = ApprovalManager()
        approval_id = manager.request_approval("test", "Test operation")

        result = manager.approve(approval_id)

        assert result is True
        assert manager.pending_approvals[approval_id]["status"] == "approved"
        assert manager.is_approved(approval_id) is True

    def test_reject(self):
        """Test rejecting a request."""
        manager = ApprovalManager()
        approval_id = manager.request_approval("test", "Test operation")

        result = manager.reject(approval_id, "Too dangerous")

        assert result is True
        assert manager.pending_approvals[approval_id]["status"] == "rejected"
        assert manager.is_approved(approval_id) is False

    def test_cleanup_expired(self):
        """Test cleaning up expired approvals."""
        manager = ApprovalManager(timeout_seconds=0.001)  # Very short timeout
        approval_id = manager.request_approval("test", "Test")

        # Wait for timeout
        import time

        time.sleep(0.01)

        count = manager.cleanup_expired()

        assert count == 1
        assert manager.pending_approvals[approval_id]["status"] == "timeout"


class TestDiffFormatter:
    """Test suite for DiffFormatter."""

    def test_format_unified_diff(self):
        """Test formatting unified diff."""
        formatter = DiffFormatter()
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_modified
 line3"""

        result = formatter.format_unified_diff(diff)

        assert "📄" in result
        assert "✅" in result  # Added line
        assert "❌" in result  # Removed line

    def test_format_compact_diff(self):
        """Test compact diff formatting."""
        formatter = DiffFormatter()
        old = "line1\nline2\nline3"
        new = "line1\nline2_modified\nline3"

        result = formatter.format_compact_diff(old, new)

        assert "+line2_modified" in result or "-line2" in result

    def test_create_file_diff_summary(self):
        """Test creating diff summary."""
        formatter = DiffFormatter()
        diff = """+added1
+added2
-removed1"""

        result = formatter.create_file_diff_summary("test.py", diff)

        assert "test.py" in result
        assert "+2" in result  # 2 additions
        assert "-1" in result  # 1 deletion


class TestSyntaxHighlighter:
    """Test suite for SyntaxHighlighter."""

    def test_highlight_python(self):
        """Test Python syntax highlighting."""
        highlighter = SyntaxHighlighter()
        code = "def hello():\n    return 'world'"

        result = highlighter.highlight_python(code)

        assert "**def**" in result  # Keyword should be bolded

    def test_get_language_from_extension(self):
        """Test language detection from extension."""
        highlighter = SyntaxHighlighter()

        assert highlighter.get_language_from_extension("test.py") == "python"
        assert highlighter.get_language_from_extension("test.js") == "javascript"
        assert highlighter.get_language_from_extension("test.go") == "go"
        assert highlighter.get_language_from_extension("test.txt") is None


class TestOutputFormatter:
    """Test suite for OutputFormatter."""

    def test_format_tool_call(self):
        """Test formatting tool call output."""
        formatter = OutputFormatter()

        result = formatter.format_tool_call(
            tool_name="read_file",
            args={"path": "test.py"},
            result="content",
            duration_ms=100,
        )

        assert "🔧 read_file" in result
        assert "(100ms)" in result
        assert "test.py" in result

    def test_format_error(self):
        """Test formatting error message."""
        formatter = OutputFormatter()

        result = formatter.format_error("Something went wrong", "Reading file")

        assert "❌ Error" in result
        assert "Something went wrong" in result
        assert "Reading file" in result

    def test_format_success(self):
        """Test formatting success message."""
        formatter = OutputFormatter()

        result = formatter.format_success("Operation completed")

        assert "✅ Operation completed" == result

    def test_truncate_output(self):
        """Test output truncation."""
        formatter = OutputFormatter()
        long_output = "\n".join([f"line{i}" for i in range(100)])

        result = formatter.truncate_output(long_output, max_lines=10)

        assert "more lines" in result
        assert len(result.split("\n")) <= 12  # 10 lines + indicator + original


# Integration Tests


class TestPhase4Integration:
    """Integration tests for Phase 4 components."""

    def test_full_tool_workflow(self):
        """Test a complete workflow using Phase 4 tools."""
        # Create registry
        registry = ToolRegistry()

        # Register a mock tool
        def mock_tool(x):
            return x * 2

        registry.register(
            Tool(
                name="doubler",
                category=ToolCategory.FILESYSTEM,
                description="Doubles a number",
                func=mock_tool,
            )
        )

        # Execute
        result = registry.call("doubler", x=5)
        assert result == 10

    def test_plan_and_track_changes(self):
        """Test planning workflow with change tracking."""
        mock_sandbox = Mock()
        mock_sandbox.execute.return_value = Mock(output="", exit_code=0)

        # Create planner
        planner = TaskPlanner()
        plan = planner.create_plan(
            "Test",
            [
                {"description": "Task 1"},
                {"description": "Task 2"},
            ],
        )

        # Create change tracker
        tracker = ChangeTracker(mock_sandbox)

        # Simulate changes
        tracker.record_change("file1.py", "modify")
        tracker.record_change("file2.py", "modify")

        assert len(tracker.changes) == 2
        assert len(plan.tasks) == 2
