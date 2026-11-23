"""The watched data (the node inspector's rows) is most of every payload but rarely changes, so pushes carry only what changed.

The property that matters: merging every pushed update the way the node does reproduces the complete data after every push."""
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.modules.setdefault("nodes", types.SimpleNamespace(NODE_CLASS_MAPPINGS={}))  # like ComfyUI: already imported
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from workflow_eta_history import WorkflowETAHistory
import workflow_eta_tracker as t

spec = importlib.util.spec_from_file_location("panel_model", root / "Workflow_ETA_Panel" / "panel_model.py")
panel_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel_model)


def make_graph(fillers=6, samplers=2, steps=20):
    graph = {"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
             "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": "a photo", "clip": ["ckpt", 1]}},
             "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["ckpt", 1]}},
             "lat": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}}}
    order, previous = ["ckpt", "pos", "neg", "lat"], "lat"
    for i in range(samplers):
        graph[f"s{i}"] = {"class_type": "KSampler", "inputs": {"model": ["ckpt", 0], "positive": ["pos", 0], "negative": ["neg", 0],
                          "latent_image": [previous, 0], "seed": i, "steps": steps, "cfg": 7, "sampler_name": "euler",
                          "scheduler": "normal", "denoise": 1}}
        order.append(f"s{i}")
        previous = f"s{i}"
    for i in range(fillers):
        graph[f"f{i}"] = {"class_type": "ImageScale", "inputs": {"samples": [previous, 0], "width": 1024, "height": 1024,
                          "upscale_method": "bilinear", "crop": "disabled"}}
        order.append(f"f{i}")
        previous = f"f{i}"
    graph["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": [previous, 0], "vae": ["ckpt", 2]}}
    graph["save"] = {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "out"}}
    return graph, order + ["dec", "save"]


def merge(state, watched):
    """What the node does with a pushed `watched` object."""
    plain = {k: v for k, v in watched.items() if k != "full"}
    if watched.get("full") is not False:
        return plain
    assert state is not None and state["prompt_id"] == watched["prompt_id"], "a partial update arrived with no base"
    return dict(state, **plain)


def normal(value):
    return json.loads(json.dumps(value))


with tempfile.TemporaryDirectory() as folder:
    graph, order = make_graph()
    now = [1000.0]
    pushes, checks, rebuilds, counting = [], [], [0], [True]
    state = {"watched": None}

    def send_sync(event, data, *args, **kwargs):
        if event != t.PUSH_EVENT:
            return
        watched = data["watched"]
        pushes.append(dict(data, watched=normal(watched)))
        state["watched"] = merge(state["watched"], normal(watched))
        run = tracker.run
        if run is not None:  # equivalence after EVERY push: merged == the complete current data
            counting[0] = False  # this check builds the complete data itself; do not count it as the tracker's work
            try:
                assert normal(tracker._watch_snapshot(run)) == state["watched"], ("merged watched data diverged", len(pushes), watched.get("full"))
            finally:
                counting[0] = True
            checks.append(1)

    history = WorkflowETAHistory(folder + "/h.sqlite")
    tracker = t.WorkflowETATracker(types.SimpleNamespace(send_sync=send_sync), history, find_prompt_fn=lambda p: (graph, ["save"]),
                                   clock=lambda: now[0], wall=lambda: 1.7e9 + now[0], verbose=False, start_thread=False, device="gpu0")
    original_rows = tracker._watch_rows_snapshot
    tracker._watch_rows_snapshot = lambda run: (rebuilds.__setitem__(0, rebuilds[0] + counting[0]), original_rows(run))[1]
    next_loop = [0.0]

    def loop_push():  # what the push thread does every 0.5 s while a run is active
        while now[0] >= next_loop[0]:
            next_loop[0] += 0.5
            with tracker.lock:
                if tracker.run is not None:
                    tracker._push(tracker.run, force=True)

    injected_at = {}
    for number in range(2):  # the second run has a comparison baseline
        pid = f"p{number}"
        now[0] += 5
        next_loop[0] = now[0]
        first_push = len(pushes)
        tracker.on_event("execution_start", {"prompt_id": pid})
        cached = ["ckpt", "pos", "neg"] if number else []
        if cached:
            tracker.on_event("execution_cached", {"nodes": cached, "prompt_id": pid})
        for node in order:
            if node in cached:
                continue
            now[0] += 0.02
            loop_push()
            tracker.on_event("executing", {"node": node, "prompt_id": pid})
            if node.startswith("s") and node[1:].isdigit():
                for step in range(1, 21):
                    now[0] += 1.0
                    loop_push()
                    tracker.on_event("progress", {"value": step, "max": 20, "node": node, "prompt_id": pid})
                    if number == 1 and node == "s0" and step == 10 and not injected_at:
                        with tracker.lock:  # a runtime row appears mid-run (rows are replaced, never mutated)
                            run = tracker.run
                            run.watch_rows = run.watch_rows + [dict(id='["s0","KSampler","runtime_probe"]', node="s0", parameter="runtime_probe",
                                                                      value='"1"', group="Runtime", input=None, **{"class": "KSampler"})]
                            injected_at["index"] = len(pushes)
            else:
                now[0] += 0.15
                loop_push()
        tracker.on_event("executing", {"node": None, "prompt_id": pid})
        tracker.on_event("execution_success", {"prompt_id": pid})
        run_pushes = pushes[first_push:]

        # first push of a run and the final push are complete
        assert run_pushes[0]["watched"]["full"] is True
        assert {"rows", "nodes", "previous_nodes", "baseline_prompt", "prompt_id"} <= set(run_pushes[0]["watched"]), run_pushes[0]["watched"].keys()
        assert run_pushes[-1]["state"] == "done" and run_pushes[-1]["watched"]["full"] is True
        if number == 1:
            assert run_pushes[0]["watched"]["baseline_prompt"] == "p0", "the second run compares against the first"

        partial = [p["watched"] for p in run_pushes if p["watched"]["full"] is False]
        full = [p["watched"] for p in run_pushes if p["watched"]["full"] is True]
        assert len(partial) > len(full) * 3, (len(partial), len(full))
        assert all("previous_nodes" not in w and "baseline_prompt" not in w for w in partial), "unchanged parts are not repeated"
        rows_sent = [w for w in partial if "rows" in w]
        assert len(rows_sent) == (1 if number == 1 else 0), f"rows travel only when they changed (run {number}): {len(rows_sent)}"
        assert any(p["watched"]["full"] for p in run_pushes[1:-1]), "a periodic complete update heals pages that missed a push"

    # the rows were rebuilt when they changed, not on every push
    assert rebuilds[0] <= 6 and rebuilds[0] < len(pushes) / 10, (rebuilds[0], len(pushes))
    assert len(checks) > 100, len(checks)

    # bytes: a push used to carry the complete watched data
    complete = len(json.dumps(normal(tracker._watch_snapshot(tracker.run))) if tracker.run else json.dumps(pushes[-1]["watched"]))
    sent = sum(len(json.dumps(p["watched"])) for p in pushes)
    assert sent < 0.2 * complete * len(pushes), (sent, complete * len(pushes))

    # status endpoint data: complete by default, compact on request
    status = tracker.status_snapshot()
    assert status["watched"]["full"] is True and status["watched"]["rows"] and status["logs"]
    compact = tracker.status_snapshot(compact=True)
    assert "watched" not in compact and "logs" not in compact and compact["compact"] is True
    assert all(field in compact for field in panel_model.PANEL_FIELDS if field in status), "compact keeps everything the panel reads"
    assert len(json.dumps(compact)) < 0.15 * len(json.dumps(status)), (len(json.dumps(compact)), len(json.dumps(status)))
    snapshot = panel_model.Snapshot()  # the desktop panel's own model consumes the compact payload as it comes over the wire
    snapshot.accept(panel_model.valid_payload(normal(compact)), 100.0)
    view = snapshot.view(100.0)
    assert view["theme"] == compact["theme"] and view["extension"] == "Workflow ETA v" + t.WORKFLOW_ETA_VERSION and not view["newer"], view
    assert compact["state"] == "done" and view["total"] != "—", view
    history.close()

# ---- the rows builder memoizes serialization without ever merging distinct values
values = [0.0, -0.0, 1, True, 1.0, False, 0, "1", "true", None, float("nan"), 10 ** 20, "café ☕", "x" * 64, "model.safetensors", -1, 2.5e-10]
graph = {f"n{i}": {"class_type": "KSampler", "inputs": {"steps": value, "cfg": values[(i + 1) % len(values)]}} for i, value in enumerate(values)}
run = types.SimpleNamespace(graph=graph, reach=list(graph), model_map={}, node_cold={}, shape_plans={}, step_plans={}, video_sources={})
rows = t._workflow_eta_watch_rows(run, {}, "gpu0")
by_key = {(row["node"], row["parameter"]): row for row in rows}
for i, value in enumerate(values):
    for parameter, expected in (("steps", value), ("cfg", values[(i + 1) % len(values)])):
        row = by_key[(f"n{i}", parameter)]
        assert row["value"] == json.dumps(expected, ensure_ascii=False, sort_keys=True, default=str), (parameter, expected, row["value"])
        assert row["id"] == json.dumps([f"n{i}", "KSampler", parameter])
shown = {by_key[(f"n{i}", "steps")]["value"] for i in range(len(values))}
assert {"0.0", "-0.0", "1", "true", "1.0", "false", "0", '"1"', '"true"', "null"} <= shown, "0.0/-0.0, 1/1.0/true and 1/\"1\" stay distinct"

print("PASS: pushes carry only changed watched parts and merging them equals the complete data after every push; "
      "compact status drops the bulk; rows builder memoization keeps distinct values distinct")
