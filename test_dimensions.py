import json, sys, tempfile, types
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from workflow_eta_tracker import (_workflow_eta_literal, _workflow_eta_workload_lineage, _workflow_eta_latent_info,
                     _workflow_eta_video_header, _workflow_eta_image_info, WorkflowETATracker)
from workflow_eta_history import WorkflowETAHistory

g={'p':{'class_type':'PrimitiveString','inputs':{'value':'sample.png'}},
   'r':{'class_type':'Reroute','inputs':{'input':['p',0]}},
   'l':{'class_type':'CustomLoadImage','inputs':{'image_path':['r',0]}},
   's':{'class_type':'KSampler','inputs':{'latent_image':['l',0]}}}
assert _workflow_eta_literal(g,['r',0])=='sample.png'
calls=[]
w=_workflow_eta_workload_lineage(g,lambda name:calls.append(name) or {'width':640,'height':480,'frames':1})
assert calls==['sample.png'] and not w['s'][1]
g['p']['class_type']='RandomString'
assert _workflow_eta_literal(g,['r',0]) is None
assert _workflow_eta_workload_lineage(g)['s'][1]
g['p']['class_type']='Reroute';g['p']['inputs']={'input':['r',0]}
assert _workflow_eta_literal(g,['r',0]) is None

with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'sample.latent'
    header=json.dumps({'latent_tensor':{'dtype':'F32','shape':[2,16,8,64,96],'data_offsets':[0,1]}}).encode()
    p.write_bytes(len(header).to_bytes(8,'little')+header+b'X')
    assert _workflow_eta_latent_info(str(p))=={'latent_shape':[2,16,8,64,96],'batch':2}
    g={'l':{'class_type':'LoadLatent','inputs':{'latent':str(p)}},'s':{'class_type':'SamplerCustomAdvanced','inputs':{'latent_image':['l',0]}}}
    assert not _workflow_eta_workload_lineage(g)['s'][1]
    p.write_bytes((2**40).to_bytes(8,'little'))
    assert _workflow_eta_latent_info(str(p)) is None
    from PIL import Image
    image=Path(d)/'test.png';Image.new('RGB',(320,240)).save(image)
    assert _workflow_eta_image_info(str(image))['width']==320
    h=WorkflowETAHistory(d+'/h.sqlite')
    graph={'l':{'class_type':'LoadVideo','inputs':{'video':'missing.mp4'}},
           's':{'class_type':'Sampler','inputs':{'latent_image':['l',0]}}}
    t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda pid:(graph,list(graph)),verbose=False,start_thread=False,clock=lambda:10.)
    t._on_start({'prompt_id':'test'})
    t.run.done=[{'node':'l'}];t.run.todo={'l'}
    t._push(t.run,force=True)
    assert not t.last_payload['workload_approximate']
    t.run.todo={'l','s'};t.run.cur={'node':'s','ticks':[(0,1),(1,2),(2,3),(3,4)],'max':10,'repeated':False,'t0':0,'hist_step':None}
    t._push(t.run,force=True)
    assert not t.last_payload['workload_approximate']
    h.close()

_workflow_eta_video_header.cache_clear()
metadata={'streams':[{'width':1920,'height':1080,'nb_frames':'120','duration':'4','avg_frame_rate':'30/1','side_data_list':[{'rotation':90}]}]}
with patch('subprocess.run',return_value=types.SimpleNamespace(stdout=json.dumps(metadata))) as run:
    info=_workflow_eta_video_header('ffprobe','/fake.mp4',100,1)
    assert info['width']==1080 and info['height']==1920 and info['frames']==120
    _workflow_eta_video_header('ffprobe','/fake.mp4',100,1)
    assert run.call_count==1
    _workflow_eta_video_header('ffprobe','/fake.mp4',100,2)
    assert run.call_count==2
    assert run.call_args.kwargs['timeout']==.5
print('PASS: loader aliases, linked constant paths, random/cyclic paths, absolute image paths, bounded latent headers, video metadata/cache/rotation, remaining-node uncertainty')
