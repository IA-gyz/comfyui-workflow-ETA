import hashlib,json,os,sys,tempfile,threading,types,zipfile
from unittest.mock import patch
from workflow_eta_machine import snapshot,context,install_model_observer
from workflow_eta_replay import review
from workflow_eta_history import WorkflowETAHistory

class CUDA:
 def __init__(self):self.touched=False
 def is_initialized(self):return False
 def mem_get_info(self,*args):self.touched=True;raise AssertionError('must not initialize CUDA')
cuda=CUDA()
with patch.dict(sys.modules,{'torch':types.SimpleNamespace(cuda=cuda,__version__='test')}):
 data=snapshot();assert not cuda.touched and 'gpu' not in data
 assert context(data)['gpu_pressure']=='unknown'
base={'software':{'torch':'test'},'gpu':{'available_including_allocator':80,'total':100},
      'models':[{'class':'M','device':'cuda:0','total_bytes':100,'resident_bytes':100}]}
pressure=dict(base,gpu={'available_including_allocator':2,'total':100})
partial=dict(base,models=[dict(base['models'][0],resident_bytes=50)])
assert context(base)!=context(pressure) and context(base)!=context(partial)
# Loader return values and exceptions are untouched; wrappers never load extra models.
class Tracker:
 lock=threading.RLock();run=types.SimpleNamespace(prompt_id='p',cur={'node':'n'})
 def __init__(self):self.calls=[]
 def observe_model_management(self,*args):self.calls.append(args)
t=Tracker();marker=object();mm=types.SimpleNamespace(load_models_gpu=lambda *a,**k:marker)
assert install_model_observer(t,mm);wrapped=mm.load_models_gpu;install_model_observer(t,mm)
assert wrapped is mm.load_models_gpu and mm.load_models_gpu([]) is marker
assert len(t.calls)==1 and t.calls[0][:2]==('p','n')
def fail(*args):raise ValueError('original failure')
mm=types.SimpleNamespace(load_models_gpu=fail);install_model_observer(t,mm)
try:mm.load_models_gpu([])
except ValueError as e:assert str(e)=='original failure'
else:raise AssertionError('execution error swallowed')
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/h.sqlite')
 # Held-out audit: an enormous future measurement must not affect predictions.
 with h._lock,h._db:
  h._db.execute("INSERT INTO node_runs(ts,class_type,sig,node_id,device,cold,duration) VALUES(1,'C','s','n','gpu',0,10)")
  h._db.execute("INSERT INTO node_runs(ts,class_type,sig,node_id,device,cold,duration) VALUES(100,'C','s','n','gpu',0,99999)")
 events=[dict(kind='run_start',start_ts=50,data={'environment':{'device':'gpu'},'cold_by_node':{'n':False}}),
   dict(kind='node_end',elapsed=12,data={'node':'n','duration':12,'meta':['C','s',''],'prediction':{'seconds':10}}),
   dict(kind='run_end',data={'state':'done','elapsed':12,'initial_expected_total':10,'initial_coverage':{}})]
 p=root+'/d.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('runs/run_test.jsonl','\n'.join(json.dumps(e) for e in events))
 h.close();before=open(root+'/h.sqlite','rb').read()
 r=review(p,root+'/h.sqlite');assert r['historical_node_backtest']['exact_median']['mae_seconds']==2
 assert open(root+'/h.sqlite','rb').read()==before
 h=WorkflowETAHistory(root+'/h.sqlite')
 # Rolling errors pre-exist the next forecast; empirical envelope expands for systematic underestimation.
 with h._lock,h._db:
  for i in range(10):h._db.execute("INSERT INTO eta_errors(ts,class_type,recipe,node_id,device,cold,predicted,actual) VALUES(?,?,?,?,?,?,?,?)",(i,'C','recipe','n','gpu',0,10,30))
 pred={'seconds':10,'low':9,'high':11}
 h.calibrate_runtime(pred,'C','recipe',False,'gpu','n')
 assert pred['high']>=30 and pred['interval_calibration']['samples']==10
 h.close()
print('PASS: no CUDA initialization, pressure/residency partitioning, transparent model-load hook, read-only no-future replay and empirical interval calibration')
# Matching timings from another pressure/residency regime must not remain exact.
from workflow_eta_tracker import WorkflowETATracker
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/h.sqlite');g={'n':{'class_type':'Consumer','inputs':{}}}
 shape={'complete':True,'has_tensors':True,'inputs':{'image':{'length':1,'items':[{'tensor_shape':[12,64,64,3],'dtype':'float16'}]}}}
 def start(machine,pid):
  clock=[0.];t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['n']),clock=lambda:clock[0],verbose=False,start_thread=False,device='gpu')
  t.machine_fn=lambda:machine;t._on_start({'prompt_id':pid});t._on_executing({'prompt_id':pid,'node':'n'});t.observe_runtime(pid,'n',shape)
  return t,clock
 for i in range(3):
  t,c=start(base,str(i));c[0]=10.;t._on_success({'prompt_id':str(i)})
 t,c=start(pressure,'tight');assert t.run.preds['n']['tier']=='machine-unmatched',t.run.preds['n']
 t._discard('test');t,c=start(base,'normal');assert t.run.preds['n']['tier']=='runtime-exact'
 t._discard('test');h.close()
print('PASS: end-to-end machine regimes separate runtime timing matches')
