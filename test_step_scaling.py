import copy, sys, tempfile, types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker, _workflow_eta_sampler_plan, _workflow_eta_workload_lineage

def graph(steps=20):
    return {'model':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'a.safetensors'}},
            'latent':{'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}},
            'sampler':{'class_type':'KSampler','inputs':{'steps':steps,'model':['model',0], 'latent_image':['latent',0],
                       'cfg':7.,'denoise':1.,'sampler_name':'euler','scheduler':'normal','seed':10}},
            'decode':{'class_type':'VAEDecode','inputs':{'samples':['sampler',0],'vae':['model',2]}}}
class Clock:
    now=0.
    def __call__(self):return self.now

def start(h,g,c=None,cold=True):
    c=c or Clock()
    t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *args:None),h,find_prompt_fn=lambda pid:(g,['decode']),clock=c,verbose=False,start_thread=False,
        device='gpu',resident_fn=lambda:frozenset() if cold else frozenset({('model',1)}))
    if not cold:t.last_models=frozenset({'a.safetensors'})
    t._on_start({'prompt_id':'test'})
    return t,c
with tempfile.TemporaryDirectory() as d:
    h=WorkflowETAHistory(d+'/h.sqlite')
    g=graph();t,c=start(h,g);base=t.run.step_plans['sampler']['step_sig']
    # Capture an actual completed run through tracker events.
    t._on_cached({'prompt_id':'test','nodes':['model','latent']})
    t._on_executing({'prompt_id':'test','node':'sampler'})
    for step in range(1,21):
        c.now=4.+(step-1)*.2;t._on_progress({'node':'sampler','value':step,'max':20})
    c.now+=.1;t._on_executing({'node':None,'prompt_id':'test'})
    t._on_success({'prompt_id':'test'})
    assert h._db.execute('SELECT step_sig FROM sampler_runs').fetchone()[0]==base
    bigger=graph(40);new,c=start(h,bigger)
    assert new.run.step_plans['sampler']['step_sig']==base
    pred=new.run.preds['sampler']
    assert pred['tier']=='step-scaled' and abs(pred['seconds']-11.9)<1e-8
    assert new.run.preds['decode']['tier']!='step-scaled'
    new._on_cached({'prompt_id':'test','nodes':['model','latent','decode']})
    assert abs(new.run.initial-11.9)<1e-8 and new.last_payload['step_scaled']
    # Known one-step target keeps first-step overhead and measured tail.
    one,c=start(h,graph(1))
    assert abs(one.run.preds['sampler']['seconds']-4.1)<1e-8
    # A real exact 40-step duration wins over extrapolation.
    meta=new.run.meta['sampler'];h.record_node(meta[0],meta[1],12.5,True,'gpu',node_id='sampler')
    exact,c=start(h,graph(40));assert exact.run.preds['sampler']['tier']=='node-few'
    # Different non-step parameters must produce different family signatures.
    cases=[('sampler','cfg',8.),('sampler','denoise',.5),('sampler','sampler_name','dpmpp_2m'),
           ('sampler','scheduler','karras'),('model','ckpt_name','b.safetensors'),
           ('latent','width',1024),('latent','batch_size',2)]
    for nid,key,value in cases:
        changed=graph(40);changed[nid]['inputs'][key]=value
        other,c=start(h,changed)
        assert other.run.step_plans['sampler']['step_sig']!=base
        assert other.run.preds['sampler']['tier']!='step-scaled'
    changed=graph(40);changed['sampler']['inputs']['seed']=999
    seed,c=start(h,changed);assert seed.run.step_plans['sampler']['step_sig']==base
    warm,c=start(h,graph(60),cold=False);assert warm.run.preds['sampler']['tier']!='step-scaled'
    assert h.predict_sampler_steps('KSampler',base,40,True,'other-gpu','sampler') is None
    # A linked constant step count is excluded only along its step-control edge.
    linked=graph();linked['n']={'class_type':'PrimitiveInt','inputs':{'value':20}}
    linked['sampler']['inputs']['steps']=['n',0]
    l,c=start(h,linked);first=l.run.step_plans['sampler']['step_sig']
    linked['n']['inputs']['value']=40;l._prepare(l.run)
    assert l.run.step_plans['sampler']['step_sig']==first
    linked['sampler']['inputs']['cfg']=['n',0];l._prepare(l.run);first=l.run.step_plans['sampler']['step_sig']
    linked['n']['inputs']['value']=60;l._prepare(l.run)
    assert l.run.step_plans['sampler']['step_sig']!=first
    # Custom samplers read a supported direct sigma scheduler.
    custom=graph();custom['schedule']={'class_type':'BasicScheduler','inputs':{'model':['model',0],'scheduler':'normal','steps':20,'denoise':1.}}
    custom['sampler']['class_type']='SamplerCustomAdvanced';custom['sampler']['inputs']={'sigmas':['schedule',0],'latent_image':['latent',0],'model':['model',0]}
    a,c=start(h,custom);first=a.run.step_plans['sampler']['step_sig']
    custom['schedule']['inputs']['steps']=40;a._prepare(a.run)
    assert a.run.step_plans['sampler']['step_sig']==first and a.run.step_plans['sampler']['steps']==40
    custom['schedule']['class_type']='UnknownSigmaGenerator';a._prepare(a.run)
    assert 'sampler' not in a.run.step_plans
    # Advanced ranges retain their identity, and calculate effective iterations.
    advanced=graph(40);advanced['sampler']['class_type']='KSamplerAdvanced'
    advanced['sampler']['inputs'].update(start_at_step=10,end_at_step=30)
    a,c=start(h,advanced);assert a.run.step_plans['sampler']['steps']==20
    old=a.run.step_plans['sampler']['step_sig'];advanced['sampler']['inputs']['end_at_step']=35;a._prepare(a.run)
    assert a.run.step_plans['sampler']['step_sig']!=old
    # Progress that starts late cannot establish first-step overhead safely.
    t,c=start(h,graph(80));t._on_executing({'node':'sampler','prompt_id':'test'})
    c.now=1;t._on_progress({'node':'sampler','value':5,'max':80})
    c.now=2;t._on_progress({'node':'sampler','value':80,'max':80})
    c.now=2.1;t._finish_node(t.run,c.now)
    assert t.run.done[-1]['step_sig'] is None
    h.close()
print('PASS: captured 20->40 scaling, startup/tail, exact precedence, sampler-only scope, config/model/shape/state/device isolation, linked steps, custom schedulers, advanced ranges, unsafe-progress rejection')
