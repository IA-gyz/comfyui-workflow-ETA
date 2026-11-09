import copy,tempfile,types
from unittest.mock import patch
from workflow_eta_tracker import WorkflowETATracker
from workflow_eta_history import WorkflowETAHistory

def graph(name):
 return {
 'v':{'class_type':'PixaromaLoadVideo','inputs':{'video':name,'max_frames':0,'force_fps':0,'skip_first_frames':0,'custom_width':0,'custom_height':0}},
 'm':{'class_type':'UNETLoader','inputs':{'unet_name':'h3.safetensors'}},
 'h':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':['v',2],'width':640,'height':384,'ref_videos.ref_video_0':['v',0],'model':['m',0]}},
 'steps':{'class_type':'BasicScheduler','inputs':{'steps':8,'scheduler':'simple','denoise':1}},
 's':{'class_type':'SamplerCustomAdvanced','inputs':{'sigmas':['steps',0],'latent_image':['h',1]}}}

def meta(frames,w=1920):return {'frames':frames,'width':w,'height':1080,'frame_rate':'24/1','duration':str(frames/24),'uncertain_frames':False}
with tempfile.TemporaryDirectory() as path:
 h=WorkflowETAHistory(path+'/h.sqlite')
 def start(name,frames,g=None,device='gpu',w=1920):
  g=g or graph(name);clock=[0.]
  with patch('workflow_eta_tracker._workflow_eta_video_info',return_value=meta(frames,w)):
   t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['s']),clock=lambda:clock[0],verbose=False,start_thread=False,device=device,resident_fn=lambda:frozenset())
   t._on_start({'prompt_id':name})
  return t,clock
 def record(name,frames):
  t,c=start(name,frames);t._on_executing({'node':'s','prompt_id':name})
  rate=(frames/120)**2
  for i in range(1,9):c[0]=2+(i-1)*rate;t._on_progress({'node':'s','value':i,'max':8})
  c[0]+=.1;t._on_success({'prompt_id':name})
 record('a.mp4',72)
 t,c=start('b.mp4',240);assert t.run.preds['s']['tier']!='reference-learned'
 record('b.mp4',96);record('c.mp4',120)
 t,c=start('new-ten-seconds.mp4',240);p=t.run.preds['s']
 assert p['tier']=='reference-learned' and p['extrapolated'],p
 assert abs(p['seconds']-(2+7*4+.1))<1e-8,p
 assert p['seconds']>2*(2+7+.1)
 assert t.run.shape_plans['s']['reference_frames']==240
 t._on_cached({'prompt_id':'new-ten-seconds.mp4','nodes':['v','m','h','steps']})
 assert t.last_payload['workload_scaling']=='reference'
 # Every non-temporal spatial/model/config key must remain in the family.
 for key,value in [('width',768),('model',['other',0])]:
  g=graph('different.mp4');g['h']['inputs'][key]=value
  if key=='model':g['other']={'class_type':'UNETLoader','inputs':{'unet_name':'different.safetensors'}}
  t,c=start('different.mp4',240,g);assert t.run.preds['s']['tier']!='reference-learned'
 for mode in ('fps','steps','fixed_output','skip'):
  g=graph('different.mp4')
  if mode=='fps':g['v']['inputs']['force_fps']=30
  elif mode=='steps':g['steps']['inputs']['steps']=4
  elif mode=='fixed_output':g['h']['inputs']['length']=100
  else:g['v']['inputs']['skip_first_frames']=1
  t,c=start('different.mp4',240,g);assert t.run.preds['s']['tier']!='reference-learned',mode
 t,c=start('resolution.mp4',240,w=1280);assert t.run.preds['s']['tier']!='reference-learned'
 t,c=start('device.mp4',240,device='other');assert t.run.preds['s']['tier']!='reference-learned'
 t,c=start('too-far.mp4',1000);assert t.run.preds['s']['tier']!='reference-learned'
 h.close();h=WorkflowETAHistory(path+'/h.sqlite')
 t,c=start('after-restart.mp4',240);assert t.run.preds['s']['tier']=='reference-learned';h.close()
print('PASS: cross-file nonlinear reference learning, three individual batch measurements, 5s->10s superlinear extrapolation, insufficient-data/distant-target rejection, generated/reference coupling, spatial/model/steps/FPS/trim/device isolation and persisted migration')
