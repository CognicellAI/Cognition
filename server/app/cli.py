"""Typer CLI for Cognition server.

Commands:
- serve: Start the API server
- init: Initialize configuration
- config: Show configuration
- db: Database migration commands
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from server.app.config_loader import (
    ConfigLoader,
    get_global_config_path,
    get_project_config_path,
    init_global_config,
    init_project_config,
    load_config,
)
from server.app.settings import Settings, get_settings

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

console = Console()


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Host to bind to"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port to bind to"),
    log_level: Optional[str] = typer.Option(None, "--log-level", "-l", help="Log level"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development)"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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

    console.print(f"[bold green]Starting Cognition server...[/bold green]")
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
    path: Optional[Path] = typer.Option(None, "--path", help="Path for project config"),
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
            console.print(f"[green]Exists[/green]")
        else:
            console.print(f"[yellow]Not created yet[/yellow]")
        return

    if show_project:
        path = get_project_config_path()
        if path:
            console.print(f"Project config: {path}")
            console.print(f"[green]Exists[/green]")
        else:
            console.print(f"Project config: [yellow]Not found[/yellow]")
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
            console.print(f"  [yellow]○[/yellow] Project: Not found in current directory")


@app.command()
def health(
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Server host"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Server port"),
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
            console.print(f"[bold green]✓ Server is healthy[/bold green]")
            console.print(f"Version: {data.get('version', 'unknown')}")
            console.print(f"Active Sessions: {data.get('active_sessions', 0)}")
        else:
            console.print(f"[bold red]✗ Server unhealthy[/bold red]")
            console.print(f"Status: {response.status_code}")
    except Exception as e:
        console.print(f"[bold red]✗ Cannot connect to server[/bold red]")
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
    import os

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
            console.print(f"[bold green]✓ Database upgraded successfully[/bold green]")
            if result.stdout:
                console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Database upgrade failed[/bold red]")
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
            console.print(f"[bold green]✓ Database downgraded successfully[/bold green]")
            if result.stdout:
                console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Database downgrade failed[/bold red]")
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

        console.print(f"[bold green]✓ Migration created successfully[/bold green]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Migration creation failed[/bold red]")
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

        console.print(f"[bold blue]Current database revision:[/bold blue]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Failed to get current revision[/bold red]")
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

        console.print(f"[bold blue]Migration history:[/bold blue]")
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Failed to get migration history[/bold red]")
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
    console.print(f"[bold blue]Initializing database...[/bold blue]")
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

        console.print(f"[bold green]✓ Database initialized successfully[/bold green]")
        if result.stdout:
            console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Database initialization failed[/bold red]")
        console.print(f"Error: {e.stderr}")
        raise typer.Exit(1)


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
