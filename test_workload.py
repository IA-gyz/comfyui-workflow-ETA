import sys, tempfile, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_tracker import _workflow_eta_workload_lineage, _workflow_eta_image_info, WorkflowETATracker
from workflow_eta_history import make_workflow_eta_signature, WorkflowETAHistory

def graph():
    return {
        '1': {'class_type':'EmptyLatentImage','inputs':{'width':512,'height':768,'batch_size':1}},
        '2': {'class_type':'KSampler','inputs':{'latent_image':['1',0],'seed':10,'steps':20}},
        '3': {'class_type':'VAEDecode','inputs':{'samples':['2',0]}},
        '4': {'class_type':'CLIPTextEncode','inputs':{'text':'hello'}},
    }

def sig(g, nid):
    w=_workflow_eta_workload_lineage(g)
    n=g[nid]
    return make_workflow_eta_signature(n['class_type'],n['inputs'],workload=w[nid][0])[0]

g=graph(); baseline={n:sig(g,n) for n in g}
g['1']['inputs']['width']=1024
assert sig(g,'2')!=baseline['2'] and sig(g,'3')!=baseline['3']
assert sig(g,'4')==baseline['4']
g=graph();g['1']['inputs']['batch_size']=2
assert sig(g,'2')!=baseline['2']
g=graph();g['2']['inputs']['seed']=99
assert sig(g,'2')==baseline['2'] and sig(g,'3')==baseline['3']
g=graph();g['1']['inputs']['width']=['5',0];g['5']={'class_type':'Integer','inputs':{'value':512}}
s=sig(g,'2');g['5']['inputs']['value']=1024
assert sig(g,'2')!=s
g['5']['class_type']='RandomInteger'
assert _workflow_eta_workload_lineage(g)['2'][1]
g=graph();g['1']={'class_type':'LoadImage','inputs':{'image':'test.png'}}
g['5']={'class_type':'ImageScaleBy','inputs':{'image':['1',0],'scale_by':1.0}}
g['2']['inputs']['latent_image']=['5',0]
calls=[]
def info(name): calls.append(name);return {'width':512,'height':768,'frames':1}
w=_workflow_eta_workload_lineage(g,info)
assert calls==['test.png'] and not w['2'][1]
a=make_workflow_eta_signature('KSampler',{},workload=w['2'][0])[0]
g['5']['inputs']['scale_by']=2
w=_workflow_eta_workload_lineage(g,info)
assert make_workflow_eta_signature('KSampler',{},workload=w['2'][0])[0]!=a
assert _workflow_eta_workload_lineage(g,lambda name:None)['2'][1]
g['1']['inputs']['loop']=['2',0]
assert _workflow_eta_workload_lineage(g,info)['2'][0]
with tempfile.TemporaryDirectory() as d:
    h=WorkflowETAHistory(d+'/history.sqlite')
    small=graph();big=graph();big['1']['inputs']['height']=1536
    h.record_node('KSampler',sig(small,'2'),1,False,node_id='2')
    h.record_node('KSampler',sig(big,'2'),4,False,node_id='2')
    assert h.predict_node('KSampler',sig(small,'2'),False,node_id='2')['seconds']==1
    assert h.predict_node('KSampler',sig(big,'2'),False,node_id='2')['seconds']==4
    t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda pid:(small,['3','4']),verbose=False,start_thread=False)
    t._on_start({'prompt_id':'a'})
    before=t.run.meta['2'][1]
    small['1']['inputs']['width']=1024
    t._prepare(t.run)
    assert t.run.meta['2'][1]!=before
    h.close()
    from PIL import Image
    image=Path(d)/'sample.png';Image.new('RGB',(123,456)).save(image)
    sys.modules['folder_paths']=types.SimpleNamespace(get_annotated_filepath=lambda name:str(image))
    assert _workflow_eta_image_info('sample.png')=={'width':123,'height':456,'frames':1}
    Image.new('RGB',(456,789)).save(image)
    assert _workflow_eta_image_info('sample.png')['height']==789
print('PASS: upstream resolution, batch, seed invariance, scalar links, random sizes, image headers, scaling, cycles, separate history and tracker integration')
