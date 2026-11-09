import tempfile,types,math,copy
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_runtime import scaling_inputs,fingerprint
from workflow_eta_tracker import WorkflowETATracker

def metadata(steps,time=5,width=80):
 return {'complete':True,'has_tensors':True,'inputs':{
  'sigmas':{'length':1,'items':[{'tensor_shape':[steps+1],'dtype':'float32'}]},
  'latent_image':{'length':1,'items':[{'samples':{'tensor_shape':[1,16,time,48,width],'dtype':'float16'}}]}}}

def plan(steps,time=5,width=80):
 p=scaling_inputs(metadata(steps,time,width));p.update(family='same-model-and-recipe',frame_key='same-spatial-source',effective_frames=time*24);return p

def record(h,steps,time,rate=None):
 rate=rate or (time/5)**2;p=plan(steps,time);m=metadata(steps,time)
 rec={'meta':('SamplerCustomAdvanced','static-'+str((steps,time)),'h3'), 'node':'s','duration':2+steps*rate+.2,
      'sampler':(steps,2+rate,rate),'cold':False,'runtime_tail':.2,'runtime_progress_valid':True,
      'runtime':dict(recipe='exact-recipe-'+str(steps),input_sig=fingerprint(m),metadata=m,scaling=p)}
 h.record_completed_run([rec],str((steps,time)),rec['duration'],1,0,False,'gpu')
with tempfile.TemporaryDirectory() as tmp:
 h=WorkflowETAHistory(tmp+'/h.sqlite');record(h,6,5)
 p=h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,5),False,'gpu','s')
 assert p['tier']=='runtime-step-scaled' and abs(p['seconds']-10.2)<1e-8,p
 assert abs(p['seconds']-8.2*8/6)>0.5
 sparse=h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,10),False,'gpu','s')
 assert sparse['tier']=='runtime-video-provisional' and sparse['low']<sparse['seconds']<sparse['high']
 assert h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,20),False,'gpu','s') is None
 assert h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,5,120),False,'gpu','s') is None
 assert h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,5),False,'otherGPU','s') is None
 for time in (7,9):record(h,6,time)
 learned=h.predict_runtime_scaling('SamplerCustomAdvanced',plan(8,18),False,'gpu','s')
 expected=2+8*(18/5)**2+.2
 assert learned['tier']=='runtime-video-learned' and abs(learned['seconds']-expected)<1e-7,learned
 assert learned['calibration']['power_slope']>1.9
 initial=h.predict_runtime_video_initial('SamplerCustomAdvanced','same-spatial-source',18*24,8,False,'gpu','s')
 assert initial['tier']=='runtime-video-profile' and abs(initial['seconds']-expected)<1e-7,initial
 assert h.predict_runtime_video_initial('SamplerCustomAdvanced','different-resolution',432,8,False,'gpu','s') is None
 h.close()
# End-to-end: runtime sigma count enables step scaling, leaving ordinary exact
# runtime matches first. No tensor contents or sigma values are needed.
with tempfile.TemporaryDirectory() as tmp:
 h=WorkflowETAHistory(tmp+'/h.sqlite')
 def graph(steps):return {'q':{'class_type':'BasicScheduler','inputs':{'steps':steps,'scheduler':'simple','denoise':1}},
  's':{'class_type':'SamplerCustomAdvanced','inputs':{'sigmas':['q',0]}}}
 def start(steps):
  clock=[0.];g=graph(steps)
  t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['s']),clock=lambda:clock[0],verbose=False,start_thread=False,device='gpu')
  t._on_start({'prompt_id':str(steps)});t._on_executing({'node':'s','prompt_id':str(steps)})
  t.observe_runtime(str(steps),'s',metadata(steps));return t,clock
 t,c=start(6)
 for i in range(1,7):c[0]=2+i;t._on_progress({'node':'s','value':i,'max':6})
 c[0]=8.2;t._on_success({'prompt_id':'6'})
 t,c=start(8);assert t.run.preds['s']['tier']=='runtime-step-scaled',t.run.preds['s']
 assert abs(t.run.preds['s']['seconds']-10.2)<1e-8,t.run.preds['s']
 t._discard('test');h.close()
print('PASS: 6->8 step overhead preservation, sparse-video provisional ratio, nonlinear video curve, queue-time projection, spatial/device separation and runtime tracker integration')
# Reference duration is independent of output length: opaque guider objects must
# not cause an exact match when a different reference clip feeds identical latents.
from unittest.mock import patch
with tempfile.TemporaryDirectory() as tmp:
 h=WorkflowETAHistory(tmp+'/h.sqlite')
 def start_reference(frames):
  g={'v':{'class_type':'PixaromaLoadVideo','inputs':{'video':str(frames)+'.mp4'}},
     'h':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':96,'width':640,'height':384,'ref_videos.ref_video_0':['v',0]}},
     'q':{'class_type':'BasicScheduler','inputs':{'steps':6,'scheduler':'simple','denoise':1}},
     's':{'class_type':'SamplerCustomAdvanced','inputs':{'sigmas':['q',0],'latent_image':['h',1]}}}
  c=[0.];t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['s']),clock=lambda:c[0],verbose=False,start_thread=False,device='gpu')
  with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value={'v':{'frames':frames,'frame_rate':'24/1','width':1920,'height':1080,'duration':str(frames/24),'uncertain_frames':False}}):
   t._on_start({'prompt_id':str(frames)})
  t._on_executing({'prompt_id':str(frames),'node':'s'});t.observe_runtime(str(frames),'s',metadata(6))
  return t,c
 t,c=start_reference(120)
 for i in range(1,7):c[0]=2+i;t._on_progress({'node':'s','value':i,'max':6})
 c[0]=8.2;t._on_success({'prompt_id':'120'})
 t,c=start_reference(240)
 assert t.run.preds['s']['tier']=='runtime-video-provisional',t.run.preds['s']
 assert t.run.runtime['s']['scaling']['axis']=='reference_frames'
 assert t.run.runtime['s']['scaling']['axis_value']==240
 assert t.run.runtime['s']['scaling']['effective_frames']==240
 t._discard('test');h.close()
print('PASS: changing reference length with fixed output/identical latent shape does not reuse an exact timing')
