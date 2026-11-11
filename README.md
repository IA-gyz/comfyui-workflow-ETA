# Workflow ETA v0.2.34

This release includes the latest dimension detection, seven themes, eight
progress-bar styles and the previous ETA reliability fixes. The package version is v0.2.34.

Automatic timing history and a live display for ComfyUI workflows.

## Optional verbose log panel (v0.2.3)

Click the icon in the node's **bottom-right corner** to show or hide its verbose
log panel. The node expands and shrinks to fit; the panel follows the selected
UI scale and theme. Visibility is stored per node with the saved workflow.
The right-click menu also offers Show verbose log / Hide verbose log.

The panel displays timestamped Workflow ETA diagnostics: device/history startup
messages, predicted totals and confidence tiers, per-node prediction sources and
sample counts, measured durations, sampler startup/step timings, unresolved
metadata, completion/accuracy summaries, interruption and persistence errors.
This is the extension's log, not the entire ComfyUI server console.

The newest entries are shown by default. Use the panel header's up/down buttons
to browse older/newer pages, and the rightmost latest button to return to the
newest entries. Text wraps to the available width. The log buffer is shared by
all Workflow ETA displays and retains the latest 250 entries (700 characters
maximum per message). It remains available after completion and interruption.

Buffers are kept in memory only and reset when the ComfyUI backend restarts;
log contents are not saved in the workflow or timing database. Browser reloads
and opening the panel restore the backend's retained buffer through the status
endpoint. WebSocket updates send only new entries. Logging into this buffer
also works with WORKFLOW_ETA_VERBOSE=0, which disables console printing only.

## Empirical batch and resolution estimates (v0.2.2)

Sampler estimates can now learn relationships across otherwise matching batch
sizes or resolutions. Exact history takes priority, followed by compatible
step-count scaling, then empirical workload prediction. Other nodes retain their
ordinary history estimates; this does not scale the entire workflow.

Calibration requires at least two successful complete single-pass measurements
at each of two distinct workloads. Keep model, prompt/conditioning, sampler,
scheduler, steps, CFG, denoise, cold/warm state and device comparable. For example,
run 768x768 twice and 1024x1024 twice, then try 896x896; or run batches 1 and 3
twice each, then try batch 2. Seed changes are allowed.

Resolution comparison holds batch size and aspect ratio fixed; batch comparison
holds resolution fixed. Startup overhead, per-step rate and cleanup time are
interpolated separately from measured values. Simultaneous changes to both axes,
or to steps plus geometry, do not qualify for empirical scaling.

The estimator uses nearest calibrated neighbors, rejects large gaps (area ratio
above 2.5, batch ratio above 4), requires reasonably stable per-step measurements,
and declines observed abrupt rate jumps. Extrapolation is limited to 80%–125%
of the measured workload boundary, with a wider heuristic range. These guards
cannot detect every unmeasured VRAM threshold and the ranges are not statistical
confidence intervals. Unsupported targets keep normal fallback predictions.

Supported explicit geometry paths: EmptyLatentImage/EmptySD3LatentImage, supported
LatentUpscale and RepeatLatentBatch chains, and single-image LoadImage -> explicit
ImageScale -> VAEEncode. Linked simple constants work. Image dimensions must be
aligned to 8 pixels. Zero/auto dimensions, video latents, arbitrary custom shape
operations and unresolved runtime shapes are not empirically extrapolated.
Image-to-image currently supports resolution calibration; adjustable batch
calibration requires an explicit batch/repeat control.

The UI labels these predictions learned resolution scaling or learned batch
scaling, while retaining estimated confidence. Database schema 4 adds workload
family and geometry fields without deleting old rows. Old records cannot supply
geometry calibration retroactively, so collect the new calibration runs after
installing. Step-scaling records from v0.2.1 remain usable.

## Sampler-only step extrapolation (v0.2.1)

If the sampler configuration matches except for step count, initial ETA can use:

    first-step time + (new effective steps - 1) * time per step + measured tail

Only the sampler estimate is extrapolated. Exact duration history for the new
configuration has priority. The scaled estimate is labeled estimated, and the
UI shows sampler step scaling while that prediction is relevant.

The family key retains all ancestor settings and metadata except the selected
step-count control and ordinary seed controls. Checkpoint/model lineage,
resolution, batch size, CFG, denoise, sampler, scheduler and advanced start/end
ranges must match. Warm/cold state, node ID and device must match too. Seed
changes alone do not block reuse. Other nodes retain their ordinary estimates.

Supported interfaces: KSampler, KSamplerAdvanced, SamplerCustom and
SamplerCustomAdvanced. Custom samplers require a direct supported sigma source:
BasicScheduler, KarrasScheduler, ExponentialScheduler, PolyexponentialScheduler,
BetaSamplingScheduler or VPScheduler. Unknown sigma generators and split/merged
sigma schedules retain normal fallback behavior. Advanced samplers use their
effective start/end-clipped step count, preserving range settings in the family.

Only successful complete single-pass progress beginning at step 1, ending at
its expected maximum, and without resets creates reusable step-family records.
Interrupted, resumed, multiple-pass, unknown-workload and mismatched-progress
runs do not supply step-scaling measurements.

The database migrates automatically (current schema: 4). Old timing rows are retained,
but cannot be retroactively assigned a trustworthy step-family key. Run the
unchanged sampler once after installing this release to populate suitable
history, then change only steps. Scaling is empirical, not a guarantee of
constant per-step speed across different noise schedules.

## Improved detection and progress animations

Settings now includes **Progress bar animation**: Static, Flowing stripes
(default), Shimmer, Pulse, Scanner, Comet, Wave and Rainbow. This choice is
saved per node with the workflow. Animation changes the filled bar's appearance;
it never advances the reported progress boundary. One shared redraw loop updates
active visible nodes at up to 30 FPS, stops when idle/disconnected/hidden, and
respects the browser's reduced-motion preference.

Dimension detection now accepts common source field aliases such as image_path,
filepath, filename, video_path and latent_path, including direct absolute files
and annotated ComfyUI paths. Simple connected constants and reroutes are resolved
without executing custom node code. Cycles and random values remain unresolved.

Saved latent shapes are read from a bounded safetensors header (up to 1 MiB),
without loading tensor payloads. The actual latent shape is fingerprinted; pixel
width/height are not guessed from a universal VAE factor.

If ffprobe is available on PATH, local video metadata supplies width, height,
rotation, frame count and frame rate. Each query has a 0.5-second timeout and each
preparation pass probes at most three unique videos; successful/cached results
are keyed by path, file size and modification time. Missing ffprobe, unknown frame
counts, folder/batch loaders and runtime-selected data can remain approximate.
No extra installation is required for image or latent metadata detection.

The dimensions-unresolved indicator now considers unfinished nodes only and
excludes a current node whose live rate is calibrated. Startup logs list up to
eight affected nodes; WebSocket/status payloads include unresolved_dimensions.
Native unknown node expansion remains approximate. Actual runtime tensor inputs
are not inspected, so arbitrary custom loaders can still escape inference.

## Settings and themes

Click the gear button in the display's top-right corner to open settings.
The panel contains a **Scale UI** slider (50%–300%, in 5% increments) and a
**Theme** dropdown with seven palettes: Ocean (default), Midnight, Graphite,
Forest, Ember, Amethyst and Paper. Paper is a light theme.
Changes apply immediately to the node. Both properties are saved per node
with the workflow. Close the panel with its close button, Escape, or a click
on the backdrop. The panel closes automatically when its node is removed.
The right-click **Settings…** entry provides another way to open the panel.

## Scale UI

Right-click a Workflow ETA node and choose **Scale UI**, then select 50%, 75%,
100%, 125%, 150%, 175%, 200%, 250% or 300%. Default is 100%.
The display contents scale uniformly and the node resizes proportionally,
including any extra space added through manual resizing. ComfyUI's native
node title and context menu retain their normal canvas styling.

The scale is stored per node as properties.ui_scale in the workflow. Save the
workflow to preserve it across reloads. Older workflows default to 100%.
This setting has no effect on generation, timing signatures or the database.

## ETA reliability improvements

- Fingerprint connected steps, CFG, denoise, sampler/scheduler and step ranges.
- Calculate progress duration per unit; handle skipped values, ignore duplicates
  and reject invalid/non-finite updates.
- Detect reset counters and changed maxima. Repeated internal passes use whole-node
  history rather than assuming the current pass is last. Full durations are saved,
  without misleading single-pass sampler statistics.
- Include discovered nodes in live ETA/counts. Resumed parents contribute current
  ETA and are recorded once with cumulative duration; unknown metadata is unsaved.
- Match abort events to the active prompt. Interrupted runs remain unsaved and
  invalidate the residency heuristic for a conservative next-run estimate.
- Save completed runs in one SQLite transaction. Write failures roll back their
  measurements, log an error and still finalize the display.
- Reuse graph metadata on cache events and extract model literals once per node.
- Bound progress buffers to 128 updates and accuracy samples to 4096 entries.
- Label incomplete history as a partial estimate rather than a mathematical bound.
  The existing lower_bound payload field remains for compatibility.

## Install

Place this folder under ComfyUI/custom_nodes, replacing the existing Workflow ETA
files. Avoid installing a second copy: two trackers would collect duplicate
events. Restart ComfyUI, then hard-refresh the browser (Ctrl+Shift+R).
Add **Workflow ETA** from the **Workflow** node category or node search.
The display needs no connections. Save your workflow to retain its position
and size. It is registered by Python for node discovery and marked virtual by the frontend,
so it is excluded from backend execution. Its Python fallback is a harmless
empty-output function if the frontend extension is unavailable.

History is stored separately in user/workflow_eta/history.sqlite and survives
restarts. Schema migration is automatic.

## Display

- Remaining duration and approximate local finish time.
- Elapsed time, completed/executed node count and cached node count.
- Current node and run-level cold/warm indicator.
- Learned, estimated or partial-estimate confidence.
- Completion, interruption/error and disconnected states.

All displays show the server's current run, even if another workflow or client
queued it. Time values come from backend updates rather than a separate
browser countdown. The bar is estimated elapsed / estimated total, not a
percentage of sampling steps; it can move backward when estimates change.
Completed runs remain visible until the next run starts.

The status endpoint /workflow/eta/status restores the latest state on browser
load or reconnection. Normal updates arrive through workflow.eta WebSocket events.
The clock button on the WorkflowETA node (next to the settings gear) calls
POST /workflow/eta/open_panel, which opens the installed Workflow ETA Panel
desktop companion (loopback clients only); see Workflow_ETA_Panel/.

## WorkflowETAHistory and limitations

Records completed successful runs only. Per-node history separates node IDs,
signatures, cold/warm state and device. Signatures include literal parameters, ancestor model filenames and upstream
workload fingerprints. Sampler seeds and long prompt text remain excluded.

The fallback ladder prefers matching node/signature/state, then matching
signature/state, then the same node/class/state across signatures, then class,
then opposite-state history. Same-node cross-checkpoint fallback is marked
estimated: different architectures can have substantially different costs.
Old rows remain usable through fallback without deleting the database.

Cold/warm state is a heuristic based on model filenames from the last completed
run and whether ComfyUI reports loaded models. It cannot guarantee physical
VRAM residency. Ancestor lineage is conservative and can include models that
do not materially affect a node's execution. Reusing node IDs in unrelated
workflows can weaken cross-signature fallback.

An unseen checkpoint or unseen warm state may have an inaccurate initial ETA.
Live sampler steps improve it during execution. Unknown nodes make the estimate
partial. Dynamic expansion is approximate. No measurements imply no
meaningful initial estimate.

Console completion summaries report counts of cold and warm records separately.
The UI's cold indicator means at least one expected executed node is cold.

## Resolution and batch tracking

Changing upstream width, height, batch size, resize scale, crop settings or
shape-operation wiring changes downstream timing signatures. This separates
sampler and VAE histories even when their image/latent inputs are linked.
Linked dimension controls include their scalar dependency settings. Ordinary
sampler seed changes preserve matching history.

Source image headers supply width, height and frame count, including EXIF
orientation. No image tensors are decoded and no CUDA work is introduced.
Files are inspected once per prediction pass; changing the dimensions of a
file at the same path is detected. File content changes with identical metadata
shape do not create a downstream workload signature merely because of mtime.

This is a conservative configuration fingerprint, not generic runtime tensor
inspection and not a mathematical estimate of final dimensions. Custom nodes
with hidden shape changes, runtime input data, variable output counts or
unrecognized shape controls can still evade separation. Dynamic video/latent
loaders, unreadable image sources and recognizable random dimension controls
are marked approximate. Animated files with varying frame sizes can have a
runtime batch different from the header frame count.

Older rows remain intact and serve as estimated fallback. The first run at a
new workload may therefore be inaccurate; subsequent matching runs use their
own records. No fixed pixels-to-duration scaling is applied.

## Standalone checks

Run from this folder:

    python test_workflow_eta.py
    python test_workload.py
    python test_regressions.py
    python test_dimensions.py
    python test_step_scaling.py
    python test_empirical_scaling.py
    python test_verbose_logs.py
    node test_frontend.cjs

These check SQLite migration, history lookup, model lineage, state prediction,
confidence and frontend event/render behavior using stubs. They do not replace
a live ComfyUI/browser test.

To verify live, run a checkpoint once, change only the seed twice, switch
checkpoint, then change the seed again. Change both prompts several times to
exercise separate CLIP encoders. Inspect:

    grep 'workflow-eta' /home/toto/Apps/comfyui/user/comfyui.log

Expected startup messages include the GPU device and wrapper installation;
the browser console should show "display ready".

## Remaining limitations

The tracker follows one active node at a time; concurrent asynchronous execution
does not have independent timers. Expansion definitions are not recovered from
ComfyUI DynamicPrompt. Discovered nodes contribute live progress but cannot be
forecast in advance or recorded with accurate signatures while metadata is unknown.
Repeated Detailer passes use whole-node history; progress alone cannot predict
an unknown number of future passes. Cold/warm state remains a residency heuristic.
Prepared signatures are fixed per run; runtime mutations and hidden custom-node
dimensions can still escape workload fingerprinting. Ancestor traversal can still
be costly for large graphs containing many shape operations.

## H3 video-length calibration (v0.2.4)

Supports MiniMaxH3ReferenceToVideo latent output 1 feeding SamplerCustomAdvanced, including ComfyMathExpression frame controls and boolean ComfySwitchNode step controls. Expressions use a bounded arithmetic whitelist; no node code is executed.

Generated frames, rather than playback seconds, identify temporal workload. A matching frame family keeps model, resolution, steps, conditioning and reference inputs fixed. The sampler learns a local power curve between measured frame counts, interpolating startup, per-step cost and tail separately. This permits superlinear costs without inventing a fixed exponent. Two completed runs at each of two distinct frame counts are required; cold and warm histories are separate. Extrapolation is restricted to 80–125% of measured frame counts; unstable measurements and extreme discontinuities are rejected.

With only a four-second measurement, the extension cannot infer two-second performance. An uncalibrated H3 sampler contributes an unknown duration and the displayed ETA is a lower bound until live sampling provides timing. Other nodes retain their existing estimation behavior; this is not a whole-workflow video scaling rule. Existing history is preserved, but old rows cannot supply missing frame calibration. Start by completing the same workflow twice at each desired duration in the same model-residency state, then try an intermediate duration. Tests use synthetic nonlinear timings and workflow structure; real GPU timings still need validation.

## Theme-aware log colors (v0.2.5)

The verbose panel uses a dedicated palette for each of the seven themes. Entries are tagged INFO, RUN, NODE, DONE, CHECK, WARN or ERROR, so categories remain readable without relying on color. Wrapped lines keep their category color, and changing theme immediately recolors existing entries. All palette text colors meet a 4.5:1 contrast ratio against their theme background.

See [PARAMETER_TRACKING.md](PARAMETER_TRACKING.md) for the complete watched-key list, matching rules, exclusions and supported cross-configuration estimates.

## Watched data and run comparison (v0.2.6)

Open the verbose log with the bottom-right icon. The **Watched data** table appears below it, with a compact node/parameter/value layout, highlighted changes, eight-row pages, and an All values / Changed only filter. Click its heading to collapse it and reclaim space. The log toggle hides both sections. The collapse and filter settings are saved in the workflow.

Click **Compare ↗** or a value row to open a large theme-matched inspector. Search any displayed field, filter by change status or unresolved node, and select a data group. The inspector uses sticky column headers, wrapping values, and pages of 100 rows, keeping long prompts and large workflows readable. Values are rendered as plain text.

- **Parameter values** shows node ID/class, data group, parameter, previous/current value, change state, and the node's estimate method. Workload, source metadata, scalar dependencies, derived dimensions/frames/effective steps, model lineage, device/cold context, direct literals, and strict sampler-family inputs are included. Seeds remain excluded. **Family** fields are additional upstream settings retained by strict sampler comparisons, including prompts and links; their presence does not imply a numerical timing formula.
- **Node timing & impact** shows previous/current node forecasts, forecast difference, previous/current completed-node durations, observed duration difference, cache/unresolved status, and history tier. These are node-level observations; multiple changed fields do not establish a separate causal timing cost for each parameter. Forecasts are original per-node predictions, not a countdown. Actual duration appears after that node finishes.

The comparison baseline is the **last completed run in this server session**, matched by node ID and class. It is captured when a new run starts. Interrupted runs do not replace it. The first run after startup has no baseline; completed timing history in SQLite still persists as before. Comparing different workflows can show added/removed fields or unrelated nodes with reused identities, so use the same workflow for useful timing comparisons. Expanded runtime nodes with unavailable graph metadata cannot expose unknown parameters.

The new inspector is covered by standalone backend and frontend tests; full ComfyUI/browser visual validation remains necessary.

## Explicit timing coverage and provisional full ETA (v0.2.7)

The main time shows **Known-time subtotal** when some work has no usable duration, rather than the vague Partial label. Alongside it, **Provisional full ETA** and a provisional finish time combine the known subtotal with rough estimates for missing work where possible. Any nodes that still cannot be priced are counted explicitly; their unknown duration is excluded, not invented. A provisional full ETA can therefore still be incomplete. These are heuristic estimates, not guaranteed earliest finish times.

At the first completed sampling step, an optional rough projection uses elapsed time through that step, divided by completed progress, multiplied by remaining progress. This resembles a console progress-bar projection. **It includes initialization and may be much too high.** Once a regular step interval is available, measured step speed replaces this projection. Matching history can provide timing before live intervals exist; fallback history used only for the provisional line is labeled as possibly belonging to a different workload. Repeated/reset progress does not use the first-step projection.

Status wording distinguishes waiting for the first step without matching history, waiting for live timing while using history, first-step completion without regular speed, provisional speed from one or two intervals, measured speed from recent intervals, repeated passes, missing downstream durations and incomplete workflow graphs. Status transitions are logged once per change, rather than on every countdown tick. The node gains space for these explanations while keeping its log and watched-data controls aligned.

## Reference video length and Pixaroma adapters (v0.2.8)

Recognizes `PixaromaLoadVideo` metadata outputs (frame count, FPS, dimensions and duration), `LazySwitchKJ`, `Basic data handling: Equal`, and `easy int` scalar constants. The duration-zero pattern in the supplied v3.0 H3 workflow now resolves its generated length from the loader's frame-count output. This is a source-frame-count path, not a zero-second generation.

`max_frames`, `force_fps`, `custom_width` and `custom_height` join the upstream workload keys. Loaded video metadata includes the cap and skip controls: Pixaroma caps the source window before skipping frames. Changing reference length separates downstream history even when the generated output length remains fixed. The startup log reports loaded reference frames/duration and generated frame count; watched-data rows include the effective loader outputs.

Detection requires a readable local source file and `ffprobe` on ComfyUI's PATH (normally supplied by FFmpeg). Metadata queries read headers, not video tensors. Probes are cached by file size/mtime and limited to three distinct sources per preparation. If exact frame count is absent, duration/FPS can provide an explicitly approximate source-frame indication, not an exact generation count. An FPS conversion is conservatively unresolved until runtime because its precise output count depends on actual resampling. Proportional custom resizing is also left unresolved where rounding cannot be guaranteed.

Unresolved H3 generation length no longer selects an ordinary exact/class sampler duration or a historical step rate as if length were known. Live sampling can still provide a remaining-time estimate, and the separate provisional line may use explicitly labeled rough fallback history. No linear video-length multiplier is introduced. Existing nonlinear output-frame calibration remains strict: changes to reference inputs can require new matching measurements rather than reusing a curve from different conditioning.

Validation includes the supplied API workflow with synthetic metadata and dedicated regressions for source-matched generation, explicit duration, cap/skip controls, fixed output with changed reference length, overwritten-file metadata, missing headers, FPS-conversion uncertainty and cycles. The actual source video was not supplied, so its real frame count and GPU timing were not measured here.

## Start date/time and expected total duration (v0.2.9)

The display shows **Started** with the full execution-start date and time in the browser's local timezone. The server captures this timestamp once, so it remains stable through updates, reloads and reconnects while the run/status snapshot is retained. It is execution start, not queue submission time.

**Expected total** is elapsed time plus provisional estimated remaining time. It is labeled incomplete when some nodes remain unpriced or the graph is incomplete, and provisional when rough projections fill missing history. Until any usable estimate exists it says waiting for timing. On completion this line becomes **Actual total**; on interruption it shows elapsed time before stopping. The node expands to fit both new rows without overlapping the log or watched-data controls.

## Prominent timing display (v0.2.10)

Full remaining time is highlighted in a separate card with a 38px bold value. Elapsed time is 20px bold. Expected total and Expected finish time share a two-column row, with 23px/20px bold values. Incomplete/provisional labels stay attached to the total; finish time includes a short date when it is not on the current day. All sizes follow Scale UI and each theme's palette. New nodes are 540px wide; existing nodes expand to at least 480px to fit the row. Log and watched-data areas shift down to keep the timing section clear.

## Learning across reference video files (v0.2.11)

H3 sampler calibration now has a separate **reference-learned** family. It ignores the Pixaroma reference filename and temporal source metadata when comparing costs, while retaining source dimensions/FPS, loader controls, model lineage, output resolution, sampler configuration, prompts and other reference inputs. Ordinary exact history still retains the real filenames and temporal metadata.

The axis is effective loaded reference frames. Supported relationships are generated frames following reference frames at 1:1, or a fixed generated frame count. They are separate families: a fixed 100-frame output is never mixed with output length following the reference. Multiple independent video loaders, unknown frame counts and actual FPS conversion remain unsupported for this comparison. The adapter currently targets PixaromaLoadVideo feeding MiniMaxH3ReferenceToVideo and SamplerCustomAdvanced.

Startup, regular step cost and tail are learned separately with local power-curve interpolation; there is no hard-coded linear or quadratic multiplier. Calibration requires either two measurements at each of two frame counts, or three distinct frame counts with one measurement each, in the same device/cold-warm state and matching configuration. With three lengths whose recent per-step power slopes agree within 0.75 and lie between 0 and 4, extrapolation can reach twice the longest measured clip or half the shortest. Otherwise extrapolation remains limited to 80–125% of the measured range. Cross-video heuristic uncertainty ranges are ±35% for interpolation and ±50% for extrapolation; they are not statistical confidence intervals. Abrupt observed slowdowns are rejected, but an unobserved VRAM cliff cannot be predicted.

Thus, after suitable batch measurements, a 5-second to 10-second change can yield a greater-than-twofold estimate when the measured curve supports it. A single 5-second run cannot determine the nonlinear relationship. UI history tiers and scaling indicators report reference-learned estimates; actual live step timing replaces projections as usual.

SQLite schema 6 adds indexed reference-family/frame columns without deleting history. Older rows have no cross-video calibration fields and cannot be backfilled reliably; new successful sampling measurements build the curve automatically. Interrupted runs remain excluded. Calibration concerns the sampler only: preprocessing, model initialization in other nodes, decoding and saving continue to use their own node estimates.

Validation includes synthetic quadratic timings across different reference filenames and persistence/isolation regressions. Real H3 batch GPU timings still need validation.

## Persistent diagnostic recording for LLM review (v0.2.12)

Diagnostic recording is enabled automatically for new runs. Open the gear menu, choose **Recent runs to export (1–100)** and click **Export diagnostic log (ZIP)**. The default is the latest 20 runs, including a currently running one. Start with `RUN_SUMMARY.md` and `REVIEW_GUIDE.md`; give the whole ZIP to an LLM for analysis.

The bundle contains:

- `RUN_SUMMARY.md`: runs/outcomes, start dates, initial predictions, projected totals, node forecast-versus-actual tables and sampled ETA error trajectories for completed runs.
- `runs/run_*.jsonl`: schema-versioned append-only records of full API graphs (including seeds, short/long prompts and unused nodes), graph reachability, requested outputs, environment/device, inferred residency/cold state, model lineage, resolved video metadata, effective frame/step plans, watched controls, predictions and calibration anchors, cache events, progress, node UI-output events, node timing segments, tracker messages and terminal outcomes.
- `REVIEW_GUIDE.md`: an analysis brief explaining partial/full estimates, timing units, uncertainty, cold/warm heuristics, interrupted runs, nonlinear calibration and evidence limitations.
- `extension_source/`: installed implementation and documentation for code-aware review.
- `manifest.json`: included files and omissions caused by export limits.

Logs persist under ComfyUI's user directory at `workflow_eta/diagnostics/` and survive browser/ComfyUI restarts. They are separate from SQLite training history, so recording failed/aborted runs does not turn them into successful calibration. Sampling ETA snapshots are recorded at most once every two seconds; progress and node events are recorded as received. Completed run records retain initial coverage and predictions alongside actual timings. Initial predictions include their elapsed offset, so they can be compared correctly with the final total.

Missing estimates remain null. Interrupted runs retain completed-node durations, error data and current-node observed elapsed time, but do not claim a completed total. Active/crashed runs without terminal records remain explicitly incomplete. A partial last JSONL line is tolerated by the summary exporter and retained in the raw export. Older versions cannot retroactively supply these records.

Full prompt strings and paths are deliberately retained for review; inspect them before sharing. Common explicit credential keys are redacted, but arbitrary free text is not guaranteed to be secret-free. Media data URLs, binary/tensor values and media files are excluded. Strings over 1 MiB, nesting beyond depth 64 and lists beyond 20,000 entries are explicitly truncated. Each run is limited to 64 MiB, with a recording-limit marker if reached. Raw exports are capped at 64 MiB total; older/not-selected and size-omitted files are reported. Local recordings are not automatically deleted. Disable future recording with `WORKFLOW_ETA_DIAGNOSTICS=0` before restarting ComfyUI. Recording/export failures are reported without stopping ETA tracking.

The diagnostic endpoint is `/workflow/eta/diagnostics/export?limit=20`, available on the same ComfyUI server. It exports owned diagnostic files only, without arbitrary path parameters. The implementation does not collect full server-console logs, media bytes or live GPU memory telemetry.

## Completed duration highlighting (v0.2.13)

Completed Total duration, Actual total duration and Actual total values use yellow text. Paper uses a darker yellow for readability. Running, interrupted and idle states keep their existing value colors.

## Workflow-load node height fix (v0.2.14)
On workflow load, the canvas node refits its height to the saved UI scale, verbose-log visibility and watched-data collapse state. Excess saved height is discarded; saved width is retained. Manual resizing remains available during use. This package updates the ComfyUI extension, not the separate desktop companion.

## Flexible video discovery (v0.2.15)

Video scalar outputs, watched data, workload fingerprints and reference-length families now share one adapter lookup. Built-in adapters cover PixaromaLoadVideo and MagikVideoLoader. Magik folder paths and backend cursor/reset state are read without running the loader, writing cursor state or advancing its selection. If the installed cursor helpers cannot resolve the next source, ETA leaves it unresolved.

Unknown loaders still contribute generic source-header metadata when safely available, but ETA never assumes that their output socket numbers or trim semantics match Pixaroma. Third-party node classes can opt in with a declarative class attribute:

```python
WORKFLOW_ETA_VIDEO_ADAPTER = {
    "source": "video",  # source filename input; can be another input name
    "window": "cap_then_skip",
    "outputs": {2: "frames", 3: "fps", 4: "width", 5: "height", 6: "duration"},
}
```

This contract means max_frames caps the window first, then skip_first_frames trims its front; force_fps, custom_width and custom_height follow the Pixaroma-style controls. Missing controls default to zero. Declare this only when those semantics match. The outputs map can use different socket numbers. A different transform policy requires a dedicated adapter; output names alone do not prove behavior. ETA reads this class attribute from ComfyUI's registered node classes without calling INPUT_TYPES, IS_CHANGED or execution functions.

MagikCompare's Values comparisons are resolved for scalar constants. Magik.Float secondary numeric outputs are resolved only when the installed RETURN_TYPES schema confirms INT or FLOAT and the node has only a value input. Unsupported sockets remain unknown.

Headers without exact frame counts can supply an ETA-only duration/FPS estimate. Such inferred frame counts are explicitly approximate, remain flagged unresolved in watched workload metadata, and use separate calibration family hashes from exact counts. FPS resampling remains unresolved; no decoding is performed for ETA. No database reset is needed. Newly recognized signatures may require new matching runs; old unresolved history cannot be retroactively converted into exact frame measurements.

## Runtime workload learning (v0.2.16)

ETA now observes resolved node inputs at ComfyUI's async `get_output_data` boundary. This is independent of node names and includes tensors obtained from upstream cache entries. It reads tensor shape/dtype only: no contents, `.cpu()`, `.numpy()`, decoding or GPU synchronization. Bounded metadata is copied into JSON; tensors and model objects are never retained by ETA. Optional unsupported executor signatures disable observation with a startup log message; ordinary static ETA continues.

For synchronous completed outputs, diagnostics also record storage shapes, image batch counts/resolution where the node declares IMAGE outputs, and declared numeric/boolean scalar outputs. Latent shapes remain storage dimensions; no universal spatial or temporal compression factor is assumed. Pending async tasks/graph expansion scheduling are not used for runtime timing calibration; downstream consumers can still expose their resolved inputs. Opaque custom media objects and oversized/truncated containers are not calibrated by this mechanism.

A new runtime_runs SQLite table records successful-run durations with the actual input metadata and a recipe hash preserving upstream configuration and model lineage while removing loader filenames and cursor identities. Matching is strict on device, cold/warm state, node identity, recipe, input shapes and dtype. Different shapes do not inherit another shape's timing as an exact measurement. Existing step/nonlinear frame algorithms continue; this generic layer does not assume linear pixels or frames/time scaling.

When a node begins execution, matching actual runtime inputs refine its estimate. Resolved static configurations can reuse an initial profile after three consistent observations. If actual input shapes disagree with the history, the mismatched runtime estimate is dropped. Previously unseen transforms still require observations; this is not universal prediction of arbitrary code before execution. No historical shapes are invented for older database rows. Keep your existing database; migration is automatic. Interrupted/failed runs never become successful calibration rows.

The watched-data table adds a Runtime/resolved_inputs row after observation. Exports include runtime_inputs, runtime_outputs and runtime_timing_excluded events, plus the runtime observer source. To verify installation, the ComfyUI log should contain `runtime input observation: enabled`.

## Hybrid workload estimates (v0.2.17)

The estimator combines existing static step/frame/reference curves with runtime measurements. Exact runtime matches take priority. For supported single-pass samplers, actual sigmas tensor length (without reading values) or the resolved steps scalar supplies the iteration count. Runtime scaling preserves measured first-step initialization, regular per-step cost and finishing tail; 6 to 8 steps scales the iterative work, not the whole workflow. Only completed, first-to-last progress sequences enter this calibration. Arbitrary samplers/schedulers and partial KSamplerAdvanced passes do not receive this new generic scaling assumption.

Temporal video latent dimensions are raw B,C,T,H,W storage sizes. When a single reference video's effective count is known, that count is used instead; changing reference duration with a fixed output length must not reuse an exact timing solely because latent shapes match. Reference/media context is now part of runtime exact and step matching. Independent reference-video axes are not flattened into one.

With comparable measurements, video cost uses empirical log-space interpolation of the per-step rate and initialization overhead. Bounded extrapolation up to twice the measured extent requires three lengths with consistent local slopes. Sparse history can produce a clearly labeled provisional compute-cost ratio within a factor of two of an anchor, with a wide heuristic range. This is not a claim that video time is linear. Measured discontinuities/unstable timing groups prevent smooth-curve claims. Exact spatial dimensions, model/recipe, device and cold/warm state remain separated.

For supported source paths, effective reference/generated frame counts and source spatial metadata can project these runtime measurements at queue time, before sampling. The source-based projection is explicitly provisional until actual inputs arrive. It remains separate from the stricter three-consistent-observation runtime profile from v0.2.16. New shapes can invalidate the projection at sampler start. A changed reference clip with identical output latent shape is no longer treated as an exact runtime match.

The status display distinguishes runtime step scaling, learned runtime video curves, provisional video ratios and source-based video projections. The Runtime/workload_scaling watched row and runtime_prediction diagnostic events include targets, anchors, assumptions and curve slopes where available. Existing history is retained; new runtime scaling columns migrate automatically, and older shape observations without measured sampler components are not retroactively invented.

## Accuracy evaluation and machine-state learning (v0.2.18)

- Added a standalone read-only evaluator: `python workflow_eta_replay.py Workflow_ETA_diagnostics.zip --history /path/to/history.sqlite --output review.json`. It audits recorded forecasts, long-run midpoint errors, interval coverage and node-class errors. Its historical node backtest admits only database measurements recorded strictly before each held-out run start. It does not claim to rerun the old executor or supply missing historical telemetry. REPLAY_REVIEW.md/JSON contain the review of the supplied dataset.
- Optional machine snapshots record allocator-aware available GPU memory, allocated/reserved memory, RAM/swap counters, ComfyUI loaded/offloaded model bytes, VRAM mode, attention/offload options, Torch/CUDA versions and NVIDIA driver information when available. CUDA is never initialized solely for telemetry. Residency counters do not prove checkpoint filename identity; the existing identity/cold heuristics remain separate.
- Runtime families now distinguish coarse GPU/RAM pressure and model-residency regimes plus software/options. Unmatched machine state downgrades an otherwise exact static forecast to provisional instead of presenting it as matched runtime evidence. Boundaries are heuristic buckets (GPU/RAM available below 5%, below 20%, or higher), not a universal slowdown formula. This may temporarily reduce calibration coverage. `WORKFLOW_ETA_MACHINE=0` disables future machine observation before ComfyUI starts.
- A transparent wrapper times `load_models_gpu` calls and records post-call counters. The portion observed before the first sampler tick is separated in runtime history, so video scaling can distinguish measured model-management time from other first-step compute. Compilation and other initialization outside that call are not claimed as measured loading time. Other unload paths may appear only through periodic counter changes.
- Runtime central estimates and video anchors use recent weighted medians rather than giving old observations equal weight. Isolated outliers do not shift a weighted median much; consistent regime changes can. A new eta_errors table stores forecast-versus-actual evidence from completed runs only. Once nine prior errors are available, an empirical rolling error envelope can widen ranges. These are heuristic ranges, not guaranteed statistical confidence intervals. Live sampler ranges also reflect observed step-rate variation rather than a zero-width point interval.
- The Runtime/machine_state table row and machine_sample/model_management events expose the new evidence. SQLite migrates automatically, preserves old history, and bounds retention of the new rows. Old forecasts are not retrospectively assigned machine data. No universal accuracy gain is claimed before new telemetry is collected and evaluated.

## Downstream refinement from runtime values (v0.2.19)

Actual singleton numeric/boolean outputs now resolve workload-control links for the current run, regardless of the producing node's class name. This includes frame counts, FPS, dimensions, steps and calculation/switch outputs feeding tracked workload controls. Linked scalar inputs supplied from ComfyUI's cache can also supply authoritative values when the producing node does not execute again.

Only controls whose dependencies can affect workload are considered. Multi-item mapped outputs, truncated metadata, nonnumeric values and observations from another prompt are not treated as unambiguous scalars. Pending node plans/predictions are refreshed using the existing selected-file snapshot: no new file probing or backend folder-cursor read is performed. A loader advancing its cursor therefore cannot switch ETA to the next file mid-run.

For supported video adapters, observed effective frame counts can resolve FPS-conversion paths that static metadata deliberately left unknown. Actual loaded dimensions are separate from original source dimensions. The watched table and runtime_values_resolved diagnostic events expose corrections and changed pending predictions. No tensor contents are inspected.

The original initial forecast, completed-node timing records and the current output-producing node's timing metadata stay unchanged. When scalar inputs are observed immediately before a consumer executes, its plan can be corrected before timing calibration. A failed refresh is transactional and leaves the previous plans/source values intact. Runtime values reset for each generation; cached values become known through actual consumers rather than being invented from a prior run. Existing history is preserved.

## Partial runtime video metadata (v0.2.20)

Runtime frame counts, FPS, dimensions and duration now refine each field independently.
A loader that exposes only a frame count keeps its known dimensions; observed dimensions
are used even before the exact resampled frame count is available. Explicit runtime
duration takes precedence over count/FPS-derived duration. FPS conversion still waits
for an actual frame count rather than inventing one. No video is decoded for monitoring.

## Startup video-history bridge and nested latents (v0.2.21)

Queue-time video projections can now reuse measured sampler history when header
frame counts are approximate. The current graph/configuration and source geometry
must reproduce the stored family hash; device, cold/warm state, software, attention
and offload options remain constrained. Different memory/residency contexts are
allowed only for explicitly provisional projections, with at least ±50% bounds.
Actual runtime observations still take precedence. No fixed video power law is assumed.

The metadata observer now recognizes ComfyUI's NestedTensor container and records
all bounded video/audio component shapes. It reads no tensor contents and performs
no device transfers. Multi-component temporal axes are not collapsed into one
invented frame count. Older opaque observations remain available; queue-time
curves use the newest observation representation at each measured frame count.

The bundled STARTUP_REPLAY.json replays the 12 supplied test runs chronologically,
adding each completed timing only after forecasting that run. It validates the
sampler component, not a live full-workflow accuracy claim. The replay uses the
recorded graphs and header metadata, and a FLOAT/INT schema stub for the installed
Magik.Float node; execution, file probing and GPU state are not rerun.

## Preparation and finishing forecasts (v0.2.22)

Non-sampler nodes can provisionally reuse timings from a near-matching video
workload. The graph/configuration recipe, source geometry, device, cold/warm state,
software and execution options must match. Frame differences are limited to header
rounding (at most 5%, with an additional count tolerance); this is measured-duration
reuse, not linear scaling of a node's total time. Unstable timing groups are rejected.
Exact history retains priority. Bounds remain deliberately wide until actual inputs
arrive. Unseen video lengths still require calibration.

SaveVideo and CreateVideo can now record timing observations with opaque video
handles when their upstream video source has an exact resolved frame count. The
observer never opens or decodes those objects. The nested-tensor support added in
v0.2.21 enables future decoder timing observations too.

WORKFLOW_STARTUP_REPLAY.json contains a chronological queue-time forecast review of
the 12 supplied runs. Only preceding measurements are available for each forecast;
recorded cached-node notifications are applied. Runs 4–12 have a median absolute
percentage error of 2.67% for total startup forecasts (worst 8.73%). Across all 12,
including the first encounter with each length, the median is 5.60%. These are offline
forecasts with recorded headers/graphs/machine snapshots, not live generation results.
The first three runs remain poorly forecast without matching prior calibration.

Reproduce with:
```
python replay_startup_review.py diagnostics.zip history.sqlite --output review.json --magik-float-int-schema
```
The schema flag explicitly supplies the reviewed environment's Magik.Float FLOAT/INT
socket types. Omit it for other environments. The source database is opened read-only
and copied to a temporary database; only the temporary copy receives replayed rows.

## Forecast provenance in the timing inspector (v0.2.23)

Open the watched-data inspector and choose **Node timing & impact** to see the
estimate source, number of history samples, frozen startup forecast, current node
forecast and estimated range. On completion, actual duration and the signed initial
error (actual minus startup forecast) show which nodes were under/overestimated.
Hover a timing row for the raw history tiers and calibration details. The Runtime
group is now selectable in the parameter inspector.

The initial node forecasts are copied at the first run announcement and cannot be
rewritten by later input resolution or history refinement. These fields are also
included in diagnostic node summaries. Estimated ranges remain provisional; they
are not guaranteed confidence limits. Live remaining time stays on the main timer.
The v0.2.22 replay results remain historical validation of the prediction engine;
this release improves its inspection and records, not its timing model.

## Whole-run prior is a floor, not an override (v0.2.34)

The graph-level prior now only raises the estimate above the live/per-node sum; it
never lowers it. Previously a historical whole-run total could hide a currently slow
sampler (log showed 5:49 remaining for the sampler while the ETA displayed a lower
provisional total). The same floor applies to the provisional card value.

## Whole-graph ETA prior (v0.2.33)

Each run gets a graph-level signature (node classes + meaningful literal inputs +
model lineage + a coarse workload summary: width/height/frames/reference-image dims).
Seeds, node ids, file paths and cosmetic strings (titles, prompts, counters) are
excluded, so re-running the same workflow with different assets still matches. The
`runs` table stores graph_sig + device (schema 11). While no node has finished yet,
a matching whole-run prior (tiers run-exact / run-few) drives the remaining ETA;
once the first node completes, the per-node sum takes over.

## Confidence color ladder (v0.2.32)

The gradient strip on the timer card is replaced by a five-block color ladder
(deep green on top, red at the bottom; + / ETA confidence / - markers kept). When a
confidence level is known, the matching block shines (full opacity with a white
outline) and the others dim to 35%; in idle state all blocks show at 85%.

## Independent browser panel (v0.2.31)

The WorkflowETA node has an "open in new window" button (left of the clock button)
that opens `/workflow/eta/panel` in a separate browser window titled **Workflow ETA**.
The page is self-contained: it bootstraps from `GET /workflow/eta/status` and then
follows `workflow.eta` WebSocket events, rendering remaining/finish/elapsed/nodes,
the confidence color and the progress bar. Repeated clicks focus the same window.
The clock button still launches the desktop companion via `/workflow/eta/open_panel`.

## Model-key rung in the fallback ladder (v0.2.30)

When no signature match exists, predictions now try a model-key rung
(same class, same model lineage, any config; tiers model / model-few) before the
bare class average. On the reference dataset this cut fallback MAE from 73.7 s to
58.2 s on model-covered held-out points (~21% better), because the class average
mixed very different models/configs (e.g. +190 s overestimate on video samplers).

## Reference-image dimensions in the workload signature (v0.2.29)

Multi-image reference nodes (inputs like reference_1..reference_N on classes with
"reference" in the name) are now probed like image loaders: each file's width/height
(bounded to 12 files, 24 probes per graph pass) enters the workload token as
source_reference_images. Previously only the file paths were hashed, so overwriting
a reference image with a different resolution kept the same signature and exact
matches averaged across very different processing times. Unreadable reference files
mark the workload unresolved.

## Provisional totals shown as ranges (v0.2.28)

When nodes are unpriced or the graph is incomplete (lower-bound estimate), the top
remaining line reads "Known ≥ <low>" and the main timer card shows the known
interval as a range ("≈ low – high") instead of a single point, alongside the
existing "N nodes unpriced" caveat. Fully priced runs still show a point estimate.

## sampler_runs restricted to sampler classes (v0.2.27)

Step-family timing is now recorded only for KSampler, KSamplerAdvanced,
SamplerCustom and SamplerCustomAdvanced. Fast video post-process nodes that emit
progress ticks (e.g. per-frame upscalers) previously landed in sampler_runs with
sub-second step times, polluting family statistics. Their durations remain in
node_runs as before.

## Uncertain video length downgrades exact tiers (v0.2.26)

If any upstream video loader's frame count is missing or only estimated at run start,
the workload signature cannot be trusted to separate temporal lengths. Exact/few
predictions for such nodes are downgraded one tier (node-exact -> node-few, exact ->
few, wider intervals) and marked approximate, and the node counts as unresolved
workload for the run-level confidence label. Runs with fully resolved video headers
are unaffected.

## Offload-pressure bucket in exact matching (v0.2.25)

The boolean cold flag under-represented how much model weight was offloaded at run
start; exact-history predictions on heavily offloaded runs underestimated by ~1.4x.
The machine snapshot at run start is now bucketed into a pressure value (0 = <1 GB
offloaded, 1 = 1-5 GB, 2 = >=5 GB) and stored per row in node_runs/sampler_runs
(schema 10; old rows keep NULL and still match). Exact/few and step-scaled lookups
prefer rows with the same pressure bucket. Predictions made without machine telemetry
behave exactly as before.

## Memory and lifecycle audit (v0.2.24)

Diagnostic exports now snapshot to temporary files, stream summary parsing, bound
retained ETA summary samples and reject overlapping export builds. Serializer budgets
apply to whole events, including dictionaries, with explicit truncation markers. The
raw selected JSONL bytes remain in the ZIP. Compressed response bytes still use RAM.

Tracker attachment is idempotent; an explicit close path stops the push worker and
releases owned resources. Browser exports abort when their settings dialog closes,
object URLs are cleaned up on failure, and removed nodes release wrapped-log caches.
Long watched values use bounded binary-search text fitting instead of repeated
single-character trimming. See MEMORY_REVIEW.md for evidence, bounds and limitations.
