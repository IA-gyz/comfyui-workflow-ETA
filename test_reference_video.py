"""Regression for loader frame_count -> equality -> lazy-switch H3 length."""
import copy,tempfile,types
from unittest.mock import patch
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import (WorkflowETATracker,_workflow_eta_literal,
    _workflow_eta_collect_videos,_workflow_eta_pixaroma_info)

def graph():
 return {
 '480':{'class_type':'PrimitiveFloat','inputs':{'value':0}},
 '940':{'class_type':'PrimitiveFloat','inputs':{'value':0}},
 '937':{'class_type':'Basic data handling: Equal','inputs':{'value1':['480',0],'value2':['940',0]}},
 '942':{'class_type':'easy int','inputs':{'value':0}},
 '481':{'class_type':'ComfyMathExpression','inputs':{'expression':'max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17','values.a':['480',0]}},
 '939':{'class_type':'LazySwitchKJ','inputs':{'switch':['937',0],'on_true':['942',0],'on_false':['481',1]}},
 '936':{'class_type':'PixaromaLoadVideo','inputs':{'video':'clip.mp4','max_frames':['939',0],'skip_first_frames':0,'force_fps':0,'custom_width':0,'custom_height':0}},
 '943':{'class_type':'LazySwitchKJ','inputs':{'switch':['937',0],'on_false':['481',1],'on_true':['936',2]}},
 '464':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':['943',0],'width':640,'height':384,'ref_videos.ref_video_0':['936',0]}},
 '473':{'class_type':'BasicScheduler','inputs':{'scheduler':'simple','steps':8,'denoise':1}},
 '469':{'class_type':'SamplerCustomAdvanced','inputs':{'sigmas':['473',0],'latent_image':['464',1]}}}

meta={'width':1920,'height':1080,'frames':240,'duration':'10','frame_rate':'24/1','uncertain_frames':False}
with tempfile.TemporaryDirectory() as path,patch('workflow_eta_tracker._workflow_eta_video_info',return_value=meta) as probe:
 h=WorkflowETAHistory(path+'/h.sqlite');g=graph()
 def start(g):
  t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['469']),clock=lambda:0.,verbose=False,start_thread=False,device='gpu',resident_fn=lambda:frozenset())
  t._on_start({'prompt_id':'p'});return t
 t=start(g)
 assert t.run.shape_plans['469']['frames']==240
 assert _workflow_eta_literal(g,['943',0],video_info=t.run.video_sources)==240
 t._on_cached({'prompt_id':'p','nodes':[]})
 assert any('loaded 240 frames' in e['message'] for e in t.log_entries)
 assert any(r['parameter']=='loaded_duration' and r['value']=='10.0' for r in t.last_payload['watched']['rows'])
 first_sig=t.run.meta['469'][1]
 g2=copy.deepcopy(g);g2['480']['inputs']['value']=2
 t2=start(g2);assert t2.run.shape_plans['469']['frames']==56
 loaded=_workflow_eta_pixaroma_info(g2,'936',t2.run.video_sources)
 assert loaded['frames']==56 and abs(loaded['duration']-56/24)<1e-9
 assert t2.run.meta['469'][1]!=first_sig
 # Reference duration changes even if output duration is explicitly fixed.
 g3=copy.deepcopy(g2);g3['936']['inputs']['max_frames']=100
 t3=start(g3);assert t3.run.shape_plans['469']['frames']==56
 assert t3.run.meta['469'][1]!=t2.run.meta['469'][1]
 # Documented Pixaroma order: cap first, then skip.
 g3['936']['inputs']['skip_first_frames']=5
 loaded=_workflow_eta_pixaroma_info(g3,'936',{'936':meta});assert loaded['frames']==95
 # Replacing same-name media with a different duration separates downstream signatures.
 probe.return_value=dict(meta,frames=480,duration='20')
 longer=start(g);assert longer.run.shape_plans['469']['frames']==480
 assert longer.run.meta['469'][1]!=first_sig
 # Missing headers and resampling are explicitly unresolved, not fabricated exact counts.
 probe.return_value=None
 missing=start(g);assert '469' not in missing.run.shape_plans and missing.run.preds['469']['seconds'] is None
 probe.return_value=meta
 resampled=copy.deepcopy(g);resampled['936']['inputs']['force_fps']=30
 t4=start(resampled);assert '469' not in t4.run.shape_plans
 assert '469' in t4.run.workload_unknown
 # Cyclic scalar wiring remains guarded.
 cyclic=copy.deepcopy(g);cyclic['939']['inputs']['switch']=['943',0]
 assert _workflow_eta_literal(cyclic,['943',0],video_info={'936':meta}) is None
 h.close()
print('PASS: Pixaroma reference-frame outputs, equal/lazy switch, source-matched generated length, duration/cap/skip signatures, fixed output with changed reference duration, overwritten file metadata, missing metadata/resampling uncertainty, cycles')
