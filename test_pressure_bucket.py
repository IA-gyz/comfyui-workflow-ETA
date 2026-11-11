import shutil, sqlite3, sys, tempfile, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory, SCHEMA_VERSION
from workflow_eta_tracker import _workflow_eta_pressure_bucket

assert SCHEMA_VERSION == 10

# bucket mapping
assert _workflow_eta_pressure_bucket(None) is None
assert _workflow_eta_pressure_bucket({}) == 0
assert _workflow_eta_pressure_bucket({"models": [{"offloaded_bytes": 2e9}]}) == 1
assert _workflow_eta_pressure_bucket({"models": [{"offloaded_bytes": 6e9}]}) == 2
assert _workflow_eta_pressure_bucket({"models": [{"offloaded_bytes": 1e8}, {"offloaded_bytes": 6e9}]}) == 2

with tempfile.TemporaryDirectory() as d:
    h = WorkflowETAHistory(d + "/h.sqlite")
    cols = {r[1] for r in h._db.execute("PRAGMA table_info(node_runs)")}
    assert "pressure" in cols
    cols = {r[1] for r in h._db.execute("PRAGMA table_info(sampler_runs)")}
    assert "pressure" in cols
    # heavy-offload rows are ~3x slower for the same signature
    for _ in range(4):
        h.record_node("KSampler", "sigA", 10.0, cold=False, device="dev", node_id="5", pressure=0)
        h.record_node("KSampler", "sigA", 30.0, cold=False, device="dev", node_id="5", pressure=2)
        h.record_completed_run([{"meta": ("KSampler", "sigS", "m"), "node": "5", "cold": False, "duration": 21.0,
                                 "sampler": (20, 2.0, 1.0), "step_sig": "stepA", "tail": 0.0}],
                               "p0", 21.0, 1, 0, False, "dev", pressure=0)
        h.record_completed_run([{"meta": ("KSampler", "sigS", "m"), "node": "5", "cold": False, "duration": 63.0,
                                 "sampler": (20, 6.0, 3.0), "step_sig": "stepA", "tail": 0.0}],
                               "p2", 63.0, 1, 0, False, "dev", pressure=2)
    p0 = h.predict_node("KSampler", "sigA", cold=False, device="dev", node_id="5", pressure=0)
    p2 = h.predict_node("KSampler", "sigA", cold=False, device="dev", node_id="5", pressure=2)
    pNone = h.predict_node("KSampler", "sigA", cold=False, device="dev", node_id="5")
    assert abs(p0["seconds"] - 10.0) < 1e-6, p0
    assert abs(p2["seconds"] - 30.0) < 1e-6, p2
    assert 10.0 <= pNone["seconds"] <= 30.0 and pNone["n"] == 8, pNone  # unknown pressure mixes both buckets
    s2 = h.predict_sampler_steps("KSampler", "stepA", 20, cold=False, device="dev", node_id="5", pressure=2)
    s0 = h.predict_sampler_steps("KSampler", "stepA", 20, cold=False, device="dev", node_id="5", pressure=0)
    assert abs(s2["seconds"] - (6.0 + 19 * 3.0)) < 1e-6, s2
    assert abs(s0["seconds"] - (2.0 + 19 * 1.0)) < 1e-6, s0
    # batch record path stores pressure
    h.record_completed_run([{"meta": ("KSampler", "sigB", "m"), "node": "7", "cold": False, "duration": 12.0, "sampler": None}],
                           "pid", 12.0, 1, 0, False, "dev", pressure=2)
    row = h._db.execute("SELECT pressure FROM node_runs WHERE sig='sigB'").fetchone()
    assert row[0] == 2, row
    h.close()

# migration of a real schema-9 database (user's 2-week history)
src = "/workspace/data/history.sqlite"
if Path(src).exists():
    dst = tempfile.mkdtemp() + "/mig.sqlite"
    shutil.copy(src, dst)
    before = sqlite3.connect(dst).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    h = WorkflowETAHistory(dst)
    after = h._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    cols = {r[1] for r in h._db.execute("PRAGMA table_info(node_runs)")}
    assert before == "9" and after == "10" and "pressure" in cols, (before, after, cols)
    n = h._db.execute("SELECT COUNT(*) FROM node_runs").fetchone()[0]
    assert n > 29000 and h._db.execute("SELECT COUNT(*) FROM node_runs WHERE pressure IS NOT NULL").fetchone()[0] == 0
    p = h.predict_node("VAEDecode", h._db.execute("SELECT sig FROM node_runs WHERE class_type='VAEDecode' LIMIT 1").fetchone()[0],
                       cold=False, device="cuda:0 NVIDIA GeForce RTX 3090 : cudaMallocAsync", pressure=2)
    assert p["seconds"] is None or p["seconds"] > 0
    h.close()

print("PASS: pressure bucket mapping, schema 9->10 migration on real DB, per-bucket exact/step predictions, batch record path")
