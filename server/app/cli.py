"""Typer CLI for Cognition server.

Commands:
- serve: Start the API server
- init: Initialize configuration
- config: Show configuration
- db: Database migration commands
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from server.app.config_loader import (
    ConfigLoader,
    get_global_config_path,
    get_project_config_path,
    init_global_config,
    init_project_config,
)
from server.app.settings import get_settings

app = typer.Typer(
    name="cognition",
    help="Cognition AI coding assistant",
    no_args_is_help=True,
)

# Create db subcommand group
db_app = typer.Typer(
    name="db",
    help="Database migration commands",
)
app.add_typer(db_app)

# Create scaffold subcommand group for tools/middleware scaffolding
create_app = typer.Typer(
    name="create",
    help="Create tools, middleware, and other extensions",
)
app.add_typer(create_app)

# Create tools subcommand group for tool management
tools_app = typer.Typer(
    name="tools",
    help="Manage registered tools",
)
app.add_typer(tools_app)

console = Console()

# Project root directory for alembic commands
project_root = Path(__file__).parent.parent.parent


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", "-h", help="Host to bind to"),
    port: int | None = typer.Option(None, "--port", "-p", help="Port to bind to"),
    log_level: str | None = typer.Option(None, "--log-level", "-l", help="Log level"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development)"),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the Cognition API server.

    Starts the REST API server with SSE streaming support.
    """
    import uvicorn

    # Load settings to get defaults
    settings = get_settings()

    # Override with CLI arguments
    host = host or settings.host
    port = port or settings.port
    log_level = log_level or settings.log_level

    console.print("[bold green]Starting Cognition server...[/bold green]")
    console.print(f"Host: {host}")
    console.print(f"Port: {port}")
    console.print(f"Log Level: {log_level}")
    console.print(f"Docs: http://{host}:{port}/docs")
    console.print()

    uvicorn.run(
        "server.app.main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
    )


@app.command()
def init(
    global_config: bool = typer.Option(False, "--global", "-g", help="Initialize global config"),
    project: bool = typer.Option(False, "--project", "-p", help="Initialize project config"),
    path: Path | None = typer.Option(None, "--path", help="Path for project config"),
):
    """Initialize configuration files.

    Creates default configuration files.
    """
    if global_config:
        config_path = init_global_config()
        console.print(f"[bold green]Created global config:[/bold green] {config_path}")

    if project or (not global_config and not project):
        # Default to project init if neither specified
        project_path = path or Path.cwd()
        config_path = init_project_config(project_path)
        console.print(f"[bold green]Created project config:[/bold green] {config_path}")


@app.command()
def config(
    show_global: bool = typer.Option(False, "--global", "-g", help="Show global config path"),
    show_project: bool = typer.Option(False, "--project", "-p", help="Show project config path"),
    show_values: bool = typer.Option(True, "--values", "-v", help="Show config values"),
):
    """Show configuration.

    Displays current configuration from all sources.
    """
    if show_global:
        path = get_global_config_path()
        console.print(f"Global config: {path}")
        if path.exists():
            console.print("[green]Exists[/green]")
        else:
            console.print("[yellow]Not created yet[/yellow]")
        return

    if show_project:
        path = get_project_config_path()
        if path:
            console.print(f"Project config: {path}")
            console.print("[green]Exists[/green]")
        else:
            console.print("Project config: [yellow]Not found[/yellow]")
        return

    if show_values:
        # Show merged configuration
        loader = ConfigLoader()
        config = loader.load()

        table = Table(title="Cognition Configuration")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")

        def add_config(prefix: str, data: dict):
            for key, value in data.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    add_config(full_key, value)
                else:
                    table.add_row(full_key, str(value))

        add_config("", config)
        console.print(table)

        # Show config sources
        console.print()
        console.print("[bold]Configuration Sources:[/bold]")

        global_path = get_global_config_path()
        if global_path.exists():
            console.print(f"  [green]✓[/green] Global: {global_path}")
        else:
            console.print(f"  [yellow]○[/yellow] Global: {global_path} (not created)")

        project_path = get_project_config_path()
        if project_path:
            console.print(f"  [green]✓[/green] Project: {project_path}")
        else:
            console.print("  [yellow]○[/yellow] Project: Not found in current directory")


@app.command()
def health(
    host: str | None = typer.Option(None, "--host", "-h", help="Server host"),
    port: int | None = typer.Option(None, "--port", "-p", help="Server port"),
):
    """Check server health.

    Makes a request to the server's health endpoint.
    """
    import httpx

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    url = f"http://{host}:{port}/health"

    try:
        response = httpx.get(url, timeout=5.0)
        data = response.json()

        if response.status_code == 200:
            console.print("[bold green]✓ Server is healthy[/bold green]")
            console.print(f"Version: {data.get('version', 'unknown')}")
            console.print(f"Active Sessions: {data.get('active_sessions', 0)}")
        else:
            console.print("[bold red]✗ Server unhealthy[/bold red]")
            console.print(f"Status: {response.status_code}")
    except Exception as e:
        console.print("[bold red]✗ Cannot connect to server[/bold red]")
        console.print(f"Error: {e}")


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Option(
        "head", "--revision", "-r", help="Target revision (default: head)"
    ),
    sql: bool = typer.Option(False, "--sql", help="Print SQL instead of executing"),
):
    """Upgrade database to the specified revision.

    Applies all pending migrations to bring the database schema
    up to the specified revision (default: latest).

    Examples:
        cognition db upgrade              # Upgrade to latest
        cognition db upgrade --revision 001  # Upgrade to specific revision
    """

    # Ensure we're using the correct working directory
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    cmd = ["alembic", "-c", str(alembic_ini), "upgrade", revision]

    if sql:
        cmd.append("--sql")

    console.print(f"[bold blue]Upgrading database to {revision}...[/bold blue]")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        if sql:
            console.print(result.stdout)
        else:
            console.print("[bold green]✓ Database upgraded successfully[/bold green]")
            if result.stdout:
                console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Database upgrade failed[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@db_app.command("downgrade")
def db_downgrade(
    revision: str = typer.Argument(..., help="Target revision (e.g., -1, base, or revision ID)"),
    sql: bool = typer.Option(False, "--sql", help="Print SQL instead of executing"),
):
    """Downgrade database to the specified revision.

    Reverts migrations to bring the database schema back
    to the specified revision.

    Examples:
        cognition db downgrade -1         # Downgrade one revision
        cognition db downgrade base       # Downgrade to base (empty)
        cognition db downgrade 001        # Downgrade to specific revision
    """
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    cmd = ["alembic", "-c", str(alembic_ini), "downgrade", revision]

    if sql:
        cmd.append("--sql")

    console.print(f"[bold yellow]Downgrading database to {revision}...[/bold yellow]")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        if sql:
            console.print(result.stdout)
        else:
            console.print("[bold green]✓ Database downgraded successfully[/bold green]")
            if result.stdout:
                console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Database downgrade failed[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@db_app.command("migrate")
def db_migrate(
    message: str = typer.Argument(..., help="Migration message/description"),
    autogenerate: bool = typer.Option(
        False, "--autogenerate", "-a", help="Auto-generate migration from models"
    ),
):
    """Create a new database migration.

    Creates a new migration script with the given message.
    Use --autogenerate to automatically detect schema changes.

    Examples:
        cognition db migrate "add user table"
        cognition db migrate --autogenerate "auto migration"
    """
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    cmd = ["alembic", "-c", str(alembic_ini), "revision", "-m", message]

    if autogenerate:
        cmd.append("--autogenerate")

    console.print(f"[bold blue]Creating migration: {message}...[/bold blue]")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        console.print("[bold green]✓ Migration created successfully[/bold green]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Migration creation failed[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@db_app.command("current")
def db_current(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show verbose output"),
):
    """Show current database revision."""
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    cmd = ["alembic", "-c", str(alembic_ini), "current"]

    if verbose:
        cmd.append("-v")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        console.print("[bold blue]Current database revision:[/bold blue]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Failed to get current revision[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@db_app.command("history")
def db_history(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show verbose output"),
    indicate_current: bool = typer.Option(
        False, "--current", "-c", help="Indicate current revision"
    ),
):
    """Show migration history."""
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    cmd = ["alembic", "-c", str(alembic_ini), "history"]

    if verbose:
        cmd.append("-v")

    if indicate_current:
        cmd.append("--indicate-current")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        console.print("[bold blue]Migration history:[/bold blue]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Failed to get migration history[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@db_app.command("init")
def db_init():
    """Initialize database (run all migrations).

    This is an alias for 'db upgrade head' that creates
    all necessary tables for a fresh installation.
    """
    alembic_ini = Path(__file__).parent.parent / "alembic.ini"

    if not alembic_ini.exists():
        console.print(f"[bold red]Error:[/bold red] alembic.ini not found at {alembic_ini}")
        raise typer.Exit(1)

    settings = get_settings()
    console.print("[bold blue]Initializing database...[/bold blue]")
    console.print(f"Backend: {settings.persistence_backend}")
    console.print(f"URI: {settings.persistence_uri}")

    cmd = ["alembic", "-c", str(alembic_ini), "upgrade", "head"]

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )

        console.print("[bold green]✓ Database initialized successfully[/bold green]")
        if result.stdout:
            console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]✗ Database initialization failed[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


@create_app.command("tool")
def create_tool(
    name: str = typer.Argument(..., help="Name of the tool to create"),
    path: Path | None = typer.Option(
        None, "--path", "-p", help="Target directory (default: .cognition/tools/)"
    ),
    description: str | None = typer.Option(None, "--description", "-d", help="Tool description"),
):
    """Create a new tool template.

    Generates a Python file with a properly structured @tool decorator
    and example implementation. The tool can be automatically discovered
    by the AgentRegistry when placed in .cognition/tools/.

    Examples:
        cognition create tool file_reader
        cognition create tool calculator --description "Perform calculations"
        cognition create tool custom_tool --path ./my_tools/
    """
    # Determine target directory
    if path is None:
        path = Path.cwd() / ".cognition" / "tools"
    else:
        path = Path(path).resolve()

    # Ensure directory exists
    path.mkdir(parents=True, exist_ok=True)

    # Sanitize tool name
    tool_name = name.lower().replace("-", "_").replace(" ", "_")

    # Validate tool name is a valid Python identifier
    if not tool_name.isidentifier():
        console.print(f"[bold red]Error:[/bold red] '{name}' is not a valid Python identifier.")
        console.print(
            "[dim]Tool names must start with a letter or underscore, and contain only letters, numbers, and underscores.[/dim]"
        )
        raise typer.Exit(1)

    file_path = path / f"{tool_name}.py"

    # Check if file already exists
    if file_path.exists():
        console.print(f"[bold red]Error:[/bold red] Tool file already exists: {file_path}")
        raise typer.Exit(1)

    # Generate tool template
    desc = description or f"{name.replace('_', ' ').title()} tool"
    template = f'''"""{desc}.

This tool was generated by Cognition CLI.
Place this file in .cognition/tools/ to enable automatic discovery.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool


@tool
def {tool_name}(query: str) -> str:
    """{desc}.

    Args:
        query: The input query or parameter

    Returns:
        Result of the tool execution
    """
    # TODO: Implement your tool logic here
    return f"Result for: {{query}}"


# Optional: Define additional tools in the same file
@tool
def {tool_name}_advanced(
    param1: str,
    param2: Optional[int] = None,
) -> dict:
    """Advanced version of {name} with more parameters.

    Args:
        param1: First parameter
        param2: Optional second parameter

    Returns:
        Dictionary with results
    """
    return {{
        "tool": "{tool_name}",
        "param1": param1,
        "param2": param2,
        "status": "success",
    }}
'''

    # Write the file
    file_path.write_text(template)

    console.print(f"[bold green]✓ Created tool:[/bold green] {file_path}")
    console.print(f"[dim]Description: {desc}[/dim]")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Edit {file_path} to implement your tool logic")
    console.print("  2. The tool will be auto-discovered from .cognition/tools/")


@create_app.command("middleware")
def create_middleware(
    name: str = typer.Argument(..., help="Name of the middleware to create"),
    path: Path | None = typer.Option(
        None, "--path", "-p", help="Target directory (default: .cognition/middleware/)"
    ),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Middleware description"
    ),
):
    """Create a new middleware template.

    Generates a Python file with a properly structured AgentMiddleware
    class and example implementation. The middleware can be automatically
    discovered by the AgentRegistry when placed in .cognition/middleware/.

    Examples:
        cognition create middleware logging
        cognition create middleware auth --description "Authentication middleware"
        cognition create middleware custom --path ./my_middleware/
    """
    # Determine target directory
    if path is None:
        path = Path.cwd() / ".cognition" / "middleware"
    else:
        path = Path(path).resolve()

    # Ensure directory exists
    path.mkdir(parents=True, exist_ok=True)

    # Sanitize middleware name
    middleware_name = name.lower().replace("-", "_").replace(" ", "_")
    class_name = "".join(word.capitalize() for word in middleware_name.split("_")) + "Middleware"
    file_path = path / f"{middleware_name}.py"

    # Check if file already exists
    if file_path.exists():
        console.print(f"[bold red]Error:[/bold red] Middleware file already exists: {file_path}")
        raise typer.Exit(1)

    # Generate middleware template
    desc = description or f"{name.replace('_', ' ').title()} middleware"
    template = f'''"""{desc}.

This middleware was generated by Cognition CLI.
Place this file in .cognition/middleware/ to enable automatic discovery.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain.agents.middleware.types import AgentMiddleware


class {class_name}(AgentMiddleware):
    """{desc}.

    This middleware intercepts agent execution at various lifecycle points.
    Implement the hooks you need and omit the rest for better performance.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the middleware.

        Args:
            **kwargs: Configuration options for the middleware
        """
        super().__init__()
        # TODO: Initialize your middleware state here
        self.config = kwargs

    def on_start(self, state: dict[str, Any]) -> dict[str, Any]:
        """Called when the agent starts processing.

        Args:
            state: The current agent state

        Returns:
            Modified state (or original if no changes needed)
        """
        # TODO: Implement start hook
        return state

    def on_end(self, state: dict[str, Any]) -> dict[str, Any]:
        """Called when the agent finishes processing.

        Args:
            state: The final agent state

        Returns:
            Modified state (or original if no changes needed)
        """
        # TODO: Implement end hook
        return state

    def on_tool_start(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Called before a tool is executed.

        Args:
            tool_name: Name of the tool being called
            tool_input: Input parameters for the tool

        Returns:
            Tuple of (possibly modified tool_name, possibly modified tool_input)
        """
        # TODO: Implement tool start hook
        return tool_name, tool_input

    def on_tool_end(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
    ) -> Any:
        """Called after a tool is executed.

        Args:
            tool_name: Name of the tool that was called
            tool_input: Input parameters that were used
            tool_output: Output from the tool execution

        Returns:
            Possibly modified tool output
        """
        # TODO: Implement tool end hook
        return tool_output

    def on_error(self, error: Exception, state: dict[str, Any]) -> dict[str, Any]:
        """Called when an error occurs during agent execution.

        Args:
            error: The exception that was raised
            state: The current agent state

        Returns:
            Modified state (or original if no changes needed)
        """
        # TODO: Implement error handling
        return state
'''

    # Write the file
    file_path.write_text(template)

    console.print(f"[bold green]✓ Created middleware:[/bold green] {file_path}")
    console.print(f"[dim]Description: {desc}[/dim]")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Edit {file_path} to implement your middleware logic")
    console.print("  2. The middleware will be auto-discovered from .cognition/middleware/")
    console.print("  3. Use AgentRegistry to register the middleware for sessions")
    console.print()
    console.print("Note: Middleware changes apply to new sessions only (session-based reload)")


@tools_app.command("list")
def tools_list(
    host: str | None = typer.Option(None, "--host", "-h", help="Server host"),
    port: int | None = typer.Option(None, "--port", "-p", help="Server port"),
):
    """List all registered tools.

    Queries the server for registered tools and displays them in a table.
    Also shows any load errors if present. Exits with code 1 if errors exist.

    Examples:
        cognition tools list
        cognition tools list --host localhost --port 8080
    """
    import httpx

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    base_url = f"http://{host}:{port}"

    try:
        # Get tools list
        response = httpx.get(f"{base_url}/tools", timeout=10.0)
        response.raise_for_status()
        data = response.json()

        tools = data.get("tools", [])

        if not tools:
            console.print("[yellow]No tools registered[/yellow]")
        else:
            table = Table(title="Registered Tools")
            table.add_column("Name", style="cyan", no_wrap=True)
            table.add_column("Source", style="green")

            for tool in tools:
                source = tool.get("source", "unknown")
                # Truncate long paths
                if len(source) > 50:
                    source = "..." + source[-47:]
                table.add_row(tool.get("name", "unknown"), source)

            console.print(table)
            console.print(f"[dim]Total: {len(tools)} tool(s)[/dim]")

        # Get errors
        errors_response = httpx.get(f"{base_url}/tools/errors", timeout=10.0)
        errors_response.raise_for_status()
        errors = errors_response.json()

        if errors:
            console.print()
            console.print("[bold red]Load Errors:[/bold red]")
            for error in errors:
                console.print(f"  [red]✗[/red] {error.get('file', 'unknown')}")
                console.print(f"    [dim]{error.get('error', 'unknown error')}[/dim]")
            raise typer.Exit(1)

    except httpx.ConnectError:
        console.print("[bold red]Error:[/bold red] Cannot connect to server")
        console.print(f"[dim]Is the server running at {base_url}?[/dim]")
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]Error:[/bold red] Server returned {e.response.status_code}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@tools_app.command("reload")
def tools_reload(
    host: str | None = typer.Option(None, "--host", "-h", help="Server host"),
    port: int | None = typer.Option(None, "--port", "-p", help="Server port"),
):
    """Trigger a reload of tools from the discovery path.

    Sends a request to the server to reload all tools from .cognition/tools/.
    Displays the count of tools loaded and any errors encountered.

    Examples:
        cognition tools reload
        cognition tools reload --host localhost --port 8080
    """
    import httpx

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port

    base_url = f"http://{host}:{port}"

    try:
        console.print("[bold blue]Reloading tools...[/bold blue]")
        response = httpx.post(f"{base_url}/tools/reload", timeout=30.0)
        response.raise_for_status()
        result = response.json()

        count = result.get("count", 0)
        errors = result.get("errors", [])

        console.print(f"[bold green]✓ Reloaded {count} tool(s)[/bold green]")

        if errors:
            console.print()
            console.print("[bold red]Errors during reload:[/bold red]")
            for error in errors:
                console.print(f"  [red]✗[/red] {error.get('file', 'unknown')}")
                console.print(f"    [dim]{error.get('error', 'unknown error')}[/dim]")
            raise typer.Exit(1)

    except httpx.ConnectError:
        console.print("[bold red]Error:[/bold red] Cannot connect to server")
        console.print(f"[dim]Is the server running at {base_url}?[/dim]")
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]Error:[/bold red] Server returned {e.response.status_code}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
