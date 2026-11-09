import tempfile,types
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker

g={
'm':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'model.safetensors'}},
'l':{'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}},
's':{'class_type':'KSampler','inputs':{'model':['m',0],'latent_image':['l',0],'steps':8}},
'd':{'class_type':'VAEDecode','inputs':{'samples':['s',0],'vae':['m',2]}}}
with tempfile.TemporaryDirectory() as path:
 h=WorkflowETAHistory(path+'/h.sqlite');clock=[0.]
 t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda p:(g,['d']),clock=lambda:clock[0],verbose=False,start_thread=False,device='gpu',resident_fn=lambda:frozenset())
 t._on_start({'prompt_id':'p'});t._on_cached({'prompt_id':'p','nodes':['m','l']})
 t._on_executing({'prompt_id':'p','node':'s'})
 detail=t.last_payload['eta_detail'];assert detail['provisional_remaining'] is None
 started=t.last_payload['start_ts'];assert t.last_payload['expected_total'] is None
 assert 'Waiting for first step' in detail['timing_message']
 clock[0]=230.81;t._on_progress({'node':'s','value':1,'max':8})
 detail=t.last_payload['eta_detail']
 assert abs(detail['provisional_remaining']-1615.67)<.01,detail
 assert t.last_payload['start_ts']==started and abs(t.last_payload['expected_total']-1846.48)<.01
 assert t.last_payload['expected_total_incomplete']
 assert detail['known_remaining']==0 and detail['downstream_unpriced']==1
 assert detail['unpriced_nodes']==[{'node':'d','class':'VAEDecode'}]
 assert 'startup' in detail['provisional_notes'][0]
 clock[0]+=5;t._push(t.run,force=True)
 assert abs(t.last_payload['eta_detail']['provisional_remaining']-1610.67)<.01
 clock[0]=240.81;t._on_progress({'node':'s','value':2,'max':8})
 detail=t.last_payload['eta_detail'];assert detail['known_remaining']==60 and detail['provisional_remaining']==60
 assert not detail['provisional_notes'] and 'provisional speed' in detail['timing_message']
 for value in (3,4):
  clock[0]+=10;t._on_progress({'node':'s','value':value,'max':8})
 assert 'measured speed' in t.last_payload['eta_detail']['timing_message']
 logs=[r['message'] for r in t.log_entries if r['message'].startswith('ETA status:')]
 count=len(logs);t._push(t.run,force=True)
 assert len([r for r in t.log_entries if r['message'].startswith('ETA status:')])==count
 assert any('startup' in r for r in logs)
 t._on_abort({'prompt_id':'p'},'interrupted');assert t.last_payload['eta_detail'] is None
 h.close()
print('PASS: no fabricated duration, first-step startup projection, countdown, unpriced downstream nodes, regular-step replacement, stable-speed message, deduplicated status logs and terminal reset')
