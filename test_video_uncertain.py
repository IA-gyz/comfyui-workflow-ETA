"""Exact-tier predictions must downgrade when the upstream video frame count is unknown/estimated."""
import sys, tempfile, types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker, _workflow_eta_uncertain_video_upstream

graph = {
    "v": {"class_type": "MagikVideoLoader", "inputs": {"video": "clip.mp4", "max_frames": 100, "skip_first_frames": 5}},
    "h": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"video": ["v", 0], "length": 81}},
    "s": {"class_type": "SamplerCustomAdvanced", "inputs": {"latent_image": ["h", 1]}},
}
exact_meta = {"width": 1920, "height": 1080, "frames": 240, "frame_rate": "24/1", "uncertain_frames": False}
estimated_meta = {"width": 1920, "height": 1080, "frames": None, "estimated_frames": 240, "uncertain_frames": True}


def make_tracker(hist, video_sources):
    def find(pid):
        return graph, ["s"]
    with patch("workflow_eta_tracker._workflow_eta_collect_videos", return_value=video_sources):
        t = WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a: None), hist,
                              find_prompt_fn=find, verbose=False, start_thread=False,
                              clock=lambda: 0.0, device="dev")
        t._on_start({"prompt_id": "p"})
    return t


with tempfile.TemporaryDirectory() as d:
    h = WorkflowETAHistory(d + "/h.sqlite")

    # scenario A: exact video metadata -> sig includes frames, exact tier preserved
    t = make_tracker(h, {"v": exact_meta})
    sig_exact = t.run.meta["h"][1]
    for _ in range(4):
        h.record_node("MiniMaxH3ReferenceToVideo", sig_exact, 50.0, cold=False, device="dev", node_id="h")
    with patch("workflow_eta_tracker._workflow_eta_collect_videos", return_value={"v": exact_meta}):
        t._prepare(t.run)
    pred = t.run.preds["h"]
    assert pred["tier"] == "node-exact" and not pred.get("approximate"), pred
    assert not _workflow_eta_uncertain_video_upstream(t.run, "h")

    # scenario B: no metadata at start (probe missed/limited) -> same-sig exact rows exist,
    # but the temporal axis is untrusted: tier must downgrade and mark approximate
    t2 = make_tracker(h, {})
    sig_none = t2.run.meta["h"][1]
    for _ in range(4):
        h.record_node("MiniMaxH3ReferenceToVideo", sig_none, 50.0, cold=False, device="dev", node_id="h")
    with patch("workflow_eta_tracker._workflow_eta_collect_videos", return_value={}):
        t2._prepare(t2.run)
    pred2 = t2.run.preds["h"]
    assert pred2["tier"] == "node-few" and pred2.get("approximate") is True, pred2
    assert "h" in t2.run.workload_unknown
    assert _workflow_eta_uncertain_video_upstream(t2.run, "h")

    # scenario C: estimated frames only -> also downgraded
    t3 = make_tracker(h, {"v": estimated_meta})
    with patch("workflow_eta_tracker._workflow_eta_collect_videos", return_value={"v": estimated_meta}):
        t3._prepare(t3.run)
    assert _workflow_eta_uncertain_video_upstream(t3.run, "h")
    h.close()

print("PASS: uncertain upstream video frames downgrade exact tiers and mark the workload unresolved")
