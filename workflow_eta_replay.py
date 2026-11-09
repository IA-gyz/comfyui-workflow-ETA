"""Read-only forecast audit and node backtest. Never trains on held-out outcomes.

python workflow_eta_replay.py diagnostics.zip --history history.sqlite --output review.json
"""
import argparse
import json
import math
import sqlite3
import statistics
import zipfile
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import quote


def valid(value):return type(value) in (int,float) and math.isfinite(value)


def metrics(pairs):
    if not pairs:return {'n':0}
    errors=[pred-actual for pred,actual in pairs];absolute=sorted(abs(e) for e in errors)
    return {'n':len(pairs),'mae_seconds':statistics.mean(absolute),'bias_seconds':statistics.mean(errors),
            'median_absolute_percentage_error':statistics.median(abs(p-a)/max(a,1.)*100 for p,a in pairs),
            'p90_absolute_error_seconds':absolute[min(len(absolute)-1,math.ceil(.9*len(absolute))-1)]}


def recent_median(values):
    weighted=sorted((v,2**(-index/8)) for index,v in enumerate(values));half=sum(w for v,w in weighted)/2
    total=0
    for value,weight in weighted:
        total+=weight
        if total>=half:return value


def review(archive,history=None):
    runs=[];bad_lines=0;manifest={}
    with zipfile.ZipFile(archive) as z:
        if 'manifest.json' in z.namelist():manifest=json.loads(z.read('manifest.json'))
        selected=[i for i in z.infolist() if i.filename.startswith('runs/') and i.filename.endswith('.jsonl')]
        if len(selected)>1000 or sum(i.file_size for i in selected)>128*1024*1024:raise ValueError('Diagnostic replay size limit exceeded')
        for name in z.namelist():
            if not name.startswith('runs/') or not name.endswith('.jsonl'):continue
            if z.getinfo(name).file_size>70*1024*1024:raise ValueError('Run file exceeds diagnostic size limit')
            events=[]
            for line in z.read(name).splitlines():
                try:events.append(json.loads(line))
                except ValueError:bad_lines+=1
            if events:runs.append(events)
    outcomes=Counter();initial=[];heavy=[];midpoints=[];coverage=[];node_saved=[];median_pairs=[];recent_pairs=[]
    node_classes=defaultdict(list);cutoffs=[];missing_history=0;telemetry_runs=0
    db=None
    if history:
        db=sqlite3.connect('file:'+quote(str(Path(history).resolve()),safe='/')+'?mode=ro',uri=True)
    try:
        for events in runs:
            start=next((e for e in events if e.get('kind')=='run_start'),None)
            terminal=next((e for e in reversed(events) if e.get('kind')=='run_end'),None)
            state=(terminal or {}).get('data',{}).get('state','incomplete');outcomes[state]+=1
            telemetry_runs+=any(e.get('kind')=='machine_sample' for e in events)
            if state!='done':continue
            end=terminal['data'];actual=end.get('elapsed');forecast=end.get('initial_expected_total')
            if not valid(actual) or actual<=0:continue
            complete=not ((end.get('initial_coverage') or {}).get('graph_incomplete') or (end.get('initial_coverage') or {}).get('unpriced_nodes'))
            if complete and valid(forecast):
                initial.append((forecast,actual))
                if actual>=60:heavy.append((forecast,actual))
            samples=[e for e in events if e.get('kind')=='eta_sample' and valid(e.get('elapsed')) and 0<=e['elapsed']<actual]
            if samples:
                sample=min(samples,key=lambda e:abs(e['elapsed']-actual/2));p=sample.get('data',{})
                full=(p.get('eta_detail') or {}).get('provisional_remaining')
                if actual>=60 and valid(full):midpoints.append((full,actual-sample['elapsed']))
                for sample in samples:
                    p=sample.get('data',{});low=p.get('low');high=p.get('high')
                    if p.get('lower_bound') or p.get('expected_total_incomplete'):continue
                    if valid(low) and valid(high):coverage.append(low<=actual-sample['elapsed']<=high)
            if not start:continue
            source=start.get('data',{});device=(source.get('environment') or {}).get('device','')
            cutoff=start.get('start_ts')
            for event in events:
                if event.get('kind')!='node_end':continue
                data=event.get('data',{});duration=data.get('duration');meta=data.get('meta')
                if not valid(duration) or duration<1 or not meta:continue
                forecast=(data.get('prediction') or {}).get('seconds')
                if valid(forecast):node_saved.append((forecast,duration));node_classes[meta[0]].append((forecast,duration))
                if db is None or not valid(cutoff):continue
                cold=bool((source.get('cold_by_node') or {}).get(str(data.get('node')),False))
                rows=db.execute('SELECT duration,ts FROM node_runs WHERE class_type=? AND sig=? AND node_id=? AND device=? AND cold=? AND ts<? ORDER BY ts DESC,id DESC LIMIT 80',
                    (meta[0],meta[1],str(data.get('node')),device,int(cold),cutoff)).fetchall()
                values=[v for v,ts in rows if valid(v) and v>0]
                if not values:missing_history+=1;continue
                assert all(ts<cutoff for v,ts in rows)
                cutoffs.extend(ts for v,ts in rows)
                median_pairs.append((statistics.median(values),duration));recent_pairs.append((recent_median(values),duration))
    finally:
        if db:db.close()
    return {'scope':{'run_files':len(runs),'outcomes':dict(outcomes),'malformed_lines':bad_lines,
                'older_files_not_selected':manifest.get('older_files_not_selected'),'runs_with_machine_samples':telemetry_runs},
            'recorded_initial_full':metrics(initial),'recorded_initial_over_60_seconds':metrics(heavy),
            'recorded_midpoint_full_remaining_over_60_seconds':metrics(midpoints),
            'recorded_known_interval_coverage':{'n':len(coverage),'fraction':statistics.mean(coverage) if coverage else None,
                'note':'Only non-partial intervals; sample-weighted, not a confidence guarantee'},
            'recorded_node_forecasts_over_1_second':metrics(node_saved),
            'recorded_nodes_by_class':{k:metrics(v) for k,v in sorted(node_classes.items())},
            'historical_node_backtest':{'exact_median':metrics(median_pairs),'recent_weighted_median':metrics(recent_pairs),
                'missing_matches':missing_history,'cutoff':'strictly before each held-out run start; read-only DB; no outcome insertion',
                'note':'Conditional on executed nodes; not a full-workflow queue-time backtest. DB snapshot may omit later measurements.'},
            'limitations':['Recorded forecasts are evaluated as emitted; this does not rerun old ComfyUI code.',
                'No machine-state benefit can be quantified for runs lacking telemetry.',
                'Percentages use denominator >=1 second; tiny runs are separate from the >=60s workload metric.',
                'Cached nodes, aborted runs and missing terminal records are not successful node-duration training.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('diagnostics');parser.add_argument('--history');parser.add_argument('--output')
    args=parser.parse_args();result=review(args.diagnostics,args.history);text=json.dumps(result,indent=2)
    if args.output:Path(args.output).write_text(text+'\n')
    else:print(text)

if __name__=='__main__':main()
