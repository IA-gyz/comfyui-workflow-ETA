import sys, tempfile, types, copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker

class Clock:
    now=0.
    def __call__(self):return self.now

def graph(size=768,batch=1,steps=10):
    return {'m':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'a.safetensors'}},
            'l':{'class_type':'EmptyLatentImage','inputs':{'width':size,'height':size,'batch_size':batch}},
            's':{'class_type':'KSampler','inputs':{'model':['m',0],'latent_image':['l',0], 'steps':steps,'cfg':7.,'denoise':1.,'sampler_name':'euler','scheduler':'normal','seed':1}},
            'd':{'class_type':'VAEDecode','inputs':{'samples':['s',0],'vae':['m',2]}}}

def start(h,g,cold=True,device='gpu'):
    c=Clock();t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda pid:(g,['d']),clock=c,verbose=False,start_thread=False,device=device,
        resident_fn=lambda:frozenset() if cold else frozenset({('m',1)}))
    if not cold:t.last_models=frozenset({'a.safetensors'})
    t._on_start({'prompt_id':'p'});return t,c

def record(h,g,rate,n=2):
    for _ in range(n):
        t,c=start(h,g);t._on_executing({'node':'s','prompt_id':'p'})
        steps=g['s']['inputs']['steps']
        for i in range(1,steps+1):
            c.now=1+(i-1)*rate;t._on_progress({'node':'s','value':i,'max':steps})
        c.now+=.02;t._on_success({'prompt_id':'p'})

with tempfile.TemporaryDirectory() as path:
    h=WorkflowETAHistory(path+'/resolution.sqlite')
    record(h,graph(768),.1);record(h,graph(1024),.16)
    target=graph(896);t,c=start(h,target);p=t.run.preds['s']
    assert p['tier']=='resolution-learned' and not p['extrapolated']
    weight=(896**2-768**2)/(1024**2-768**2)
    assert abs(p['seconds']-(1+9*(.1+weight*.06)+.02))<1e-8
    assert t.run.preds['d']['tier']!='resolution-learned'
    t._on_cached({'prompt_id':'p','nodes':['m','l','d']})
    assert t.last_payload['workload_scaling']=='resolution'
    exact,c=start(h,graph(768));assert exact.run.preds['s']['tier']=='node-few'
    close,c=start(h,graph(1120));assert close.run.preds['s']['tier']=='resolution-learned' and close.run.preds['s']['extrapolated']
    far,c=start(h,graph(1536));assert far.run.preds['s']['tier']!='resolution-learned'
    for changed in ('batch','steps','cfg','aspect','model','denoise','sampler','scheduler'):
        g=graph(896)
        if changed=='batch':g['l']['inputs']['batch_size']=2
        elif changed=='steps':g['s']['inputs']['steps']=20
        elif changed=='aspect':g['l']['inputs']['height']=768
        elif changed=='model':g['m']['inputs']['ckpt_name']='b.safetensors'
        else:g['s']['inputs'][{'sampler':'sampler_name'}.get(changed,changed)]={'cfg':8.,'denoise':.5,'sampler':'dpmpp_2m','scheduler':'karras'}[changed]
        other,c=start(h,g);assert other.run.preds['s']['tier'] not in ('resolution-learned','batch-learned'), changed
    warm,c=start(h,target,cold=False);assert warm.run.preds['s']['tier']!='resolution-learned'
    device,c=start(h,target,device='other');assert device.run.preds['s']['tier']!='resolution-learned'
    h.close()
    h=WorkflowETAHistory(path+'/batch.sqlite')
    record(h,graph(batch=1),.1);record(h,graph(batch=3),.22)
    t,c=start(h,graph(batch=2));p=t.run.preds['s']
    assert p['tier']=='batch-learned' and abs(p['seconds']-(1+9*.16+.02))<1e-8
    g=graph(batch=2);g['l']['class_type']='CustomUnknownLatent'
    t,c=start(h,g);assert 's' not in t.run.shape_plans
    g=graph();g['repeat']={'class_type':'RepeatLatentBatch','inputs':{'samples':['l',0],'amount':3}};g['s']['inputs']['latent_image']=['repeat',0]
    t,c=start(h,g);assert t.run.shape_plans['s']['batch']==3
    g['up']={'class_type':'LatentUpscale','inputs':{'samples':['repeat',0],'width':1024,'height':1024,'upscale_method':'nearest-exact','crop':'disabled'}};g['s']['inputs']['latent_image']=['up',0]
    t,c=start(h,g);assert t.run.shape_plans['s']['width']==1024 and t.run.shape_plans['s']['batch']==3
    h.close()
    # Reject calibration across a measured memory-pressure-like discontinuity.
    h=WorkflowETAHistory(path+'/cliff.sqlite')
    record(h,graph(batch=4),.3);record(h,graph(batch=5),1.2)
    t,c=start(h,graph(batch=6));assert t.run.preds['s']['tier']!='batch-learned'
    h.close()
    # A single sample at each endpoint is insufficient calibration.
    h=WorkflowETAHistory(path+'/few.sqlite')
    record(h,graph(768),.1,1);record(h,graph(1024),.16,1)
    t,c=start(h,graph(896));assert t.run.preds['s']['tier']!='resolution-learned'
    h.close()
print('PASS: captured resolution/batch interpolation, bounded extrapolation, exact priority, unchanged-node scope, shape/step/model/config/state/device isolation, adapters, cliff and low-data rejection')


from unittest.mock import patch
from workflow_eta_tracker import _workflow_eta_workload_lineage
with tempfile.TemporaryDirectory() as path:
    h=WorkflowETAHistory(path+'/img.sqlite')
    g=graph();g['load']={'class_type':'LoadImage','inputs':{'image':'source.png'}}
    g['resize']={'class_type':'ImageScale','inputs':{'image':['load',0],'width':768,'height':768,'upscale_method':'bicubic','crop':'disabled'}}
    g['l']={'class_type':'VAEEncode','inputs':{'pixels':['resize',0],'vae':['m',2]}}
    with patch('workflow_eta_tracker._workflow_eta_image_info',return_value={'width':640,'height':480,'frames':1}):
        t,c=start(h,g)
        assert t.run.shape_plans['s']['width']==768 and t.run.shape_plans['s']['batch']==1
        assert t.run.shape_plans['s']['batch_sig'] is None
        previous=t.run.shape_plans['s']['resolution_sig']
        g['resize']['inputs'].update(width=1024,height=1024);t._prepare(t.run)
        assert t.run.shape_plans['s']['resolution_sig']==previous
    h.close()
print('PASS: image-to-image explicit ImageScale -> VAEEncode resolution adapter')
