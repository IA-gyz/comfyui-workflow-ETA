"""Export peak memory, serializer budgets and tracker lifecycle regressions."""
import gc,io,json,tempfile,tracemalloc,types,weakref,zipfile,threading,sys,asyncio
from unittest.mock import patch
from pathlib import Path
from workflow_eta_diagnostics import WorkflowETADiagnostics,WorkflowETAExportBusy,safe
from workflow_eta_tracker import WorkflowETATracker
from workflow_eta_history import WorkflowETAHistory

with tempfile.TemporaryDirectory() as root:
 store=WorkflowETADiagnostics(root+'/diagnostics')
 path=store.directory/'run_memory.jsonl'
 event=json.dumps({'kind':'runtime_inputs','data':{'unused':'x'*8192}})+'\n'
 with path.open('w') as f:
  f.write(json.dumps({'kind':'run_start','start_ts':1,'prompt_id':'memory','data':{}})+'\n')
  for _ in range(2000):f.write(event)
  f.write(json.dumps({'kind':'run_end','data':{'state':'done','elapsed':1,'nodes':{}}})+'\n')
 tracemalloc.start();blob=store.export();current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
 assert peak<8*1024*1024,(current,peak)
 z=zipfile.ZipFile(io.BytesIO(blob));assert z.getinfo('runs/run_memory.jsonl').file_size==path.stat().st_size
 assert z.read('runs/run_memory.jsonl')==path.read_bytes()
 assert 'Outcome: done' in z.read('RUN_SUMMARY.md').decode()
 store.export_gate.acquire()
 try:
  try:store.export()
  except WorkflowETAExportBusy:pass
  else:raise AssertionError('concurrent export was not rejected')
 finally:store.export_gate.release()
 print('PASS: 16 MiB raw export peak traced allocations:',round(peak/1024**2,2),'MiB; one export at a time')
 # Explicit budget limits include dictionaries, keys and repeated strings.
 data={str(i):'x'*10000 for i in range(1000)}
 bounded=safe(data);assert '__workflow_eta_omitted__' in bounded
 assert len(json.dumps(bounded))<5*1024*1024
 cycle={};cycle['self']=cycle;assert 'depth limit' in str(safe(cycle))
 assert safe({'access_token':'private'})['access_token']=='[REDACTED]'
 # A huge set is iterated only up to the limit, never copied in full.
 huge=set(range(100000));tracemalloc.start();sanitized=safe(huge);_,peak_set=tracemalloc.get_traced_memory();tracemalloc.stop()
 assert len(sanitized)==20001 and peak_set<1024*1024
 # Repeated attach must not add wrapper layers or background threads.
 h=WorkflowETAHistory(root+'/h.sqlite');server=types.SimpleNamespace(send_sync=lambda *a:None)
 original=server.send_sync;t=WorkflowETATracker(server,h,verbose=False,start_thread=True)
 t.attach();worker=t._push_thread;wrapper=server.send_sync
 for _ in range(20):t.attach()
 assert t._push_thread is worker and server.send_sync is wrapper
 t.watch_baseline={'large':'x'*100000};t.last_payload={'large':'x'*100000}
 t.close();assert not worker.is_alive() and server.send_sync is original
 assert t.watch_baseline is None and t.last_payload is None and not t.log_entries
 t.close();ref=weakref.ref(t);del t;gc.collect();assert ref() is None
 print('PASS: bounded diagnostics, idempotent attach, stopped worker, released snapshots and collectible tracker')

# A long queue retains the current/baseline snapshots, not every past run.
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/soak.sqlite');clock=[0.]
 g={'n':{'class_type':'Work','inputs':{'width':640}}}
 t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,
   clock=lambda:clock[0],find_prompt_fn=lambda p:(g,['n']),verbose=False,start_thread=False)
 def generate(index):
  t.on_event('execution_start',{'prompt_id':str(index)})
  t.on_event('executing',{'prompt_id':str(index),'node':'n'})
  clock[0]+=1
  t.on_event('execution_success',{'prompt_id':str(index)})
  assert t.run is None and len(t.log_entries)<=250
 tracemalloc.start()
 for index in range(80):generate(index)
 gc.collect();warm=tracemalloc.get_traced_memory()[0]
 for index in range(80,400):generate(index)
 gc.collect();late=tracemalloc.get_traced_memory()[0];tracemalloc.stop()
 assert late-warm<1024*1024,(warm,late)
 t.close()
 print('PASS: 400-generation soak; post-warmup retained Python growth',round((late-warm)/1024,1),'KiB')

from workflow_eta_runtime import install_runtime_observer
from workflow_eta_machine import install_model_observer
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/hooks.sqlite');t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,verbose=False,start_thread=False)
 marker=([],{},False,False)
 async def output(prompt_id,unique_id,obj,input_data_all):return marker
 execution=types.SimpleNamespace(get_output_data=output)
 model_marker=object();mm=types.SimpleNamespace(load_models_gpu=lambda:model_marker)
 with patch.dict(sys.modules,{'execution':execution,'comfy.model_management':mm}):
  assert install_runtime_observer(t,execution) and install_model_observer(t,mm)
  t.close()
  assert execution.get_output_data._workflow_eta_tracker is None and mm.load_models_gpu._workflow_eta_tracker is None
  assert asyncio.run(execution.get_output_data('p','n',object(),{})) is marker
  assert mm.load_models_gpu() is model_marker
 ref=weakref.ref(t);del t;gc.collect();assert ref() is None
 print('PASS: observer ownership released; disabled hooks preserve underlying return identities')
