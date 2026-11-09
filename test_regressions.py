import sys, tempfile, types, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_tracker import WorkflowETATracker, _workflow_eta_progress_rates
from workflow_eta_history import WorkflowETAHistory

class Clock:
    now=0.
    def __call__(self): return self.now

def graph():
    return {'1':{'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}},
            '2':{'class_type':'KSampler','inputs':{'latent_image':['1',0],'steps':['3',0],'seed':1}},
            '3':{'class_type':'Integer','inputs':{'value':20}}}

def start(h, g=None):
    c=Clock();events=[]
    server=types.SimpleNamespace(send_sync=lambda *args:events.append(args))
    t=WorkflowETATracker(server,h,find_prompt_fn=lambda pid:(g or graph(),list(g or graph())),clock=c,wall=c,verbose=False,start_thread=False)
    t._on_start({'prompt_id':'active'})
    return t,c,events

with tempfile.TemporaryDirectory() as d:
    h=WorkflowETAHistory(d+'/h.sqlite')
    g=graph();t,c,events=start(h,g);old=t.run.meta['2'][1]
    g['3']['inputs']['value']=40;t._prepare(t.run)
    assert old!=t.run.meta['2'][1]
    assert t.run.model_map=={'1':(), '2':(), '3':()}
    # Cache event must not rebuild expensive graph metadata.
    meta=t.run.meta;t._on_cached({'prompt_id':'active','nodes':['1','3','unreachable']})
    assert t.run.meta is meta and t.run.cached=={'1','3'}
    for event in ('execution_error','execution_interrupted'):
        t.on_event(event,{'prompt_id':'other'});assert t.run is not None
    t._on_executing({'node':'2','prompt_id':'active'})
    original=t.run.cur;t._on_executing({'node':'2','prompt_id':'active'})
    assert t.run.cur is original and not t.run.done
    for stamp,value in enumerate((1,6,11,16)):
        c.now=stamp;t._on_progress({'node':'2','value':value,'max':21})
    assert t._current_remaining(t.run,t.run.cur,None,c.now)==1
    size=len(t.run.cur['ticks']);t._on_progress({'node':'2','value':16,'max':21})
    assert len(t.run.cur['ticks'])==size
    for value,maximum in ((float('nan'),21),(5,0),(True,21),(-1,21),(22,21)):
        t._on_progress({'node':'2','value':value,'max':maximum})
    assert len(t.run.cur['ticks'])==size
    c.now=4;t._on_progress({'node_id':'2','value':1,'max':21})
    assert t.run.cur['repeated'] and len(t.run.cur['ticks'])==1
    assert t._current_remaining(t.run,t.run.cur,{'seconds':10},c.now)==6
    c.now=5;t._on_executing({'node':'expanded','prompt_id':'active'})
    assert 'expanded' in t.run.todo and t.run.done[-1]['sampler'] is None
    for i in range(1,5):
        c.now=5+i;t._on_progress({'node':'expanded','value':i,'max':10})
    assert t._workflow_eta(t.run,c.now)['remaining']==6
    assert t._workflow_eta(t.run,c.now)['lower_bound']
    # A parent resumed after expansion must contribute current remaining time,
    # and must not count twice in history or completion counts.
    c.now=10;t._on_executing({'node':'2','prompt_id':'active'})
    t.run.preds['2']={'seconds':10.,'low':9.,'high':11.,'tier':'node-few'}
    assert t._workflow_eta(t.run,c.now)['remaining']>=10
    c.now=11;t._on_executing({'node':None,'prompt_id':'active'})
    assert sum(rec['node']=='2' for rec in t.run.done)==1
    # Interrupt never saves partial durations.
    t.last_models=frozenset({'old.ckpt'})
    t.on_event('execution_interrupted',{'prompt_id':'active'})
    assert t.run is None and not t.last_models and h.stats()['node_runs']==0
    # Finalization is independent of persistence failures.
    t,c,events=start(h);t._on_executing({'node':'2','prompt_id':'active'});c.now=1
    save=h.record_completed_run
    h.record_completed_run=lambda *args,**kw: (_ for _ in ()).throw(OSError('disk full'))
    t.on_event('execution_success',{'prompt_id':'active'})
    assert t.run is None and t.last_payload['state']=='done'
    h.record_completed_run=save
    # Atomic transaction rollback on a sampler-row write failure.
    rec={'node':'2','meta':('KSampler','sig',''),'duration':1.,'cold':False,'sampler':(20,.1,.05)}
    h._db.execute("CREATE TRIGGER fail_sampler BEFORE INSERT ON sampler_runs BEGIN SELECT RAISE(ABORT,'test failure'); END")
    h._db.commit()
    before=h.stats()
    try: h.record_completed_run([rec],'pid',1.,1,0,False)
    except Exception: pass
    else: raise AssertionError('expected write failure')
    assert h.stats()==before
    h._db.execute('DROP TRIGGER fail_sampler');h._db.commit()
    h.record_completed_run([rec],'pid',1.,1,0,False)
    assert h.stats()=={'node_runs':1,'sampler_runs':1,'runs':1}
    # Progress and samples stay bounded for long runs.
    t,c,events=start(h);t._on_executing({'node':'2','prompt_id':'active'})
    for i in range(1,4500):
        c.now=i;t._on_progress({'node':'2','value':i,'max':5000})
    assert len(t.run.cur['ticks'])==128 and len(t.run.samples)<=4096
    h.close()
print('PASS: linked steps, cache reuse, prompt isolation, duplicate starts, skipped/invalid/reset progress, expansion ETA, interruption, persistence failure, atomic rollback, bounded buffers')
