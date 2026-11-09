"""Behavioral coverage of declared outputs, file selection and uncertainty."""
import copy,types,sys,tempfile,os
from unittest.mock import patch
from workflow_eta_tracker import (_workflow_eta_collect_videos,_workflow_eta_literal,
    _workflow_eta_loaded_video_info,_workflow_eta_video_source,_workflow_eta_video_adapter,
    _workflow_eta_workload_lineage,_workflow_eta_video_plan)
meta={'width':1920,'height':1080,'frames':240,'frame_rate':'24/1','uncertain_frames':False}
g={'v':{'class_type':'MagikVideoLoader','inputs':{'video':'clip.mp4','max_frames':100,'skip_first_frames':5}}}
v={'v':meta}
assert _workflow_eta_literal(g,['v',2],video_info=v)==95
assert _workflow_eta_literal(g,['v',4],video_info=v)==1920
# Exact discovery follows the adapter contract, including renamed/moved outputs.
class Clone:WORKFLOW_ETA_VIDEO_ADAPTER={'window':'cap_then_skip','source':'movie','outputs':{0:'frames',7:'width'}}
with patch.dict(sys.modules,{'nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={'RenamedReader':Clone})}):
 c={'v':{'class_type':'RenamedReader','inputs':{'movie':'clip.mp4'}}}
 with patch('workflow_eta_tracker._workflow_eta_video_info',return_value=meta):
  found=_workflow_eta_collect_videos(c)
 assert _workflow_eta_literal(c,['v',0],video_info=found)==240
 assert _workflow_eta_literal(c,['v',7],video_info=found)==1920
 assert not _workflow_eta_workload_lineage(c,video_info=found)['v'][1]
 assert _workflow_eta_video_adapter({'class_type':'UnknownVideoLoader'}) is None
# Without an exact count, normal scalar resolution stays unknown. ETA-only
# estimates carry a separate family and retain unresolved workload status.
uncertain=dict(meta,frames=None,estimated_frames=240,uncertain_frames=True)
assert _workflow_eta_literal(g,['v',2],video_info={'v':uncertain}) is None
assert _workflow_eta_literal(g,['v',2],video_info={'v':uncertain},allow_estimated_video=True)==95
assert _workflow_eta_workload_lineage(g,video_info={'v':uncertain})['v'][1]
g['v']['inputs']['force_fps']=12
assert _workflow_eta_literal(g,['v',2],video_info=v,allow_estimated_video=True) is None
g['v']['inputs']['force_fps']=0
# Read the actual backend cursor, without invoking execution or cursor writes.
with tempfile.TemporaryDirectory() as root:
 for name in ['old.mp4','next.mp4']:open(os.path.join(root,name),'w').close()
 def read_state(identity):return {'scope':[root,False],'reset':'r','next':'next.mp4'}
 def resolve_folder(folder):return root
 def resolve_video(video,folder,sub):
  path=os.path.join(root,video)
  if not os.path.isfile(path):raise ValueError('missing')
  return path
 def load():raise AssertionError('ETA must never execute the loader')
 class Magik:pass
 Magik.load=load
 params={'video':'old.mp4','folder':root,'loop_through_folder':True,'include_subfolders':False,'loop_reset':'r','loop_identity':'id'}
 node={'class_type':'MagikVideoLoader','inputs':params};graph={'v':node}
 with patch.dict(sys.modules,{'nodes':types.SimpleNamespace(NODE_CLASS_MAPPINGS={'MagikVideoLoader':Magik}),'folder_paths':types.SimpleNamespace(get_input_directory=lambda:root)}):
  selected=_workflow_eta_video_source(graph,'v',_workflow_eta_video_adapter(node))
  assert selected==os.path.join(root,'next.mp4'),selected
  params['loop_reset']='new'
  assert _workflow_eta_video_source(graph,'v',_workflow_eta_video_adapter(node))==os.path.join(root,'old.mp4')
  params['loop_through_folder']=False;params['video']='../escape.mp4'
  assert _workflow_eta_video_source(graph,'v',_workflow_eta_video_adapter(node)) is None
# Scalar gates used by the supplied workflow.
g['compare']={'class_type':'MagikCompare','inputs':{'mode':'Values','relation':'==','a':0.,'b':0.}}
assert _workflow_eta_literal(g,['compare',0]) is True
print('PASS: declared adapter outputs, renamed loaders, header uncertainty, FPS conversion, cursor/reset and folder containment')
# Numeric secondary sockets are checked against the installed node schema.
g['constant']={'class_type':'Magik.Float','inputs':{'value':0.}}
with patch('workflow_eta_tracker._workflow_eta_video_class',return_value=types.SimpleNamespace(RETURN_TYPES=('FLOAT','INT'))):
 assert _workflow_eta_literal(g,['constant',1])==0
with patch('workflow_eta_tracker._workflow_eta_video_class',return_value=types.SimpleNamespace(RETURN_TYPES=('FLOAT','STRING'))):
 assert _workflow_eta_literal(g,['constant',1]) is None
