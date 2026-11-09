"""Preparation forecasts reuse near-matching measured video workloads only."""
import tempfile,types,copy,json
from unittest.mock import patch
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker
with tempfile.TemporaryDirectory() as root:
 h=WorkflowETAHistory(root+'/h.sqlite')
 g={'v':{'class_type':'MagikVideoLoader','inputs':{'video':'a.mp4'}},
    'n':{'class_type':'MaskProcessor','inputs':{'image':['v',0],'threshold':.5}},
    'save':{'class_type':'SaveVideo','inputs':{'video':['n',0],'codec':'auto'}}}
 raw={'width':1920,'height':1080,'frames':60,'frame_rate':'25/1'}
 def start(info):
  t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,device='gpu',find_prompt_fn=lambda p:(g,['save']),verbose=False,start_thread=False)
  t.machine_fn=lambda:None
  with patch('workflow_eta_tracker._workflow_eta_collect_videos',return_value={'v':copy.deepcopy(info)}):t._on_start({'prompt_id':'p'})
  return t
 t=start(raw)
 metadata={'complete':True,'has_tensors':True,'media_context':t._runtime_media_context(t.run,'n'),'inputs':{}}
 recipe=t._runtime_recipe(t.run,'n')
 for seconds in (80,82):
  h._db.execute('INSERT INTO runtime_runs (recipe,metadata,machine,duration,class_type,cold,device,node_id) VALUES (?,?,?,?,?,?,?,?)',
   (recipe,json.dumps(metadata),'null',seconds,'MaskProcessor',0,'gpu','n'))
 h._db.commit()
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 p=t.run.preds['n'];assert p['tier']=='runtime-media-profile' and p['seconds']==81 and p['approximate'],p
 assert p['low']<p['seconds']<p['high']
 t=start(dict(raw,frames=None,estimated_frames=147,uncertain_frames=True))
 assert t.run.preds['n']['tier']!='runtime-media-profile'
 g['n']['inputs']['threshold']=.7
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 assert t.run.preds['n']['tier']!='runtime-media-profile'
 g['n']['inputs']['threshold']=.5
 t=start(dict(raw,width=1280,height=720,frames=None,estimated_frames=61,uncertain_frames=True))
 assert t.run.preds['n']['tier']!='runtime-media-profile'
 # SaveVideo's opaque handle is observed only with a resolved source context.
 t=start(raw);t._on_executing({'prompt_id':'p','node':'save'})
 summary={'complete':True,'has_tensors':False,'inputs':{'video':{'length':1,'items':[{'type':'VideoHandle'}]}}}
 t.observe_runtime('p','save',summary)
 assert 'save' in t.run.runtime
 t=start(dict(raw,frames=None,estimated_frames=61,uncertain_frames=True))
 t._on_executing({'prompt_id':'p','node':'save'});t.observe_runtime('p','save',summary)
 assert 'save' not in t.run.runtime
 h.close()
print('PASS: provisional preparation profiles, frame/config/geometry separation and resolved-only opaque video observations')
