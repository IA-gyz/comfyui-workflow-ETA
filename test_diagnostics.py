import io,json,tempfile,types,zipfile
from unittest.mock import patch
from pathlib import Path
from workflow_eta_diagnostics import WorkflowETADiagnostics,safe
from workflow_eta_tracker import WorkflowETATracker
from workflow_eta_history import WorkflowETAHistory

g={'l':{'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}},
   's':{'class_type':'KSampler','inputs':{'latent_image':['l',0],'steps':4,'seed':99,'cfg':7,'api_key':'private'}},
   'text':{'class_type':'PrimitiveStringMultiline','inputs':{'value':'full prompt '+('x'*2000)}}}
with tempfile.TemporaryDirectory() as path:
 store=WorkflowETADiagnostics(path+'/diagnostics');h=WorkflowETAHistory(path+'/history.sqlite');clock=[0.]
 t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['s']),clock=lambda:clock[0],wall=lambda:1700000000+clock[0],verbose=False,start_thread=False,device='GPU',diagnostics=store,resident_fn=lambda:frozenset())
 t.on_event('execution_start',{'prompt_id':'completed'})
 t.on_event('execution_cached',{'prompt_id':'completed','nodes':['l']})
 t.on_event('executing',{'prompt_id':'completed','node':'s'})
 for i in range(1,5):
  clock[0]=2+i;t.on_event('progress',{'node':'s','value':i,'max':4})
 clock[0]=7;t.on_event('execution_success',{'prompt_id':'completed'})
 files=list(Path(path+'/diagnostics').glob('run_*.jsonl'));assert len(files)==1
 records=[json.loads(line) for line in files[0].read_text().splitlines()]
 start=records[0];assert start['kind']=='run_start'
 assert start['data']['graph']['s']['inputs']['api_key']=='[REDACTED]'
 assert start['data']['graph']['s']['inputs']['seed']==99
 assert len(start['data']['graph']['text']['inputs']['value'])>2000
 assert len([r for r in records if r['kind']=='progress'])==4
 assert any(r['kind']=='node_end' and r['data']['duration']==7 for r in records)
 end=records[-1];assert end['kind']=='run_end' and end['data']['state']=='done'
 assert end['data']['elapsed']==7 and end['data']['nodes']['s']['actual']==7
 assert 'initial_coverage' in end['data']
 assert any(r['kind']=='prediction_snapshot' for r in records)
 samples=[r for r in records if r['kind']=='eta_sample'];assert len(samples)>=2
 # Aborted runs retain the error event and current observed segment, without final-runtime claims.
 clock[0]=10;t.on_event('execution_start',{'prompt_id':'aborted'})
 t.on_event('executing',{'prompt_id':'aborted','node':'s'})
 clock[0]=12;t.on_event('execution_error',{'prompt_id':'aborted','node_id':'s','exception_type':'TestError','exception_message':'test'})
 z=zipfile.ZipFile(io.BytesIO(WorkflowETADiagnostics(path+'/diagnostics').export()))
 assert {'manifest.json','RUN_SUMMARY.md','REVIEW_GUIDE.md','extension_source/workflow_eta_tracker.py'}<=set(z.namelist())
 summary=z.read('RUN_SUMMARY.md').decode();assert 'completed' in summary and 'aborted' in summary
 assert 'Full projection error' in summary and 'Actual minus forecast' in summary
 raw=[json.loads(line) for name in z.namelist() if name.startswith('runs/') for line in z.read(name).splitlines()]
 assert any(r['kind']=='execution_error' and r['data']['exception_type']=='TestError' for r in raw)
 aborted=next(r for r in raw if r['kind']=='run_end' and r['data']['state']=='aborted')
 assert aborted['data']['unfinished_current_node']['observed_elapsed']==2
 # Partial/crashed final line is tolerated; the original bytes are retained.
 active=Path(path+'/diagnostics/run_incomplete.jsonl');active.write_text(json.dumps(start)+'\n{"partial":')
 z=zipfile.ZipFile(io.BytesIO(store.export()));assert 'no terminal record' in z.read('RUN_SUMMARY.md').decode()
 # Recording limits are explicit, including an oversized first event.
 run=types.SimpleNamespace(prompt_id='limited',start_ts=1,graph={},reach=set(),model_map={},node_cold={},video_sources={},step_plans={},shape_plans={},preds={},watch_rows=[])
 with patch('workflow_eta_diagnostics.MAX_RUN_BYTES',10):store.start(run,'GPU')
 assert json.loads(run.diagnostic_file.read_text())['kind']=='recording_limit'
 # File-write errors never interrupt ETA tracking.
 broken=types.SimpleNamespace(start=lambda *a:(_ for _ in ()).throw(OSError('read-only')))
 t.diagnostics=broken;clock[0]=20;t.on_event('execution_start',{'prompt_id':'still-running'})
 assert t.run and t.run.diagnostic_disabled
 h.close()
assert safe({'cookie':'secret','float':float('nan'),'media':'data:image/png;base64,abc'})=={'cookie':'[REDACTED]','float':None,'media':'[media payload omitted]'}
print('PASS: restart-persistent full parameters/ETA-versus-actual records, raw progress, redaction, successful/interrupted/incomplete runs, readable ZIP export, crash-tail tolerance, explicit recording limits and fail-soft diagnostics')
