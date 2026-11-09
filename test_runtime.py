"""Runtime observer integration, learning isolation and failed-run exclusion."""
import asyncio,gc,os,sys,tempfile,types,weakref
from unittest.mock import patch
from workflow_eta_runtime import describe_inputs,install_runtime_observer
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker

class Tensor:
    def __init__(self,shape):self.shape=shape;self.dtype='float16'
    def cpu(self):raise AssertionError('must not copy tensor')
    def numpy(self):raise AssertionError('must not copy tensor')
    def item(self):raise AssertionError('must not read tensor')
    def __iter__(self):raise AssertionError('must not iterate tensor')

def tracker(history,clock,graph):
    return WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),history,
        find_prompt_fn=lambda pid:(graph,['n']),clock=lambda:clock[0],device='gpu',verbose=False,start_thread=False)

def graph(filename='a.png'):
    return {'v':{'class_type':'LoadImage','inputs':{'image':filename}},
            'n':{'class_type':'UnknownCustomProcessor','inputs':{'image':['v',0],'mode':'quality'}}}

with patch.dict(sys.modules,{'torch':types.SimpleNamespace(Tensor=Tensor)}):
    value=Tensor((120,384,640,3));ref=weakref.ref(value)
    summary=describe_inputs({'image':[value],'prompt':[{'auth_token':'private'}]})
    assert summary['complete'] and summary['has_tensors'];assert 'private' not in str(summary)
    del value;gc.collect();assert ref() is None
    assert not describe_inputs({'image':[Tensor((1,1,1,3))]*40})['complete']
    # End-to-end timing: active input-shape matches, new shapes remain separate.
    with tempfile.TemporaryDirectory() as root:
        path=root+'/history.sqlite';h=WorkflowETAHistory(path)
        for i in range(3):
            clock=[0.];t=tracker(h,clock,graph());t._on_start({'prompt_id':str(i)})
            t._on_executing({'prompt_id':str(i),'node':'n'})
            t.observe_runtime(str(i),'n',summary)
            clock[0]=10.;t._on_success({'prompt_id':str(i)})
        assert h._db.execute('select count(*) from runtime_runs').fetchone()[0]==3
        h.close();h=WorkflowETAHistory(path)
        clock=[0.];t=tracker(h,clock,graph('different.png'));t._on_start({'prompt_id':'new'})
        t._on_executing({'prompt_id':'new','node':'n'});t.observe_runtime('wrong-prompt','n',summary)
        assert not t.run.runtime
        t.observe_runtime('new','n',summary)
        assert t.run.preds['n']['tier']=='runtime-exact' and t.run.preds['n']['seconds']==10.
        assert any(row['group']=='Runtime' for row in t.run.watch_rows)
        old_count=h._db.execute('select count(*) from runtime_runs').fetchone()[0]
        t._discard('interrupted');assert h._db.execute('select count(*) from runtime_runs').fetchone()[0]==old_count
        t._on_start({'prompt_id':'large'});t._on_executing({'prompt_id':'large','node':'n'})
        changed=describe_inputs({'image':[Tensor((240,384,640,3))]})
        t.observe_runtime('large','n',changed);assert not t.run.preds['n']['tier'].startswith('runtime')
        # Different model/config recipe cannot use the same-shape timings.
        t._discard('test');g=graph();g['n']['inputs']['mode']='fast';t=tracker(h,clock,g)
        t._on_start({'prompt_id':'config'});t._on_executing({'prompt_id':'config','node':'n'})
        t.observe_runtime('config','n',summary);assert not t.run.preds['n']['tier'].startswith('runtime')
        h.close()

    async def check_hook():
        calls=[]
        class Observer:
            def observe_runtime(self,*args):calls.append(('in',args))
            def observe_runtime_output(self,*args):calls.append(('out',args))
            def exclude_runtime_timing(self,*args):calls.append(('excluded',args))
            def _log(self,*args):pass
        marker=([ [Tensor((2,3,4,3))] ],{},False,False)
        async def get_output_data(prompt_id,unique_id,obj,input_data_all,**kwargs):return marker
        execution=types.SimpleNamespace(get_output_data=get_output_data)
        o=Observer();assert install_runtime_observer(o,execution)
        wrapped=execution.get_output_data;assert install_runtime_observer(o,execution)
        assert wrapped is execution.get_output_data
        result=await wrapped('p','n',object(),{'image':[Tensor((2,3,4,3))]})
        assert result is marker and [c[0] for c in calls]==['in','out']
        # Observer failures cannot change return identity or node exceptions.
        o.observe_runtime=lambda *args:(_ for _ in ()).throw(ValueError('observe'))
        assert await wrapped('p','n',object(),{}) is marker
        async def failing(prompt_id,unique_id,obj,input_data_all):raise RuntimeError('node failed')
        execution=types.SimpleNamespace(get_output_data=failing);install_runtime_observer(o,execution)
        try:await execution.get_output_data('p','n',object(),{})
        except RuntimeError as error:assert str(error)=='node failed'
        else:raise AssertionError('node exception swallowed')
        assert not install_runtime_observer(o,types.SimpleNamespace(get_output_data=lambda:None))
        async def pending(prompt_id,unique_id,obj,input_data_all):return ([],{},False,True)
        execution=types.SimpleNamespace(get_output_data=pending);install_runtime_observer(o,execution)
        await execution.get_output_data('p','n',object(),{})
        assert calls[-1][0]=='excluded'

    asyncio.run(check_hook())
    # A stable, resolved static configuration can reuse its runtime profile at queue time.
    with tempfile.TemporaryDirectory() as root:
        h=WorkflowETAHistory(root+'/h.sqlite')
        g={'v':{'class_type':'EmptyLatentImage','inputs':{'width':640,'height':384,'batch_size':1}},
           'n':{'class_type':'UnknownCustomProcessor','inputs':{'samples':['v',0]}}}
        for i in range(3):
            c=[0.];t=tracker(h,c,g);t._on_start({'prompt_id':str(i)});t._on_executing({'node':'n','prompt_id':str(i)})
            t.observe_runtime(str(i),'n',summary);c[0]=10.;t._on_success({'prompt_id':str(i)})
        t=tracker(h,[0.],g);t._on_start({'prompt_id':'profile'})
        assert t.run.preds['n']['tier']=='runtime-profile',t.run.preds['n']
        t._on_executing({'prompt_id':'profile','node':'n'})
        t.observe_runtime('profile','n',describe_inputs({'image':[Tensor((240,384,640,3))]}))
        assert t.run.preds['n']['seconds'] is None
        h.close()
    from workflow_eta_runtime import describe_outputs
    out=describe_outputs([[Tensor((120,384,640,3))],[24.]],types.SimpleNamespace(RETURN_TYPES=('IMAGE','FLOAT'),RETURN_NAMES=('frames','fps')))
    assert out['image_batches'][0]['image_count']==120 and out['image_batches'][0]['width']==640
    assert out['scalar_outputs'][0]['value']==24.

print('PASS: metadata-only capture, no tensor retention, persistent exact-shape learning, changed workload/config isolation, abort exclusion and executor transparency')
