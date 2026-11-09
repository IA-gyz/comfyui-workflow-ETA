import sys, tempfile, sqlite3, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory, make_workflow_eta_signature
from workflow_eta_tracker import WorkflowETATracker, _workflow_eta_model_lineage
with tempfile.TemporaryDirectory() as d:
    path=d+'/h.sqlite'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE node_runs (id INTEGER PRIMARY KEY, ts REAL, class_type TEXT, sig TEXT, model_key TEXT, device TEXT, cold INTEGER, duration REAL, work REAL)')
    db.execute("INSERT INTO node_runs VALUES (1,0,'X','s','','gpu',0,2,NULL)")
    db.commit();db.close()
    h=WorkflowETAHistory(path)
    assert h.predict_node('X','s',False,'gpu',node_id='1')['seconds']==2
    for nid,duration in [('10',.1),('11',1.3)]:
        h.record_node('CLIP','s',duration,True,'gpu',node_id=nid)
    assert h.predict_node('CLIP','s',True,'gpu',node_id='11')['seconds']==1.3
    assert h.predict_node('CLIP','s',True,'gpu',node_id='10')['seconds']==.1
    h.record_sampler('S','s',8,4,.15,True,'gpu',node_id='6')
    assert h.sampler_stats('S','s',True,'gpu',node_id='6')['step_time']==.15
    graph={'1':{'class_type':'Loader','inputs':{'ckpt_name':'a.safetensors'}},
           '2':{'class_type':'CLIP','inputs':{'clip':['1',1]}},
           '3':{'class_type':'Text','inputs':{'text':'hello'}}}
    server=types.SimpleNamespace(send_sync=lambda *a,**kw:None)
    t=WorkflowETATracker(server,h,resident_fn=lambda:frozenset({('Model',1)}),find_prompt_fn=lambda pid:(graph,['2','3']),verbose=False,start_thread=False)
    t._on_start({'prompt_id':'a'})
    assert t.run.node_cold=={'1':True,'2':True,'3':False}
    first=t.run.meta['2'][1]
    t.last_models=frozenset({'a.safetensors'})
    t._prepare(t.run)
    assert not t.run.node_cold['2']
    graph['1']['inputs']['ckpt_name']='b.safetensors'
    t._prepare(t.run)
    assert t.run.meta['2'][1]!=first and t.run.node_cold['2']
    t.run.todo={'2'}
    t.run.preds={'2':{'seconds':5,'low':4,'high':6,'tier':'node-other-state'}}
    t.run.cur={'node':'2','t0':t.clock(),'ticks':[],'max':0,'hist_step':None}
    assert t._workflow_eta(t.run,t.clock())['confidence']=='estimated'
    graph['1']['inputs']['cycle']=['2',0]
    assert _workflow_eta_model_lineage(graph,'2')==('b.safetensors',)
    h.prune();h.close()
print('PASS: migration, legacy fallback, node separation, sampler history, checkpoint lineage, per-node state, confidence, cycles')

with tempfile.TemporaryDirectory() as d:
    h=WorkflowETAHistory(d+'/history.sqlite')
    h.record_node('CLIP','checkpoint-a',1.2,True,'gpu',node_id='11')
    h.record_node('CLIP','checkpoint-a',.1,True,'gpu',node_id='10')
    result=h.predict_node('CLIP','checkpoint-b',True,'gpu',node_id='11')
    assert result['tier']=='node-class' and result['seconds']==1.2
    h.close()
print('PASS: unseen checkpoint fallback preserves encoder identity')
