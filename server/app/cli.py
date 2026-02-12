"""Typer CLI for Cognition server.

Commands:
- serve: Start the API server
- init: Initialize configuration
- config: Show configuration
"""

from __future__ import annotations

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


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
