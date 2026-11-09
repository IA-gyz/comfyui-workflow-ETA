# Parameter tracking in Workflow ETA v0.2.11

Tracking a change means selecting a different history signature or marking a workload uncertain. It does not imply a known timing ratio. Only the sampler scaling cases below estimate across changed configurations.

## Explicit upstream workload controls

These keys are matched case-insensitively and propagated from ancestors to downstream nodes:

| Group | Exact keys |
| --- | --- |
| Dimensions and resolution | `width`, `height`, `w`, `h`, `resolution`, `size`, `target_size`, `megapixels`, `longest_edge`, `shortest_edge` |
| Batch and repetition | `batch_size`, `batch`, `batch_count`, `amount`, `batch_index` |
| Video length | `length`, `num_frames`, `frames`, `frame_count` |
| Resize | `scale_by`, `scale`, `scale_factor`, `upscale_by`, `factor` |
| Crop | `crop`, `crop_width`, `crop_height`, `crop_size` |
| Padding | `padding`, `pad_left`, `pad_right`, `pad_top`, `pad_bottom` |
| Sampling | `steps`, `start_at_step`, `end_at_step`, `denoise`, `sampler_name`, `scheduler`, `cfg`, `add_noise`, `return_with_leftover_noise` |
| Video loading | `frame_load_cap`, `skip_first_frames`, `select_every_nth`, `force_rate`, `force_size`, `max_frames`, `force_fps`, `custom_width`, `custom_height` |

Additionally, any key containing `width`, `height`, `batch`, `resolution`, `scale_factor` or `num_frames` is tracked as an upstream workload control. For node class names containing `resize`, `scale`, `crop`, `pad`, `batch`, `repeat` or `tile`, all scalar settings except `seed`, `noise_seed`, `text` and `prompt`, and all direct linked inputs, enter the workload fingerprint. This includes custom interpolation/mode/rounding controls without needing a fixed key list.

Linked workload controls include the upstream scalar dependency graph, including expression text and primitive values. Their changes can therefore separate history even when the numeric result cannot be resolved. Numeric resolution supports primitive/constant/reroute nodes, boolean `ComfySwitchNode` output 0, and whitelisted bounded `ComfyMathExpression` arithmetic on outputs 0/1. Arbitrary custom nodes are not executed to discover values.

## General node history and context

- Each node's class, node ID, literal inputs, ancestor model filenames and upstream workload controls contribute to history selection. The fallback ladder can still use less-specific history and labels it estimated.
- Generic direct scalar inputs are included, with the exceptions below. For example LoRA strength, scheduler selection or short enum values can separate the affected node's exact history. This does **not** mean every arbitrary upstream scalar change propagates to every descendant: only model filenames and workload controls have that general propagation rule.
- Model filenames with `.safetensors`, `.ckpt`, `.pt`, `.pth`, `.bin`, `.gguf` or `.sft` extensions propagate through model lineage. Replacing weights under the same filename is not reliably detected.
- Device identity, per-node cold/warm heuristic and execution cache events affect matching or remaining-work selection. Cold/warm is inferred from residency and the previous completed run, rather than measuring each model's actual loading cost in advance.
- Live sampler progress updates the ETA independently of parameter matching. Repeated/reset progress is treated conservatively.

### Source inputs and metadata

Image, video and latent loader filenames are found through these aliases: `image`, `images`, `image_path`, `image_file`, `filename`, `file`, `filepath`, `path`, `video`, `video_path`, `latent`, `latent_path`, `file_path`, `input_path`, `image_filename`, `video_filename`, `imagefile`.

Where available, image dimensions/frame count, video dimensions/frame metadata and safe latent-header shape metadata enter the workload fingerprint. Sources that cannot be inspected, random loaders, and folder/batch loaders can remain uncertain. Detection is metadata-based; it is not a pixel/tensor-content comparison.

### Intentionally excluded or limited

`seed`, `noise_seed`, `control_after_generate` and `control_before_generate` are excluded from normal timing signatures and sampler comparison families. A different seed alone is not expected to imply a timing change. Fixed versus randomized seeds can still affect what ComfyUI caches; execution cache events determine which nodes remain.

Generic direct text strings longer than 64 characters, linked inputs and structured values are omitted from the basic per-node literal hash. Workload dependency fingerprints and stricter sampler families can retain such values. Sampler comparison families retain other upstream settings and prompts, excluding only the comparison axis and the listed seed controls. The extension does not call arbitrary nodes' `IS_CHANGED` methods or reproduce all ComfyUI cache logic.

## Changes that support duration estimation across configurations

| Change | Estimation | Supported scope |
| --- | --- | --- |
| Step count only | Measured startup + `(new effective steps − 1) × measured step time` + measured tail | `KSampler`, `KSamplerAdvanced`; `SamplerCustom` / `SamplerCustomAdvanced` with `BasicScheduler`, `KarrasScheduler`, `ExponentialScheduler`, `PolyexponentialScheduler`, `BetaSamplingScheduler` or `VPScheduler` |
| Resolution only, same aspect ratio | Empirical interpolation over measured pixel counts, with tightly bounded extrapolation | Explicit `EmptyLatentImage`, `EmptySD3LatentImage`, `LatentUpscale`, and supported `ImageScale → VAEEncode` paths |
| Batch size / repetition only | Empirical interpolation over measured batch sizes, with tightly bounded extrapolation | Supported latent paths including `RepeatLatentBatch` |
| Generated video frame count only | Local nonlinear power-curve interpolation of measured startup, per-step cost and tail; tightly bounded extrapolation | `MiniMaxH3ReferenceToVideo` output 1 into `SamplerCustomAdvanced` |

Geometry and frame calibration require at least two stable completed measurements at each of two distinct axis values. Other configuration, device and cold/warm state must match. Unsupported simultaneous axis changes are not combined automatically. Interrupted/failed runs do not become completed-run calibration data. Old history without the required calibration columns cannot supply those measurements.

Only sampler duration is scaled by these mechanisms. CFG, denoise, sampler algorithm, scheduler type, model changes, crop/padding changes, playback FPS and arbitrary custom controls have no general timing multiplier. They may change the signature, select other matching history, or cause an estimated fallback. For a supported H3 frame plan without sufficient calibration, the sampler duration stays unknown and ETA is a lower bound until live timing becomes available.

The v0.2.6 watched-data inspector exposes these actual fingerprint fields, plus strict sampler-family inputs, and compares them with the last completed run in the current server session. The timing tab reports differences at node level rather than attributing a measured cost to individual changed settings.

Pixaroma loaded-frame outputs and equality/lazy-switch controls are supported in v0.2.8, including generated-length controls that follow the reference video. Source duration, effective loaded frames and FPS appear in watched data. FPS resampling and missing exact counts remain explicitly uncertain; a changed reference length is not assumed to imply a linear runtime change.

As of v0.2.11, the H3/Pixaroma sampler can learn a nonlinear curve across different reference filenames using effective reference frames, with generated frames either fixed or following references 1:1. Source dimensions/FPS and non-temporal configuration remain matched. This is a separate indexed calibration family; exact history signatures still retain each file. See README for measurement requirements and bounded extrapolation.

## Runtime observations

The Runtime group shows bounded resolved input tensor storage shapes and dtype. IMAGE outputs expose declared image batch dimensions/counts in diagnostic output events. Scalar declared output names/values are recorded without reading tensor scalars. Arbitrary opaque objects are represented by type, not introspected or executed. Exact runtime timing matching is separate from static signature matching and requires matching recipe, device, node, state and input metadata. Pending task/expansion scheduling and truncated observations are excluded from runtime calibration.

## Machine-state context (v0.2.18)

Runtime matching adds coarse allocator-aware GPU pressure, RAM pressure, loaded-model class/device/size/residency, VRAM mode and software/attention options. Raw counters are recorded separately from these matching buckets. Checkpoint identity is not inferred from a model class/size pair. Pre-first-progress model-management duration and later machine samples support diagnosis of initialization and offloading. Forecast errors are committed only for completed runs and are used prospectively for empirical uncertainty envelopes.
