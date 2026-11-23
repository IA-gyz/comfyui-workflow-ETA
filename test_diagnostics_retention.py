"""Every run writes a diagnostics file; the newest are kept within a run-count and a size limit, and the file being
written (or read by an export) is never deleted."""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
import workflow_eta_diagnostics as d

KEEP, MAX_MB = "WORKFLOW_ETA_DIAGNOSTICS_KEEP_RUNS", "WORKFLOW_ETA_DIAGNOSTICS_MAX_MB"


def with_env(**values):
    environment = {k: v for k, v in os.environ.items() if k not in (KEEP, MAX_MB)}
    environment.update(values)
    return patch.dict(os.environ, environment, clear=True)


def make_files(folder, sizes, start=1_000_000):
    """run_<n>.jsonl files whose mtimes increase with n; returns paths oldest to newest."""
    paths = []
    for index, size in enumerate(sizes):
        path = Path(folder) / f"run_{index:03d}.jsonl"
        path.write_bytes(b"x" * size)
        os.utime(path, (start + index, start + index))
        paths.append(path)
    return paths


def names(folder):
    return sorted(p.name for p in Path(folder).glob("run_*.jsonl"))


# ---- defaults and environment overrides (the default size is 256 MiB in BYTES)
with with_env(), tempfile.TemporaryDirectory() as folder:
    diagnostics = d.WorkflowETADiagnostics(folder)
    assert diagnostics.keep_runs == 200 and diagnostics.max_total_bytes == 256 * 1024 * 1024, (diagnostics.keep_runs, diagnostics.max_total_bytes)
for values, expected in (({KEEP: "5", MAX_MB: "3"}, (5, 3 * 1024 * 1024)), ({KEEP: "abc", MAX_MB: "x"}, (200, 256 * 1024 * 1024)),
                         ({KEEP: "-1", MAX_MB: "-7"}, (200, 256 * 1024 * 1024)), ({KEEP: "0", MAX_MB: "0"}, (0, 0)), ({KEEP: "", MAX_MB: ""}, (200, 256 * 1024 * 1024))):
    with with_env(**values), tempfile.TemporaryDirectory() as folder:
        diagnostics = d.WorkflowETADiagnostics(folder)
        assert (diagnostics.keep_runs, diagnostics.max_total_bytes) == expected, (values, diagnostics.keep_runs, diagnostics.max_total_bytes)
    with with_env(**values), tempfile.TemporaryDirectory() as folder:  # explicit arguments win over the environment
        diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=7, max_total_bytes=1234)
        assert (diagnostics.keep_runs, diagnostics.max_total_bytes) == (7, 1234)

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- run count: the newest N by modification time survive
    paths = make_files(folder, [100] * 10)
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=4, max_total_bytes=0)
    assert diagnostics.prune() == 6
    assert names(folder) == [p.name for p in paths[-4:]], names(folder)

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- total size: newest first until the next file would exceed the limit
    paths = make_files(folder, [1000] * 6)
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=0, max_total_bytes=3500)
    assert diagnostics.prune() == 3
    assert names(folder) == [p.name for p in paths[-3:]]

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- the run being written is never deleted, even when it is the oldest and beyond the limit (it is kept in addition)
    paths = make_files(folder, [500] * 6)
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=2, max_total_bytes=0)
    diagnostics.prune(current=paths[0])
    assert names(folder) == sorted([paths[0].name] + [p.name for p in paths[-2:]]), names(folder)

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- 0 means unlimited
    make_files(folder, [100] * 8)
    assert d.WorkflowETADiagnostics(folder, keep_runs=0, max_total_bytes=0).prune() == 0 and len(names(folder)) == 8

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- only regular run_*.jsonl files are touched
    make_files(folder, [100] * 5)
    (Path(folder) / "notes.txt").write_text("keep")
    (Path(folder) / "run_other.txt").write_text("keep")
    (Path(folder) / "run_dir.jsonl").mkdir()
    outside = Path(tempfile.mkdtemp()) / "target.jsonl"
    outside.write_text("keep")
    (Path(folder) / "run_link.jsonl").symlink_to(outside)
    d.WorkflowETADiagnostics(folder, keep_runs=1, max_total_bytes=0).prune()
    assert len(names(folder)) == 3 and (Path(folder) / "run_004.jsonl").exists()  # newest regular file + the directory + the symlink (both skipped)
    assert (Path(folder) / "notes.txt").exists() and (Path(folder) / "run_other.txt").exists() and outside.read_text() == "keep"

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- nothing is deleted while an export is reading the directory
    make_files(folder, [100] * 6)
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=2, max_total_bytes=0)
    assert diagnostics.export_gate.acquire(blocking=False)
    try:
        assert diagnostics.prune() == 0 and len(names(folder)) == 6
    finally:
        diagnostics.export_gate.release()
    assert diagnostics.prune() == 4 and len(names(folder)) == 2
    assert diagnostics.export_gate.acquire(blocking=False), "prune released the gate"
    diagnostics.export_gate.release()

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- a file that cannot be deleted does not break anything
    paths = make_files(folder, [100] * 6)
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=2, max_total_bytes=0)
    real_unlink = Path.unlink

    def stubborn(self, *args, **kwargs):
        if self.name == "run_001.jsonl":
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)
    with patch.object(Path, "unlink", stubborn):
        assert diagnostics.prune() == 3
    assert names(folder) == ["run_001.jsonl", "run_004.jsonl", "run_005.jsonl"]

with with_env(), tempfile.TemporaryDirectory() as folder:
    # ---- starting a run prunes, keeps the new file, and the directory stays bounded across many runs
    diagnostics = d.WorkflowETADiagnostics(folder, keep_runs=5, max_total_bytes=0)
    make_files(folder, [100] * 9)
    for number in range(30):
        run = types.SimpleNamespace(graph={}, outputs=None, reach=set(), model_map={}, node_cold={}, video_sources={}, step_plans={},
                                    shape_plans={}, preds={}, watch_rows=[], prompt_id=f"p{number}", start_ts=1.0, machine_start=None, resident0=None)
        diagnostics.start(run, "gpu0")
        assert run.diagnostic_file.exists(), "the run just started is kept"
        assert len(names(folder)) <= 5, (number, len(names(folder)))
    assert len(names(folder)) == 5

print("PASS: diagnostics retention keeps the newest runs within count and size limits (defaults 200 runs / 256 MiB), never deletes the current run, "
      "skips non-run files and symlinks, waits for an export, tolerates undeletable files")
