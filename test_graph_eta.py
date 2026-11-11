"""Whole-graph ETA: graph signature, run-level prior, schema 11, and the blend in _workflow_eta."""
import shutil, sqlite3, sys, tempfile, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory, SCHEMA_VERSION, make_workflow_eta_graph_signature
from workflow_eta_tracker import WorkflowETATracker

assert SCHEMA_VERSION == 11


def graph(seed=10, steps=20):
    return {"model": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
            "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
            "sampler": {"class_type": "KSampler", "inputs": {"steps": steps, "model": ["model", 0], "latent_image": ["latent", 0],
                       "cfg": 7., "denoise": 1., "sampler_name": "euler", "scheduler": "normal", "seed": seed}},
            "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["model", 2]}}}


g = graph()
sig1 = make_workflow_eta_graph_signature(g, {"model": ("a.safetensors",), "decode": ("a.safetensors",)})
# seed change and node renumbering must not change the signature
g2 = graph(seed=999)
g2 = {"x" + k: v for k, v in g2.items()}
for v in g2.values():
    v["inputs"] = {k: (["x" + l[0], l[1]] if isinstance(l, list) else l) for k, l in v["inputs"].items()}
assert make_workflow_eta_graph_signature(g2, {"xmodel": ("a.safetensors",), "xdecode": ("a.safetensors",)}) == sig1
# a real parameter change must change it
assert make_workflow_eta_graph_signature(graph(steps=40)) != sig1
# model lineage is part of it
assert make_workflow_eta_graph_signature(g, {"model": ("b.safetensors",)}) != sig1
# cosmetic strings, file paths and counters are excluded
g3 = graph()
g3["sampler"]["inputs"]["title"] = "a person"
g3["sampler"]["inputs"]["output_1"] = 42
g3["model"]["inputs"]["ckpt_name"] = "other/dir/b.safetensors"  # path-like model string is excluded
assert make_workflow_eta_graph_signature(g3) == make_workflow_eta_graph_signature(graph())
# workload summary (reference dims) is part of it
wl = {"sampler": ([("latent", "EmptyLatentImage", {"width": 512, "height": 512}, [])], False)}
assert make_workflow_eta_graph_signature(graph(), None, wl) != make_workflow_eta_graph_signature(graph(), None, None)

with tempfile.TemporaryDirectory() as d:
    h = WorkflowETAHistory(d + "/h.sqlite")
    for dur in (100.0, 110.0, 120.0, 105.0):
        h.record_run(f"p-{dur}", dur, 4, 0, cold=False, graph_sig=sig1, device="dev")
    h.record_run("other", 999.0, 4, 0, cold=False, graph_sig="other", device="dev")
    p = h.predict_run(sig1, cold=False, device="dev")
    assert p is not None and p["tier"] == "run-exact" and p["n"] == 4 and 100.0 <= p["seconds"] <= 120.0, p
    assert p["low"] < p["seconds"] < p["high"]
    assert h.predict_run("missing", cold=False, device="dev") is None
    assert h.predict_run(sig1, cold=True, device="dev") is None  # cold state separated
    h.record_run("one", 50.0, 4, 0, cold=False, graph_sig="single", device="dev")
    assert h.predict_run("single", cold=False, device="dev")["tier"] == "run-few"
    h.close()

    # schema 11 migration on the real (schema 9/10) user database
    src = "/workspace/data/history.sqlite"
    if Path(src).exists():
        dst = tempfile.mkdtemp() + "/mig.sqlite"
        shutil.copy(src, dst)
        h = WorkflowETAHistory(dst)
        cols = {r[1] for r in h._db.execute("PRAGMA table_info(runs)")}
        assert {"graph_sig", "device"} <= cols, cols
        assert h._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "11"
        h.close()

    # tracker blend: whole-run prior drives remaining until the first node completes
    h = WorkflowETAHistory(d + "/h2.sqlite")
    clock = types.SimpleNamespace(now=0.0)
    def mk():
        t = WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a: None), h,
                              find_prompt_fn=lambda pid: (graph(), ["decode"]), clock=lambda: clock.now,
                              verbose=False, start_thread=False, device="gpu",
                              resident_fn=lambda: frozenset())
        t._on_start({"prompt_id": "p0"})
        return t
    probe = mk()
    real_sig = probe.run.graph_sig
    probe.run = None
    for dur in (200.0, 210.0, 220.0):
        h.record_run(f"r{dur}", dur, 4, 0, cold=True, graph_sig=real_sig, device="gpu")
    t = mk()
    t.run.prompt_id = "p1"
    assert t.run.graph_pred is not None and t.run.graph_pred["tier"] == "run-exact", getattr(t.run, "graph_pred", None)
    clock.now = 30.0
    eta = t._workflow_eta(t.run, 30.0)
    assert abs(eta["remaining"] - (t.run.graph_pred["seconds"] - 30.0)) < 1e-6, eta
    assert eta["tiers"].get("run-exact") == 1
    # live/per-node data above the prior must win: a slow sampler (900s history)
    # is not overridden by a 210s whole-run prior
    sampler_sig = t.run.meta["sampler"][1]
    for _ in range(4):
        h.record_node("KSampler", sampler_sig, 900.0, cold=True, device="gpu", node_id="sampler", model_key="a.safetensors")
    t2 = mk()
    t2.run.prompt_id = "p2"
    clock.now = 30.0
    eta2 = t2._workflow_eta(t2.run, 30.0)
    assert eta2["remaining"] > 800.0 and "run-exact" not in eta2["tiers"], eta2  # per-node sum wins over the prior
    t2.run = None
    t._on_cached({"prompt_id": "p1", "nodes": ["model", "latent"]})
    t._on_executing({"prompt_id": "p1", "node": "sampler"})
    for step in range(1, 21):
        clock.now = 40.0 + (step - 1) * 1.0
        t._on_progress({"node": "sampler", "value": step, "max": 20})
    expected_sig = t.run.graph_sig
    clock.now = 62.0
    t._on_executing({"prompt_id": "p1", "node": None})
    t._on_success({"prompt_id": "p1"})
    row = h._db.execute("SELECT graph_sig, device, duration FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == expected_sig and row[1] == "gpu" and abs(row[2] - 62.0) < 1e-6, row
    h.close()

print("PASS: graph signature stable across seeds/renumbering, run-level prior tiers, schema 11 migration, whole-run ETA blend + recording")
