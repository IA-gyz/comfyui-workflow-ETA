"""Optional machine-state observations; no model loading, tensor reads or CUDA init."""
import functools
import json
import math
import sys
import time
import threading


def snapshot(device_index=None):
    data={'models':[],'unavailable':[]}
    torch=sys.modules.get('torch');cuda=getattr(torch,'cuda',None)
    data['software']={'torch':str(getattr(torch,'__version__','unknown')),'cuda':str(getattr(getattr(torch,'version',None),'cuda',None))}
    try:
        with open('/proc/driver/nvidia/version') as f:data['software']['nvidia_driver']=f.readline(1024).strip()
    except OSError:pass
    try:
        if cuda is not None and cuda.is_initialized():
            device=cuda.current_device() if device_index is None else device_index;free,total=cuda.mem_get_info(device)
            allocated=cuda.memory_allocated(device);reserved=cuda.memory_reserved(device)
            data['gpu']={'device':device,'free':int(free),'total':int(total),'allocated':int(allocated),'reserved':int(reserved),
                         'available_including_allocator':int(min(total,free+max(0,reserved-allocated)))}
        else:data['unavailable'].append('CUDA not initialized; left unchanged')
    except Exception:data['unavailable'].append('GPU memory counters unavailable')
    mm=sys.modules.get('comfy.model_management')
    if mm is not None:
        data['vram_mode']=str(getattr(mm,'vram_state','unknown'))
        for loaded in list(getattr(mm,'current_loaded_models',()))[:32]:
            try:
                total=loaded.model_memory();resident=loaded.model_loaded_memory()
                if type(total) is not int or type(resident) is not int or total<=0:continue
                patcher=loaded.model;inner=getattr(patcher,'model',None)
                data['models'].append({'class':type(inner).__name__,'device':str(getattr(loaded,'device','unknown')),
                    'total_bytes':total,'resident_bytes':max(0,resident),'offloaded_bytes':max(0,total-resident)})
            except Exception:data['unavailable'].append('A loaded-model size is unavailable')
        data['residency_source']='ComfyUI loaded-model counters; not checkpoint identity proof'
    try:
        with open('/proc/meminfo') as f:lines=f.read(16384).splitlines()
        values={line.split(':')[0]:int(line.split()[1])*1024 for line in lines if len(line.split())>=2}
        data['ram']={k:values[k] for k in ('MemTotal','MemAvailable','SwapTotal','SwapFree') if k in values}
    except (OSError,ValueError):data['unavailable'].append('RAM counters unavailable')
    args=getattr(sys.modules.get('comfy.cli_args'),'args',None)
    if args is not None:
        data['execution_options']={k:v for k,v in vars(args).items() if type(v) in (bool,int,float,str) and
            any(word in k for word in ('attention','vram','offload','dynamic','cuda_malloc'))}
    return data


def context(data):
    """Coarse comparable regimes, not an invented seconds/VRAM scaling law."""
    def pressure(free,total):
        if type(free) not in (int,float) or type(total) not in (int,float) or total<=0:return 'unknown'
        fraction=free/total
        return 'tight' if fraction<.05 else 'limited' if fraction<.2 else 'available'
    gpu=data.get('gpu',{});ram=data.get('ram',{})
    models=[]
    for model in data.get('models',[]):
        total=model['total_bytes'];resident=model['resident_bytes']
        state='offloaded' if resident==0 else 'partial' if resident<total*.98 else 'resident'
        models.append((model['class'],model['device'],total,state))
    return {'schema':'machine-v1','gpu_pressure':pressure(gpu.get('available_including_allocator'),gpu.get('total')),
        'ram_pressure':pressure(ram.get('MemAvailable'),ram.get('MemTotal')),'models':sorted(models),
        'vram_mode':data.get('vram_mode','unknown'),'software':data.get('software',{}),'options':data.get('execution_options',{})}


def install_model_observer(tracker,mm=None):
    if mm is None:mm=sys.modules.get('comfy.model_management')
    original=getattr(mm,'load_models_gpu',None)
    if not callable(original):return False
    if getattr(original,'_workflow_eta_model_observer',False):original._workflow_eta_tracker=tracker;return True
    local=threading.local()
    @functools.wraps(original)
    def wrapped(*args,**kwargs):
        if wrapped._workflow_eta_tracker is None:return original(*args,**kwargs)
        if getattr(local,'busy',False):return original(*args,**kwargs)
        local.busy=True;target=wrapped._workflow_eta_tracker;pid=nid=None
        try:
            with target.lock:
                run=target.run;pid=run.prompt_id if run else None
                nid=run.cur['node'] if run and run.cur else None
        except Exception:pass
        start=time.monotonic()
        try:return original(*args,**kwargs)
        finally:
            local.busy=False
            try:target.observe_model_management(pid,nid,time.monotonic()-start,target.machine_fn() if getattr(target,'machine_fn',None) else snapshot())
            except Exception:pass
    wrapped._workflow_eta_model_observer=True;wrapped._workflow_eta_tracker=tracker
    mm.load_models_gpu=wrapped
    return True
