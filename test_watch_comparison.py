import copy,tempfile,types
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker
from test_step_scaling import graph

with tempfile.TemporaryDirectory() as path:
    h=WorkflowETAHistory(path+'/h.sqlite')
    current=[graph(20)];clock=[0.]
    t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,
        find_prompt_fn=lambda p:(current[0],list(current[0])),clock=lambda:clock[0],
        verbose=False,start_thread=False,device='GPU',resident_fn=lambda:frozenset())
    t._on_start({'prompt_id':'first'});t._push(t.run,force=True)
    snap=t.last_payload['watched'];assert snap['baseline_prompt'] is None
    assert all(r['change']=='baseline' for r in snap['rows'])
    assert any(r['parameter']=='effective_steps' and r['value']=='20' for r in snap['rows'])
    assert not any(r['parameter']=='seed' for r in snap['rows'])
    clock[0]=1.;t._on_success({'prompt_id':'first'})
    assert t.last_payload['watched']['baseline_prompt'] is None
    baseline=copy.deepcopy(t.watch_baseline)
    current[0]=graph(40);clock[0]=2.;t._on_start({'prompt_id':'second'});t._push(t.run,force=True)
    snap=t.last_payload['watched'];assert snap['baseline_prompt']=='first'
    assert any(r['parameter']=='effective_steps' and r['previous']=='20' and r['value']=='40' and r['change']=='changed' for r in snap['rows'])
    assert t.watch_baseline==baseline
    t._on_abort({'prompt_id':'second'},'interrupted');assert t.watch_baseline==baseline
    current[0]=graph(30);clock[0]=3.;t._on_start({'prompt_id':'third'})
    nid=next(n for n,v in current[0].items() if v['class_type']=='KSampler')
    t._on_executing({'prompt_id':'third','node':nid});clock[0]=5.;t._on_success({'prompt_id':'third'})
    assert t.last_payload['watched']['nodes'][nid]['actual']==2.
    assert t.watch_baseline['prompt_id']=='third'
    # Removed fields are retained for inspection; added fields are distinguished.
    current[0]=graph(30);current[0][nid]['inputs']['extra']='new'
    del current[0][nid]['inputs']['cfg']
    clock[0]=6.;t._on_start({'prompt_id':'fourth'});t._push(t.run,force=True)
    rows=t.last_payload['watched']['rows']
    assert any(r['parameter']=='cfg' and r['change']=='removed' for r in rows)
    assert any(r['parameter']=='extra' and r['change']=='added' for r in rows)
    # Snapshot contains resolved literals and strict-family strings without HTML interpretation.
    current[0]=graph(30);current[0][nid]['inputs']['positive']=['text',0]
    current[0]['text']={'class_type':'PrimitiveStringMultiline','inputs':{'value':'<unsafe> '+('long prompt '*20)}}
    clock[0]=7.;t._on_start({'prompt_id':'fifth'});t._push(t.run,force=True)
    assert any(r['group']=='Family' and '<unsafe>' in r['value'] for r in t.last_payload['watched']['rows'])
    h.close()
print('PASS: watched fields, resolved derived controls, excluded seeds, previous completed baseline, changed/added/removed, interruptions, actual timing, strict-family inputs')

# A refined estimate must never rewrite the original forecast used for review.
with tempfile.TemporaryDirectory() as path:
    h=WorkflowETAHistory(path+'/history.sqlite')
    g={'n':{'class_type':'Work','inputs':{}}}
    t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,
        find_prompt_fn=lambda p:(g,['n']),verbose=False,start_thread=False)
    t._on_start({'prompt_id':'provenance'})
    t.run.preds['n']={'seconds':10,'tier':'runtime-media-profile','n':3,'low':5,'high':15,'calibration':{'axis':'frames'}}
    t._announce(t.run)
    t.run.preds['n']['seconds']=12
    t.run.preds['n']['calibration']['axis']='resolved frames'
    t.run.done=[{'node':'n','duration':11}]
    row=t._watch_nodes(t.run)['n']
    assert row['initial_predicted']==10 and row['predicted']==12 and row['actual']==11
    assert row['initial_tier']=='runtime-media-profile' and row['samples']==3
    assert row['low']==5 and row['high']==15
    assert t.run.initial_predictions['n']['calibration']['axis']=='frames'
    assert row['calibration']['axis']=='resolved frames'
    h.close()
print('PASS: immutable startup node forecast, provenance/sample counts/ranges and actual-duration comparison')
