"""The desktop-panel launcher prefers the installed desktop entry and reports when the panel is missing."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from workflow_eta_tracker import _workflow_eta_panel_command

with tempfile.TemporaryDirectory() as root:
    command, error = _workflow_eta_panel_command(root)
    assert command is None and "not installed" in error, (command, error)

    script = Path(root) / "workflow-eta-panel" / "workflow_eta_panel.py"
    script.parent.mkdir(parents=True)
    script.write_text("# panel\n")
    with patch("shutil.which", return_value=None):
        command, error = _workflow_eta_panel_command(root)
    assert command == [sys.executable, str(script)] and error is None, (command, error)

    desktop = Path(root) / "applications" / "workflow-eta-panel.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\n")
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/gio" if name == "gio" else None):
        command, error = _workflow_eta_panel_command(root)
    assert command == ["/usr/bin/gio", "launch", str(desktop)] and error is None, (command, error)
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/kioclient6" if name == "kioclient6" else None):
        command, error = _workflow_eta_panel_command(root)
    assert command == ["/usr/bin/kioclient6", "exec", str(desktop)] and error is None, (command, error)

print("PASS: panel launcher prefers gio/kioclient desktop entry, falls back to the installed script, reports when not installed")
