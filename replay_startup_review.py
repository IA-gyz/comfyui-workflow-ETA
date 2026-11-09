import sys,json,zipfile,sqlite3,tempfile,types,copy
from unittest.mock import patch
import argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
parser=argparse.ArgumentParser(description='Read-only chronological startup forecast review; no node execution')
parser.add_argument('diagnostics');parser.add_argument('history');parser.add_argument('--output',required=True)
parser.add_argument('--magik-float-int-schema',action='store_true',help='Use FLOAT/INT Magik.Float schema from the reviewed test environment')
args=parser.parse_args()
from workflow_eta_tracker import WorkflowETATracker
if args.magik_float_int_schema:sys.modules['nodes']=types.SimpleNamespace(NODE_CLASS_MAPPINGS={'Magik.Float':type('Float',(),{'RETURN_TYPES':('FLOAT','INT')})})
from workflow_eta_history import WorkflowETAHistory
z=zipfile.ZipFile(args.diagnostics)
assert sum(i.file_size for i in z.infolist())<128*1024*1024, 'Archive too large'
runs=sorted(([json.loads(l) for l in z.read(n).splitlines()] for n in z.namelist() if n.startswith('runs/')),key=lambda r:r[0]['start_ts'])
with tempfile.TemporaryDirectory() as root:
 p=root+'/h.sqlite';src=sqlite3.connect(Path(args.history).resolve().as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(p);src.backup(dst);src.close()
 for table in ('node_runs','sampler_runs','runtime_runs','runs','eta_errors'):dst.execute('DELETE FROM '+table+' WHERE ts>=?',(runs[0][0]['start_ts'],))
 dst.commit();dst.close();h=WorkflowETAHistory(p);results=[]
 for i,r in enumerate(runs):
  start=r[0]['data'];end=next(x['data'] for x in r if x['kind']=='run_end');g=start['graph'];device=start['environment']['device']
  t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,device=device,clock=lambda:0,wall=lambda:r[0]['start_ts'],find_prompt_fn=lambda p:(g,start['outputs_to_execute']),resident_fn=lambda:frozenset({'resident'}),verbose=False,start_thread=False)
  t.machine_fn=lambda:start['machine_at_start'];t.last_models=frozenset(m for values in start['model_lineage'].values() for m in values)
  with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value=copy.deepcopy(start['video_metadata'])):
   t._on_start({'prompt_id':r[0]['prompt_id']})
  cached=next((x['data'] for x in r if x['kind']=='execution_cached'),None)
  if cached:t._on_cached(cached)
  pred=t.run.preds.get('469');t._announce(t.run);payload=t._workflow_eta(t.run,0)
  result=dict(run=i+1,actual=end['elapsed'],old_initial=end['initial_expected_total'],new_initial=t._eta_explanation(t.run,0)['provisional_remaining'],sampler_actual=end['nodes']['469']['actual'],sampler_prediction=pred)
  results.append(result);print(json.dumps(result))
  records=copy.deepcopy(end['completed_node_records'])
  for rec in records:rec['cold']=start['cold_by_node'].get(rec['node'],False)
  with patch('workflow_eta_history.time.time',return_value=r[0]['start_ts']+end['elapsed']):
   h.record_completed_run(records,r[0]['prompt_id'],end['elapsed'],len(records),0,False,device)
 h.close()
open(args.output,'w').write(json.dumps({'scope':'queue-time forecasts only; strict chronological history; recorded graph and headers, no live execution','runs':results},indent=2))
