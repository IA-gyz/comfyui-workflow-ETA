import sys,tempfile,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker

class Clock:
    now=100.
    def __call__(self):return self.now
with tempfile.TemporaryDirectory() as path:
    history=WorkflowETAHistory(path+'/h.sqlite');clock=Clock();sent=[]
    server=types.SimpleNamespace(send_sync=lambda *args:sent.append(args))
    graph={'1':{'class_type':'Example','inputs':{}}}
    tracker=WorkflowETATracker(server,history,find_prompt_fn=lambda pid:(graph,['1']),clock=clock,wall=clock,verbose=False,start_thread=False)
    tracker._log('startup diagnostic')
    snapshot=tracker.status_snapshot()
    assert snapshot['state']=='idle' and snapshot['logs'][0]['message']=='startup diagnostic'
    assert snapshot['logs'][0]['ts']==100.
    tracker._on_start({'prompt_id':'p'});tracker._on_cached({'prompt_id':'p','nodes':[]})
    first=sent[-1][1];assert first['log_session']==snapshot['log_session']
    assert any('nodes to run' in entry['message'] for entry in first['logs'])
    tracker._push(tracker.run,force=True);assert not sent[-1][1]['logs']
    tracker._log('new diagnostic');tracker._push(tracker.run,force=True)
    assert len(sent[-1][1]['logs'])==1 and sent[-1][1]['logs'][0]['prompt_id']=='p'
    tracker._on_executing({'prompt_id':'p','node':'1'});clock.now+=1
    tracker._on_success({'prompt_id':'p'})
    assert sent[-1][1]['state']=='done'
    assert any('ETA check' in entry['message'] for entry in sent[-1][1]['logs'])
    assert any('run done' in entry['message'] for entry in tracker.status_snapshot()['logs'])
    for i in range(400):tracker._log('entry '+str(i))
    assert len(tracker.status_snapshot()['logs'])==250
    tracker._log('x'*1000);assert len(tracker.status_snapshot()['logs'][-1]['message'])==700
    # Failed pushes retain log deltas for delivery on retry.
    tracker._on_start({'prompt_id':'q'})
    previous=tracker._last_log_sent
    server.send_sync=lambda *args:(_ for _ in ()).throw(RuntimeError('simulated delivery failure'))
    tracker._push(tracker.run,force=True);assert tracker._last_log_sent==previous
    server.send_sync=lambda *args:sent.append(args)
    tracker._push(tracker.run,force=True)
    assert any('push failed' in entry['message'] for entry in sent[-1][1]['logs'])
    other=WorkflowETATracker(server,history,verbose=False,start_thread=False)
    assert other.log_session!=tracker.log_session
    history.close()
print('PASS: timestamped logs with quiet console, idle/status snapshots, delta delivery, terminal accuracy messages, prompt association, bounds, retries and server session isolation')
