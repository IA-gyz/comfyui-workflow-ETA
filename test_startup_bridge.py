"""Queue-time frame estimates can reuse compatible measured histories honestly."""
import tempfile,types,sys,copy
from unittest.mock import patch
from workflow_eta_tracker import WorkflowETATracker
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_runtime import describe_inputs,scaling_inputs

class Tensor:
 def __init__(self,shape):self.shape=shape;self.dtype='float16'
 def cpu(self):raise AssertionError('tensor copy')
 def item(self):raise AssertionError('tensor read')
class NestedTensor:
 def __init__(self,parts):self.tensors=parts
 def unbind(self):raise AssertionError('must inspect stored metadata only')
with patch.dict(sys.modules,{'torch':types.SimpleNamespace(Tensor=Tensor),'comfy.nested_tensor':types.SimpleNamespace(NestedTensor=NestedTensor)}):
 n=NestedTensor([Tensor((1,24,15,14,24)),Tensor((1,32,2,120))])
 m=describe_inputs({'latent_image':[{'samples':n}],'sigmas':[Tensor((5,))]})
 assert m['complete'] and m['has_tensors']
 parts=m['inputs']['latent_image']['items'][0]['samples']['nested_tensors']
 assert len(parts)==2 and parts[1]['tensor_shape']==[1,32,2,120]
 assert scaling_inputs(m)['steps']==4 and scaling_inputs(m)['axis'] is None
 assert not describe_inputs({'samples':NestedTensor([n]*17)})['complete']

with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/h.sqlite')
 g={'v':{'class_type':'MagikVideoLoader','inputs':{'video':'a.mp4'}},
    'h':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':['v',2],'width':640,'height':384,'ref_videos.ref_video_0':['v',0]}},
    'q':{'class_type':'BasicScheduler','inputs':{'steps':4,'scheduler':'simple','denoise':1}},
    's':{'class_type':'SamplerCustomAdvanced','inputs':{'latent_image':['h',1],'sigmas':['q',0]}}}
 def start(info,machine=None):
  t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,device='gpu',find_prompt_fn=lambda p:(g,['s']),verbose=False,start_thread=False)
  t.machine_fn=lambda:machine
  with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value={'v':info}):t._on_start({'prompt_id':'p'})
  return t
 raw={'width':1920,'height':1080,'frames':60,'frame_rate':'25/1'}
 t=start(raw);key=t._runtime_video_context(t.run,'s');assert key
 for i in range(2):
  h._db.execute('INSERT INTO runtime_runs (class_type,node_id,device,cold,frame_key,machine,shape_key,effective_frames,first,rate,tail) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
   ('SamplerCustomAdvanced','s','gpu',0,key,'null','exact-shape',60,32,28,1))
 h._db.commit()
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 p=t.run.preds['s'];assert p['tier']=='runtime-video-profile' and p['approximate'],p
 assert p['calibration']['frame_count_estimated'] and p['low']<p['seconds']<p['high']
 g['q']['inputs']['scheduler']='different'
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 assert t.run.preds['s']['tier']!='runtime-video-profile'
 g['q']['inputs']['scheduler']='simple';g['h']['inputs']['width']=1024
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 assert t.run.preds['s']['tier']!='runtime-video-profile'
 h.close()
print('PASS: startup approximate/exact bridge, graph/config separation, bounded nested video+audio shape observation without tensor reads')

# Different software/mode is never treated as a mere memory-state change.
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/h.sqlite')
 machine={'software':{'torch':'A'},'vram_mode':'normal','execution_options':{}}
 fake=types.SimpleNamespace(shape_plans={'s':{'frames':61}},meta={'s':('SamplerCustomAdvanced','','')},node_cold={'s':False},runtime_machine={},machine_start=machine)
 t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,verbose=False,start_thread=False)
 with patch.object(h,'runtime_video_contexts',return_value=[('k',dict(machine,software={'torch':'B'}))]),patch.object(h,'predict_runtime_video_initial',side_effect=AssertionError('incompatible software')):
  assert t._project_video_history(fake,'s',4) is None
 # Rich observations replace opaque representations without mixing their shapes.
 for shape,meta in [('old','{}'),('new','{"nested_tensors": []}'),('new','{"nested_tensors": []}')]:
  h._db.execute('INSERT INTO runtime_runs (class_type,node_id,device,cold,frame_key,shape_key,effective_frames,first,rate,tail,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
   ('SamplerCustomAdvanced','s','gpu',0,'k',shape,60,32,28,1,meta))
 h._db.commit()
 p=h.predict_runtime_video_initial('SamplerCustomAdvanced','k',60,4,False,'gpu','s')
 assert p is not None and p['n']==2 and p['seconds']==117
 h.close()
print('PASS: software mismatch rejected; nested observation schema coexists with legacy history')
