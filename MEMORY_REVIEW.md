# Workflow ETA memory audit — v0.2.24

## Findings and fixes

| Finding | Impact | Fix |
|---|---|---|
| Diagnostic export retained all selected raw logs, split every run into lines, and retained every parsed event | Large transient heap spike; concurrent exports could multiply it | Bounded temporary-file snapshots, streamed JSONL summary parsing, compact/downsampled ETA samples, one export builder at a time |
| Diagnostic sanitization bounded individual strings/lists but not the entire event; sets/lists were copied before truncation | Very large or cyclic structures could cause excessive temporary allocations | Shared 100,000-item / 4,194,304-character text budget per event (including dictionary keys); bounded iteration; explicit omission markers |
| Repeated tracker attachment could create duplicate wrappers and push workers | Conditional lifecycle leak and redundant work | Idempotent attachment; explicit close stops the worker, clears retained snapshots, releases owned observer references and closes SQLite |
| Closing settings did not cancel an in-flight browser export | Detached dialog/node could remain retained until a large request completed | Abort on dialog close; check connection before download; always schedule object-URL release, including click failures |
| Removing a display retained its wrapped-log cache on the node | Extra retained strings when another component still held the removed node | Clear the cache on removal |
| Watched-value canvas fitting removed one character and remeasured the entire prefix repeatedly | Quadratic work and substring allocation churn on long fields; potential browser freeze | Bounded 2,048-character candidate and binary search for the displayed prefix |

## Measured checks

A synthetic 16 MiB JSONL export fixture with repeated, highly compressible event
payloads used **48.32 MiB** peak traced Python allocations in v0.2.23 and **0.68 MiB**
in v0.2.24. The archived JSONL bytes retained their original length and contents.
The fixture exercises the old unnecessary parse/retention path; it is not a
measurement of total process RSS, GPU memory, filesystem cache, or arbitrary ZIP sizes.
The final compressed ZIP still resides in memory for the HTTP response. Temporary
snapshots consume filesystem space and may use RAM when the system's temporary
filesystem is tmpfs. They are removed on success or failure.

A 400-generation synthetic tracker soak grew by about **4.3 KiB** of traced retained
Python allocations between the 80-run warmup and the 400-run endpoint. History rows
were allowed to accumulate on disk. Repeated attachment retained the same worker and
wrapper; close stopped that worker and the standalone tracker became collectible.

Regression coverage also checks concurrent-export rejection, bounded serializer
output, cyclic values, truncation of large sets without a full copy, browser request
cancellation and cache cleanup, and bounded canvas text measurements. Existing runtime
observer tests check metadata-only tensor observation and no retained tensor references.

## What is retained deliberately

- One active workflow, one previous completed comparison baseline and one latest UI snapshot.
- Backend and browser log rings of at most 250 entries; current-node ticks of at most 128;
  run accuracy samples of at most 4,096; header metadata cache of at most 128 entries.
- Runtime metadata is JSON-compatible shapes, dtypes and selected scalar values. No
  image/video pixels, tensor contents, or model weights are copied into ETA history.
- SQLite history is capped and pruned. Its file size does not necessarily shrink after
  deletion; SQLite can reuse those pages. This is distinct from a Python heap leak.
- Diagnostic files accumulate on disk by design. This update does not delete user logs.
- Application-wide listeners and one worker intentionally last for the ComfyUI/browser
  session. The explicit close path is resource cleanup, not hot-unregistration of
  already registered PromptServer routes and prompt callbacks.

## Limits

This audit found no evidence of a tensor/VRAM retention leak in ETA's observation
path. It cannot establish the memory behavior of a live ComfyUI executor, PyTorch
allocator, H3 model implementation, browser renderer, or other custom nodes. No live
CUDA/KDE generation was available. The offline replay accuracy numbers from earlier
releases are not new memory or production accuracy measurements.
