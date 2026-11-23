"""The desktop panel and the extension agree on the /workflow/eta/status contract.

Fails when the panel's field list, its supported schema, the extension's schema/version constants, or the
documented versions drift apart, so a change on one side cannot silently mis-render on the other."""
import importlib.util
import json
import re
import sys
import tempfile
import types
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_diagnostics import WorkflowETADiagnostics
from workflow_eta_tracker import (WorkflowETATracker, STATUS_SCHEMA, WORKFLOW_ETA_VERSION, WORKFLOW_ETA_THEMES,
                                  WORKFLOW_ETA_DEFAULT_THEME, WORKFLOW_ETA_PANEL_HTML)

spec = importlib.util.spec_from_file_location("panel_model", root / "Workflow_ETA_Panel" / "panel_model.py")
panel_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel_model)

graph = {
    "m": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "l": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "s": {"class_type": "KSampler", "inputs": {"model": ["m", 0], "latent_image": ["l", 0], "steps": 8}},
    "d": {"class_type": "VAEDecode", "inputs": {"samples": ["s", 0], "vae": ["m", 2]}},
}
with tempfile.TemporaryDirectory() as path:
    history = WorkflowETAHistory(path + "/h.sqlite")
    clock = [0.0]
    tracker = WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a: None), history,
                                 find_prompt_fn=lambda p: (graph, ["d"]), clock=lambda: clock[0],
                                 verbose=False, start_thread=False, device="gpu0")
    idle = tracker.status_snapshot()
    assert idle["state"] == "idle" and idle["schema"] == STATUS_SCHEMA and idle["version"] == WORKFLOW_ETA_VERSION, idle

    tracker._on_start({"prompt_id": "p"})
    tracker._on_cached({"prompt_id": "p", "nodes": ["m", "l"]})
    tracker._on_executing({"prompt_id": "p", "node": "s"})
    clock[0] = 10
    tracker._on_progress({"node": "s", "value": 1, "max": 8})
    running = tracker.status_snapshot()
    clock[0] = 90
    tracker._on_success({"prompt_id": "p"})
    done = tracker.status_snapshot()
    history.close()

assert running["state"] == "running" and done["state"] == "done"
for snapshot in (running, done):
    assert snapshot["schema"] == STATUS_SCHEMA and snapshot["version"] == WORKFLOW_ETA_VERSION

# Every field the panel reads must still exist in what the extension really sends (running or done).
sent = set(running) | set(done)
missing = [field for field in panel_model.PANEL_FIELDS if field not in sent]
assert not missing, f"panel reads fields the extension no longer sends: {missing}"

assert panel_model.SUPPORTED_SCHEMA == STATUS_SCHEMA, (
    f"panel supports schema {panel_model.SUPPORTED_SCHEMA} but the extension sends {STATUS_SCHEMA}: update both together")

# The extension version constant equals the README title; the panel version equals its own README title.
title = re.match(r"# Workflow ETA v(\S+)", (root / "README.md").read_text(encoding="utf-8"))
assert title and title.group(1) == WORKFLOW_ETA_VERSION, (title and title.group(1), WORKFLOW_ETA_VERSION)
panel_title = re.match(r"# Workflow ETA Panel v(\S+)", (root / "Workflow_ETA_Panel" / "README.md").read_text(encoding="utf-8"))
panel_source = (root / "Workflow_ETA_Panel" / "workflow_eta_panel.py").read_text(encoding="utf-8")
panel_version = re.search(r"^VERSION='([^']+)'", panel_source, re.M)
assert panel_title and panel_version and panel_title.group(1) == panel_version.group(1), (panel_title and panel_title.group(1), panel_version and panel_version.group(1))

# ---- the colour themes are one design in three places: node (JS), desktop panel, server (browser page + status)
script = (root / "js" / "workflow_eta.js").read_text(encoding="utf-8")
table = script[script.index("const WORKFLOW_ETA_THEMES = {"):]
table = table[:table.index("\n};")]
js_themes = {key: dict(re.findall(r'(\w+):\s*"([^"]*)"', body)) for key, body in re.findall(r'^\s{2,4}(\w+):\s*\{([^}]*)\}', table, re.M)}
assert list(js_themes) == list(WORKFLOW_ETA_THEMES) == list(panel_model.THEMES), (
    "the node, the server and the panel must offer the same themes in the same order",
    list(js_themes), list(WORKFLOW_ETA_THEMES), list(panel_model.THEMES))
for key, server in WORKFLOW_ETA_THEMES.items():
    assert js_themes[key] == server, f"node and server palettes differ for {key}: {js_themes[key]} != {server}"
    name, bg, card, text, muted, accent, warning, danger = panel_model.THEMES[key]
    expected = (server["name"], server["bg"], server["title"], server["strong"], server["muted"], server["accent"], server["warning"], server["danger"])
    assert (name, bg, card, text, muted, accent, warning, danger) == expected, f"panel palette differs for {key}"
assert WORKFLOW_ETA_DEFAULT_THEME == panel_model.DEFAULT_THEME == next(iter(WORKFLOW_ETA_THEMES))
assert f'? value : "{WORKFLOW_ETA_DEFAULT_THEME}"' in script, "the node falls back to the shared default theme"

# The browser page starts in the default theme before it follows the server's choice.
ocean = WORKFLOW_ETA_THEMES[WORKFLOW_ETA_DEFAULT_THEME]
root_vars = dict(re.findall(r"--(\w+):(#[0-9a-fA-F]{6})", WORKFLOW_ETA_PANEL_HTML.split("</style>")[0]))
assert root_vars == {key: ocean[key] for key in ("bg", "title", "text", "muted", "strong", "accent", "track")}, root_vars

# ---- diagnostics records are stamped with the real extension version (they once said 0.2.24 forever)
def first_record(diagnostics):
    run = types.SimpleNamespace(graph={}, outputs=None, reach=set(), model_map={}, node_cold={}, video_sources={}, step_plans={},
                                shape_plans={}, preds={}, watch_rows=[], prompt_id="p", start_ts=1.0, machine_start=None, resident0=None)
    diagnostics.start(run, "gpu0")
    return json.loads(run.diagnostic_file.read_text(encoding="utf-8").splitlines()[0])

with tempfile.TemporaryDirectory() as folder:
    record = first_record(WorkflowETADiagnostics(folder + "/stamped", extension_version=WORKFLOW_ETA_VERSION))
    assert record["kind"] == "run_start" and record["data"]["extension_version"] == WORKFLOW_ETA_VERSION, record["data"]["extension_version"]
    assert first_record(WorkflowETADiagnostics(folder + "/unstamped"))["data"]["extension_version"] == "unknown"

# ...and no source file may hard-code another version string: the one definition is WORKFLOW_ETA_VERSION.
literal = re.compile(r"""['"]\d+\.\d+\.\d+['"]""")
for source in sorted(root.glob("workflow_eta_*.py")):
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if literal.search(line) and not line.startswith("WORKFLOW_ETA_VERSION ="):
            raise AssertionError(f"{source.name}:{number} hard-codes a version string: {line.strip()}")

print("PASS: panel fields exist in the real status payload; schema/version constants and the three colour tables agree")
