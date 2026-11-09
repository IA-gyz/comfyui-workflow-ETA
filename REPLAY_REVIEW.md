# Replay of the supplied diagnostics and history

This is an audit of the uploaded older forecasts, not a benchmark of v0.2.18. The export has 100 run files: 91 completed, 4 aborted and 5 incomplete. Another 374 diagnostic files were outside the selection. None of these runs contains the new machine telemetry.

## Conditional node backtest

For 130 executed-node timings of at least one second, an exact earlier history match was available before the held-out run started. Mean absolute error was 5.66s for an ordinary median and 5.39s for a recency-weighted median (eight-sample half life). This is a modest improvement in this subset. There were 195 node cases without matching earlier history. It is not an end-to-end workflow ETA comparison and does not establish the benefit of memory-pressure grouping.

The evaluator opens SQLite read-only and excludes rows whose recording timestamp is at or after the evaluated run start. Recorded forecasts are evaluated as emitted; ComfyUI is not re-executed. A database snapshot predating the final diagnostic runs can omit measurements that the live estimator actually had. See REPLAY_REVIEW.json for full metrics, class breakdowns and definitions.

## Interpretation of whole-run errors

Only initial projections without explicitly unpriced nodes or incomplete graphs enter the full-forecast metric. This stricter completeness rule yields 36 completed runs over a minute, whereas the earlier conversational summary compared all 38 long completed runs. Do not compare those percentages without matching the population. Long runs with detailed timing errors remain a priority; short cached runs must not dominate the accuracy score.

## Next measurement

Use v0.2.18 for representative runs, including different video lengths, step counts and memory/residency states. Export diagnostics again. The resulting machine_sample, model_management and runtime_prediction events will allow memory regimes and observed interval coverage to be evaluated directly. Existing recordings cannot retroactively supply GPU pressure or residency measurements.
