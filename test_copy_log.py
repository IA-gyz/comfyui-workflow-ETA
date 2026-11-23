"""The 'Copy to comfyui.log' settings toggle flips console printing at runtime.

The tracker buffer always receives entries; only the print to the ComfyUI log is
gated by the flag (WORKFLOW_ETA_VERBOSE at startup, POST /workflow/eta/copy_log at
runtime). Status snapshots and workflow.eta pushes expose the current value.
"""
import contextlib
import inspect
import io
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker, install_workflow_eta

with tempfile.TemporaryDirectory() as path:
    history = WorkflowETAHistory(path + "/h.sqlite")
    sent = []
    server = types.SimpleNamespace(send_sync=lambda *args: sent.append(args))
    graph = {"1": {"class_type": "Example", "inputs": {}}}
    tracker = WorkflowETATracker(server, history, find_prompt_fn=lambda pid: (graph, ["1"]),
                                clock=lambda: 100.0, wall=lambda: 100.0, verbose=False, start_thread=False)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        tracker._log("quiet entry")
        assert tracker.status_snapshot()["copy_to_log"] is False
        tracker.verbose = True  # what POST /workflow/eta/copy_log does
        tracker._log("loud entry")
        assert tracker.status_snapshot()["copy_to_log"] is True
    assert "quiet entry" not in out.getvalue(), out.getvalue()
    assert "[workflow-eta] loud entry" in out.getvalue(), out.getvalue()
    assert any(e["message"] == "quiet entry" for e in tracker.status_snapshot()["logs"]), "buffer keeps quiet entries"

    tracker._on_start({"prompt_id": "p"})
    tracker._on_cached({"prompt_id": "p", "nodes": []})
    assert sent[-1][1]["copy_to_log"] is True, "push payload carries the flag"
    tracker.verbose = False
    tracker._push(tracker.run, force=True)
    assert sent[-1][1]["copy_to_log"] is False, "toggling off reaches later pushes"
    history.close()

src = inspect.getsource(install_workflow_eta)
assert 'routes.post("/workflow/eta/copy_log")' in src
assert '"enabled"' in src and 'tracker.set_copy_to_log(body["enabled"])' in src  # persisted: see test_copy_log_persistence.py
assert 'copy_to_log=self.verbose' in inspect.getsource(WorkflowETATracker.status_snapshot)
assert '"copy_to_log": self.verbose' in inspect.getsource(WorkflowETATracker._push)

print("PASS: 'Copy to comfyui.log' gates console printing at runtime; buffer, status, pushes and the POST route expose it")
