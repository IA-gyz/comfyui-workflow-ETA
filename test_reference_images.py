"""Multi-image reference nodes must contribute per-image dimensions to the workload signature."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_tracker import _workflow_eta_workload_lineage, make_workflow_eta_signature


def graph(refs):
    inputs = {f"reference_{i+1}": name for i, name in enumerate(refs)}
    return {
        "refs": {"class_type": "Magik9ReferencesImages", "inputs": inputs},
        "h3": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"ref_images": ["refs", 0], "length": 81, "ref_image_size": "max"}},
    }


def info_fn(name):
    return {"a.jpg": {"width": 1920, "height": 1080}, "b.jpg": {"width": 640, "height": 480},
            "c.jpg": {"width": 1280, "height": 720}, "big.jpg": {"width": 4096, "height": 2160},
            "missing.jpg": None}[name]


wl = _workflow_eta_workload_lineage(graph(["a.jpg", "b.jpg", "c.jpg"]), image_info_fn=info_fn)
tokens, uncertain = wl["h3"]
ref_control = next((c for c in tokens if c[0] == "refs"), None)
assert ref_control is not None and "source_reference_images" in ref_control[2], tokens
assert ref_control[2]["source_reference_images"] == sorted([(1920, 1080), (640, 480), (1280, 720)]), ref_control[2]
assert uncertain is False

sig1, _ = make_workflow_eta_signature("MiniMaxH3ReferenceToVideo", graph(["a.jpg", "b.jpg", "c.jpg"])["h3"]["inputs"], workload=wl["h3"][0])
wl2 = _workflow_eta_workload_lineage(graph(["big.jpg", "b.jpg", "c.jpg"]), image_info_fn=info_fn)
sig2, _ = make_workflow_eta_signature("MiniMaxH3ReferenceToVideo", graph(["big.jpg", "b.jpg", "c.jpg"])["h3"]["inputs"], workload=wl2["h3"][0])
assert sig1 != sig2, "same file list shape but different max resolution must change the signature"

# same paths, changed file content/resolution -> signature changes (cache keyed by path, info re-read per graph pass)
wl3 = _workflow_eta_workload_lineage(graph(["a.jpg", "b.jpg", "c.jpg"]), image_info_fn=lambda name: {"width": 800, "height": 600} if name == "a.jpg" else info_fn(name))
sig3, _ = make_workflow_eta_signature("MiniMaxH3ReferenceToVideo", graph(["a.jpg", "b.jpg", "c.jpg"])["h3"]["inputs"], workload=wl3["h3"][0])
assert sig3 != sig1, "overwritten reference file with new resolution must change the signature"

# unreadable reference file -> unresolved workload, no crash
wl4 = _workflow_eta_workload_lineage(graph(["a.jpg", "missing.jpg"]), image_info_fn=info_fn)
assert wl4["refs"][1] is True or wl4["h3"][1] is True
assert "source_reference_images" in next(c for c in wl4["h3"][0] if c[0] == "refs")[2]

print("PASS: multi-reference image dimensions enter the workload signature; resolution changes separate signatures; unreadable files mark unresolved")
