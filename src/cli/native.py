"""Public entry point for the single native Logchat pipeline."""
from pathlib import Path
import typer
from logchat.local.cli import app as local_app, settings_app, start_command
from logchat.local.installer import install_command

app = typer.Typer(help="Local semantic log memory: capture, summarize, retrieve context.", no_args_is_help=True)
app.add_typer(local_app, name="local")
app.add_typer(settings_app, name="settings")
app.command("install")(install_command)

@app.command("start")
def start(port: int = 8765, state_dir: Path | None = None):
    """Start the native service in the background."""
    start_command(port, state_dir, False)

if __name__ == "__main__":
    app()
