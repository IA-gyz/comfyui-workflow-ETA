"""The standalone browser panel page is served at /workflow/eta/panel and bootstraps from status + ws."""
import inspect, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_eta_tracker as t

html = t.WORKFLOW_ETA_PANEL_HTML
assert "<title>Workflow ETA</title>" in html
assert 'fetch("/workflow/eta/status")' in html
assert '"/ws"' in html and "workflow.eta" in html
assert "text/html" in inspect.getsource(t.install_workflow_eta)
src = inspect.getsource(t.install_workflow_eta)
assert 'routes.get("/workflow/eta/panel")' in src

print("PASS: /workflow/eta/panel serves a standalone page titled 'Workflow ETA' fed by status + websocket")
