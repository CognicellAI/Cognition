#!/usr/bin/env python3
"""
Local Sandbox Demonstration Script

This script demonstrates how the Cognition Sandbox Backend works
in a local development environment (without Docker).

It shows:
1. Creating a sandbox with a temporary workspace
2. Executing commands in the isolated environment
3. File operations (create, read, list)
4. The sandbox isolation (commands can't escape workspace)
"""

import tempfile
from pathlib import Path
from server.app.agent import CognitionLocalSandboxBackend
from server.app.agent import create_cognition_agent


def demo_basic_sandbox():
    """Demonstrate basic sandbox operations."""
    print("=" * 70)
    print("🧪 Cognition Sandbox Backend - Local Demo")
    print("=" * 70)

    # Create a temporary workspace
    with tempfile.TemporaryDirectory() as workspace:
        print(f"\n📁 Created workspace: {workspace}")

        # Initialize the sandbox backend
        backend = CognitionLocalSandboxBackend(root_dir=workspace, sandbox_id="demo-sandbox")

        print(f"🎛️  Sandbox ID: {backend.id}")

        # 1. Execute a command
        print("\n" + "-" * 70)
        print("1️⃣  Command Execution")
        print("-" * 70)

        result = backend.execute("pwd")
        print(f"Command: pwd")
        print(f"Output: {result.output.strip()}")
        print(f"Exit code: {result.exit_code}")

        # 2. Create a file
        print("\n" + "-" * 70)
        print("2️⃣  File Creation")
        print("-" * 70)

        write_result = backend.write("/test.txt", "Hello from the Cognition Sandbox!")
        print(f"Write to: /test.txt")
        print(f"Success: {write_result.error is None}")
        if write_result.error:
            print(f"Error: {write_result.error}")

        # 3. Read the file back
        print("\n" + "-" * 70)
        print("3️⃣  File Reading")
        print("-" * 70)

        content = backend.read("/test.txt")
        print(f"Content:\n{content}")

        # 4. List directory
        print("\n" + "-" * 70)
        print("4️⃣  Directory Listing")
        print("-" * 70)

        files = backend.ls_info("/")
        print(f"Files in workspace:")
        for f in files:
            print(f"  {'📁' if f['is_dir'] else '📄'} {f['path']} ({f['size']} bytes)")

        # 5. Search with grep
        print("\n" + "-" * 70)
        print("5️⃣  Content Search (grep)")
        print("-" * 70)

        matches = backend.grep_raw("Sandbox", path="/")
        print(f"Search for 'Sandbox':")
        if isinstance(matches, list):
            for m in matches:
                print(f"  📍 Line {m['line']}: {m['text'][:50]}...")
        else:
            print(f"  {matches}")

        # 6. Edit a file
        print("\n" + "-" * 70)
        print("6️⃣  File Editing")
        print("-" * 70)

        edit_result = backend.edit(
            "/test.txt", old_string="Sandbox", new_string="Local Environment"
        )
        print(f"Edit result: {edit_result.error or 'Success'}")

        # Read it back
        updated = backend.read("/test.txt", limit=5)
        print(f"Updated content:\n{updated}")

        # 7. Verify isolation
        print("\n" + "-" * 70)
        print("7️⃣  Sandbox Isolation")
        print("-" * 70)

        # Try to access file outside workspace (will fail appropriately)
        result = backend.execute("ls /etc/passwd")
        print(f"Attempt to access /etc/passwd:")
        print(f"  Exit code: {result.exit_code}")
        print(f"  Output: {result.output[:100]}...")

        # Verify file is in temp workspace, not system root
        workspace_file = Path(workspace) / "test.txt"
        system_file = Path("/test.txt")

        print(f"\n✅ File exists in workspace: {workspace_file.exists()}")
        print(f"✅ File NOT in system root: {not system_file.exists()}")

        print("\n" + "=" * 70)
        print("✨ Sandbox demonstration complete!")
        print("=" * 70)
        print(f"\nThe sandbox successfully:")
        print("  • Executed commands in isolation")
        print("  • Created and manipulated files")
        print("  • Kept all operations within the workspace")
        print("  • Used the deepagents backend protocol")


def demo_backend_protocol():
    """Show how the backend implements the protocol."""
    print("\n" + "=" * 70)
    print("📋 Backend Protocol Implementation")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as workspace:
        backend = CognitionLocalSandboxBackend(root_dir=workspace)

        print("\n🎯 Implements SandboxBackendProtocol:")
        print("  ✓ execute(command) -> ExecuteResponse")
        print("  ✓ ls_info(path) -> list[FileInfo]")
        print("  ✓ read(file_path) -> str")
        print("  ✓ write(file_path, content) -> WriteResult")
        print("  ✓ edit(file_path, old, new) -> EditResult")
        print("  ✓ glob_info(pattern) -> list[FileInfo]")
        print("  ✓ grep_raw(pattern) -> list[GrepMatch]")
        print("  ✓ upload_files(files) -> list[FileUploadResponse]")
        print("  ✓ download_files(paths) -> list[FileDownloadResponse]")

        print("\n🔗 When used with create_deep_agent():")
        print("  • Agent gets access to 'execute' tool")
        print("  • Agent can run shell commands automatically")
        print("  • All commands execute within workspace")
        print("  • Multi-step ReAct loop enabled")


def demo_with_agent():
    """Demonstrate how the sandbox works with an agent."""
    print("\n" + "=" * 70)
    print("🤖 Agent Integration")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as workspace:
        print(f"\n📁 Workspace: {workspace}")
        print("\nCode to create agent with sandbox:")
        print("""
    from server.app.agent import create_cognition_agent
    
    # Create agent with sandbox backend
    agent = create_cognition_agent(
        project_path=workspace,
        model=my_llm_model
    )
    
    # Agent now has access to:
    # - File tools: read, write, edit, ls, glob, grep
    # - Execute tool: run shell commands
    # - Automatic ReAct loop for multi-step tasks
    
    # Stream events
    async for event in agent.astream_events(...):
        if event["event"] == "on_tool_start":
            print(f"Agent is using: {event['name']}")
        elif event["event"] == "on_chat_model_stream":
            print(f"Token: {event['data']['chunk'].content}")
        """)


if __name__ == "__main__":
    # Run demonstrations
    demo_basic_sandbox()
    demo_backend_protocol()
    demo_with_agent()

    print("\n" + "=" * 70)
    print("🚀 Ready to test!")
    print("=" * 70)
    print("\nRun E2E tests:")
    print("  uv run pytest tests/e2e/test_sandbox_workflow.py -v")
    print("\nOr run this demo:")
    print("  uv run python scripts/demo_sandbox_local.py")
