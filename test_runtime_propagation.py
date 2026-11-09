"""Real scalar outputs refine pending work without rereading cursor-selected files."""
import tempfile,types
from unittest.mock import patch
from workflow_eta_tracker import WorkflowETATracker,_workflow_eta_literal,_workflow_eta_loaded_video_info
from workflow_eta_history import WorkflowETAHistory

def graph():return {
 'v':{'class_type':'MagikVideoLoader','inputs':{'video':'current.mp4','force_fps':12}},
 'unknown':{'class_type':'ThirdPartyCalculation','inputs':{}},
 'h':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':['v',2],'width':['v',4],'height':['v',5],'ref_videos.ref_video_0':['v',0]}},
 'q':{'class_type':'BasicScheduler','inputs':{'steps':['unknown',0],'scheduler':'simple','denoise':1}},
 's':{'class_type':'SamplerCustomAdvanced','inputs':{'latent_image':['h',1],'sigmas':['q',0]}}}

def outputs(values,count=1):return {'complete':True,'has_tensors':False,'scalar_outputs':[
 {'slot':slot,'item':0,'count':count,'value':value} for slot,value in values.items()]}
with tempfile.TemporaryDirectory() as tmp:
 hist=WorkflowETAHistory(tmp+'/h.sqlite');clock=[0.];g=graph()
 t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),hist,clock=lambda:clock[0],find_prompt_fn=lambda p:(g,['s']),verbose=False,start_thread=False)
 with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value={'v':{'width':1920,'height':1080,'frames':240,'frame_rate':'24/1','uncertain_frames':False}}):
  t._on_start({'prompt_id':'p'})
 assert 's' not in t.run.shape_plans and 's' not in t.run.step_plans
 t._on_executing({'prompt_id':'p','node':'v'});old_meta=t.run.meta['v'];t0=t.run.cur['t0']
 # Runtime outputs override an unresolved forced-FPS count, even for loader names
 # unknown to the static resolver (handled separately by the scalar cache).
 with patch('workflow_eta_tracker._workflow_eta_collect_videos',side_effect=AssertionError('must not reread loop cursor')):
  t.observe_runtime_output('p','v',outputs({2:120,3:12.,4:640,5:384,6:10.}))
 assert t.run.cur['t0']==t0 and t.run.meta['v']==old_meta
 assert _workflow_eta_literal(g,['v',2],video_info=t.run.video_sources)==120
 assert _workflow_eta_loaded_video_info(g,'v',t.run.video_sources)['width']==640
 clock[0]=1;t._on_executing({'prompt_id':'p','node':'unknown'})
 done_meta=t.run.done[0]['meta'];initial=t.run.initial
 with patch('workflow_eta_tracker._workflow_eta_collect_videos',side_effect=AssertionError('no probing')):
  t.observe_runtime_output('p','unknown',outputs({0:8}))
 assert t.run.step_plans['s']['steps']==8 and t.run.shape_plans['s']['frames']==120
 assert t.run.done[0]['meta']==done_meta and t.run.initial==initial
 previous=t.run.video_sources['unknown']['runtime_values'][0]
 t.observe_runtime_output('wrong-prompt','unknown',outputs({0:99}))
 t.observe_runtime_output('p','unknown',outputs({0:99},count=2))
 assert t.run.video_sources['unknown']['runtime_values'][0]==previous
 # Cached upstream values are authoritative at the consuming node too.
 clock[0]=2;t._on_executing({'prompt_id':'p','node':'q'})
 t.observe_runtime('p','q',{'complete':True,'has_tensors':False,'inputs':{'steps':{'length':1,'items':[{'scalar':10}]}}})
 assert t.run.step_plans['s']['steps']==10
 # Failed replanning cannot leave half-updated source values or timing metadata.
 old_sources=t.run.video_sources;old_predictions=t.run.preds;old_meta=t.run.meta
 with patch.object(t,'_prepare',side_effect=RuntimeError('test refresh failure')):
  try:t.observe_runtime_output('p','unknown',outputs({0:12}))
  except RuntimeError:pass
  else:raise AssertionError('test hook did not run')
 assert t.run.video_sources is old_sources and t.run.preds is old_predictions and t.run.meta is old_meta
 assert t.run.video_sources['unknown']['runtime_values'][0]==10
 t._discard('test');assert t.run is None
 # Run-local values must not leak into a later generation.
 with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value={}):t._on_start({'prompt_id':'new'})
 assert _workflow_eta_literal(g,['unknown',0],video_info=t.run.video_sources) is None
 hist.close()
print('PASS: authoritative output/cached-input scalars, downstream replanning, preserved timing/initial estimate, no loop-cursor rereads, list ambiguity/prompt isolation and reset')

# Partial observations preserve independent source fields.
g=graph()
raw={'width':1920,'height':1080,'frames':240,'frame_rate':'24/1','uncertain_frames':False}
info={'v':dict(raw,runtime_values={2:120})}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert (merged['frames'],merged['fps'],merged['width'],merged['height'],merged['duration'])==(120,12,1920,1080,10)
assert merged['exact_frames']
info={'v':dict(raw,runtime_values={4:640,5:384})}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert (merged['width'],merged['height'])==(640,384)
assert merged['frames'] is None and not merged['exact_frames']
info={'v':dict(raw,runtime_values={3:12.})}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert merged['frames'] is None and merged['duration'] is None
info={'v':dict(raw,runtime_values={2:120,3:12.,6:9.9})}
assert _workflow_eta_loaded_video_info(g,'v',info)['duration']==9.9
info={'v':{'runtime_values':{2:80,3:20.,4:640,5:384}}}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert merged['duration']==4 and merged['exact_frames']
g['v']['inputs']['force_fps']=0
info={'v':dict(raw,runtime_values={4:False,5:float('nan'),3:-1,2:2.5})}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert (merged['width'],merged['height'],merged['frames'],merged['fps'])==(1920,1080,240,24)
print('PASS: partial video metadata merging, FPS uncertainty, explicit duration and invalid-value guards')
info={'v':dict(raw,runtime_values={3:12.})}
merged=_workflow_eta_loaded_video_info(g,'v',info)
assert merged['frames'] is None and merged['duration'] is None and not merged['exact_frames']
