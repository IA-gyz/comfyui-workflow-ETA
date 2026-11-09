"""Standalone H3 workflow regression, with synthetic nonlinear measurements."""
import copy, tempfile, types
from pathlib import Path
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker, _workflow_eta_literal

def graph(seconds):
    return {
      'duration':{'class_type':'PrimitiveFloat','inputs':{'value':seconds}},
      'length':{'class_type':'ComfyMathExpression','inputs':{'expression':'max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17','values.a':['duration',0]}},
      'frames':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'length':['length',1],'width':640,'height':384,'model':['model',0]}},
      'model':{'class_type':'UNETLoader','inputs':{'unet_name':'h3.safetensors'}},
      'flag':{'class_type':'PrimitiveBoolean','inputs':{'value':False}},
      'steps':{'class_type':'ComfySwitchNode','inputs':{'switch':['flag',0],'on_false':20,'on_true':4}},
      'scheduler':{'class_type':'BasicScheduler','inputs':{'steps':['steps',0],'scheduler':'simple','denoise':1}},
      's':{'class_type':'SamplerCustomAdvanced','inputs':{'sigmas':['scheduler',0],'latent_image':['frames',1],'seed':1}}}
class Clock:
    now=0.
    def __call__(self):return self.now

def start(h,g):
    c=Clock();t=WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a:None),h,find_prompt_fn=lambda pid:(g,['s']),clock=c,verbose=False,start_thread=False,device='gpu',resident_fn=lambda:frozenset())
    t._on_start({'prompt_id':'p'});return t,c

def record(h,seconds):
    g=graph(seconds);frames=_workflow_eta_literal(g,['length',1]);rate=(frames/56)**2
    for _ in range(2):
        t,c=start(h,g);assert t.run.step_plans['s']['steps']==20
        t._on_executing({'node':'s','prompt_id':'p'})
        for i in range(1,21):
            c.now=2+(i-1)*rate;t._on_progress({'node':'s','value':i,'max':20})
        c.now+=.1;t._on_success({'prompt_id':'p'})

with tempfile.TemporaryDirectory() as path:
    h=WorkflowETAHistory(path+'/h.sqlite')
    assert [_workflow_eta_literal(graph(s),['length',1]) for s in (2,4,6)]==[56,107,158]
    g=graph(2);g['flag']['inputs']['value']=True
    assert _workflow_eta_literal(g,['steps',0])==4
    bad=graph(2);bad['length']['inputs']['expression']='__import__("os").system("false")'
    assert _workflow_eta_literal(bad,['length',1]) is None
    record(h,4)
    t,c=start(h,graph(2));assert t.run.preds['s']['seconds'] is None
    t._on_executing({'node':'s','prompt_id':'p'});assert t.run.cur['hist_step'] is None
    record(h,2)
    t,c=start(h,graph(3));p=t.run.preds['s'];frames=t.run.shape_plans['s']['frames']
    assert p['tier']=='frames-learned' and not p['extrapolated'],p
    assert abs(p['seconds']-(2+19*(frames/56)**2+.1))<1e-7,p
    for field,value in [('width',768),('model',['other',0])]:
        g=graph(3);g['frames']['inputs'][field]=value
        if field=='model':g['other']={'class_type':'UNETLoader','inputs':{'unet_name':'other.safetensors'}}
        t,c=start(h,g);assert t.run.preds['s']['tier']!='frames-learned'
    g=graph(3);g['flag']['inputs']['value']=True
    t,c=start(h,g);assert t.run.preds['s']['tier']!='frames-learned'
    t,c=start(h,graph(20));assert t.run.preds['s']['tier']!='frames-learned'
    h.close()
    h=WorkflowETAHistory(path+'/h.sqlite');t,c=start(h,graph(3));assert t.run.preds['s']['tier']=='frames-learned';h.close()
print('PASS: H3 rounded frames, switch steps, safe expressions, nonlinear calibration, insufficient-history lower bound, model/resolution/steps isolation, distant extrapolation rejection, persistence')
