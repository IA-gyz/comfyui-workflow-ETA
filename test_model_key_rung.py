"""The ladder gains a model-key rung between sig-level and class-level matching."""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory

with tempfile.TemporaryDirectory() as d:
    h = WorkflowETAHistory(d + "/h.sqlite")
    # same class, three different configs (sigs) on model A, one config on model B
    for sig, dur in (("sigA1", 100.0), ("sigA2", 110.0), ("sigA3", 120.0), ("sigA4", 105.0)):
        h.record_node("KSampler", sig, dur, cold=False, device="dev", model_key="modelA.safetensors", node_id="7")
    h.record_node("KSampler", "sigB1", 30.0, cold=False, device="dev", model_key="modelB.safetensors", node_id="8")
    h.record_node("KSampler", "sigB2", 32.0, cold=False, device="dev", model_key="modelB.safetensors", node_id="8")
    h.record_node("KSampler", "sigB3", 28.0, cold=False, device="dev", model_key="modelB.safetensors", node_id="8")

    # unknown sig on model A -> model rung (not bare class)
    p = h.predict_node("KSampler", "sigA_unknown", cold=False, device="dev", node_id="9", model_key="modelA.safetensors")
    assert p["tier"] == "model" and 100.0 <= p["seconds"] <= 120.0 and p["n"] == 4, p
    # unknown sig on model B -> model rung with model B's median
    p = h.predict_node("KSampler", "sigB_unknown", cold=False, device="dev", node_id="9", model_key="modelB.safetensors")
    assert p["tier"] == "model" and 28.0 <= p["seconds"] <= 32.0 and p["n"] == 3, p
    # no model_key -> falls through to class tier (mixes both models)
    p = h.predict_node("KSampler", "sigX", cold=False, device="dev", node_id="9")
    assert p["tier"] == "class", p
    # single model row -> model-few
    h2 = WorkflowETAHistory(d + "/h2.sqlite")
    h2.record_node("KSampler", "sigC1", 44.0, cold=False, device="dev", model_key="modelC.safetensors")
    p = h2.predict_node("KSampler", "sigC_other", cold=False, device="dev", model_key="modelC.safetensors")
    assert p["tier"] == "model-few" and abs(p["seconds"] - 44.0) < 1e-6, p
    h.close(); h2.close()

print("PASS: model-key rung prices same-model configs before the bare class average")
