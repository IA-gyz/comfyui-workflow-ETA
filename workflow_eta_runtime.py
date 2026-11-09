"""Bounded, metadata-only runtime observations. Never decode/copy/read tensors."""
import functools
import hashlib
import inspect
import itertools
import json
import math
import sys


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def describe_inputs(inputs):
    """Return JSON-only shapes. Bounds fail closed for learning; no retained tensors."""
    remaining=[256];complete=[True];media=[False]
    tensor=getattr(sys.modules.get('torch'),'Tensor',())
    nested=getattr(sys.modules.get('comfy.nested_tensor'),'NestedTensor',None)
    def visit(value,depth=0,key=''):
        remaining[0]-=1
        if depth>6 or remaining[0]<0:
            complete[0]=False;return {'truncated':True}
        if nested is not None and type(value) is nested:
            # ComfyUI's container stores video AND audio components. Inspect only
            # their metadata; no unbind/to/cpu calls or tensor-content reads.
            parts=vars(value).get('tensors')
            if type(parts) is not list or not parts or len(parts)>16:
                complete[0]=False;return {'unknown_nested_shape':True}
            return {'nested_tensors':[visit(v,depth+1,key) for v in parts]}
        if tensor and isinstance(value,tensor):
            shape=tuple(value.shape)
            if len(shape)>12 or any(type(n) is not int or n<0 for n in shape):
                complete[0]=False;return {'unknown_shape':True}
            media[0]=True
            return {'tensor_shape':list(shape),'dtype':str(value.dtype)}
        if type(value) is dict:
            if len(value)>32:complete[0]=False
            # Hidden graph/auth inputs must never be inspected or retained.
            return {str(k):visit(v,depth+1,str(k)) for k,v in itertools.islice(value.items(),32)
                    if type(k) is str and not any(s in k.lower() for s in ('prompt','auth','token','cookie','secret','seed','noise_seed'))}
        if type(value) in (list,tuple):
            if len(value)>32:complete[0]=False
            return {'length':len(value),'items':[visit(v,depth+1,key) for v in value[:32]]}
        if type(value) in (int,float) and math.isfinite(value) and key in {
            'steps','width','height','length','frames','frame_count','fps','batch_size','denoise','cfg','start_at_step','end_at_step'}:
            return {'scalar':value}
        if value is None:return None
        return {'type':type(value).__module__+'.'+type(value).__name__}
    summary=visit(inputs)
    return {'inputs':summary,'complete':complete[0],'has_tensors':media[0]}


def describe_outputs(outputs,obj):
    summary=describe_inputs(outputs)
    images=[];scalars=[]
    types=getattr(obj,'RETURN_TYPES',())
    names=getattr(obj,'RETURN_NAMES',())
    if type(outputs) not in (tuple,list):return summary
    tensor=getattr(sys.modules.get('torch'),'Tensor',())
    for slot,values in enumerate(outputs[:32]):
        if type(values) not in (tuple,list):continue
        kind=types[slot] if type(types) in (tuple,list) and slot<len(types) else None
        name=names[slot] if type(names) in (tuple,list) and slot<len(names) else str(slot)
        for index,value in enumerate(values[:32]):
            if kind=='IMAGE' and tensor and isinstance(value,tensor):
                shape=tuple(value.shape)
                if len(shape)==4 and all(type(n) is int and n>=0 for n in shape):
                    images.append(dict(slot=slot,item=index,image_count=shape[0],height=shape[1],width=shape[2],channels=shape[3]))
            elif kind in ('INT','FLOAT','BOOLEAN') and type(value) in (int,float,bool) and math.isfinite(value):
                scalars.append(dict(slot=slot,item=index,count=len(values),name=name,value=value))
    if images:summary['image_batches']=images
    if scalars:summary['scalar_outputs']=scalars
    return summary


def install_runtime_observer(tracker,execution=None):
    """Observe resolved inputs at the executor boundary; leave returns/tasks unchanged."""
    if execution is None:
        try:import execution
        except ImportError:return False
    original=getattr(execution,'get_output_data',None)
    if not inspect.iscoroutinefunction(original):return False
    # Reinstallation updates the consumer, avoiding nested wrappers.
    if getattr(original,'_workflow_eta_runtime',False):
        original._workflow_eta_tracker=tracker;return True
    parameters=inspect.signature(original).parameters
    if not {'prompt_id','unique_id','obj','input_data_all'}.issubset(parameters):return False
    signature=inspect.signature(original)
    @functools.wraps(original)
    async def wrapped(*args,**kwargs):
        target=wrapped._workflow_eta_tracker
        if target is None:return await original(*args,**kwargs)
        try:
            bound=signature.bind(*args,**kwargs).arguments
            target.observe_runtime(bound['prompt_id'],str(bound['unique_id']),describe_inputs(bound['input_data_all']))
        except Exception as error:
            # Observation cannot prevent a node from executing.
            try:target._log('runtime observation skipped: '+type(error).__name__)
            except Exception:pass
        result=await original(*args,**kwargs)
        try:
            # Unresolved tasks and graph-expansion links are not tensor outputs.
            if type(result) in (tuple,list) and len(result)==4 and (result[2] is True or result[3] is True):
                target.exclude_runtime_timing(bound['prompt_id'],str(bound['unique_id']))
            elif type(result) in (tuple,list) and len(result)==4 and result[2] is False and result[3] is False:
                target.observe_runtime_output(bound['prompt_id'],str(bound['unique_id']),describe_outputs(result[0],bound['obj']))
        except Exception:
            pass
        return result
    wrapped._workflow_eta_runtime=True;wrapped._workflow_eta_tracker=tracker
    execution.get_output_data=wrapped
    return True


def scaling_inputs(metadata):
    """Identify only standard sampler step/temporal storage axes; no sigma reads."""
    import copy
    data=metadata.get('inputs')
    if type(data) is not dict:return None
    normalized=copy.deepcopy(data)
    def single(key):
        value=normalized.get(key,{})
        items=value.get('items',[]) if type(value) is dict else []
        return items[0] if len(items)==1 and type(items[0]) is dict else None
    sigmas=single('sigmas');steps_value=single('steps')
    steps=None
    if sigmas and len(sigmas.get('tensor_shape',[]))==1:
        steps=sigmas['tensor_shape'][0]-1
        sigmas['tensor_shape']=['<sigma-count>']
    elif steps_value and type(steps_value.get('scalar')) is int:
        steps=steps_value['scalar'];steps_value['scalar']='<steps>'
    if type(steps) is not int or not 2<=steps<=100000:return None
    shape_key=fingerprint(normalized)
    axis=None;value=None
    # Standard video latent layout is B,C,T,H,W. This is temporal STORAGE length,
    # not decoded frames. No assumed temporal/spatial VAE compression factor.
    latent=single('latent_image')
    samples=latent.get('samples') if latent else None
    shape=samples.get('tensor_shape') if type(samples) is dict else None
    # Nested audio/video storage is retained in shape_key for step-only reuse.
    # Its several temporal axes cannot safely be collapsed into one generic
    # frame-scaling axis; the explicit reference-frame plan handles that case.
    if type(shape) is list and len(shape)==5 and type(shape[2]) is int and shape[2]>0:
        axis='latent_time';value=shape[2];shape[2]='<temporal-length>'
    axis_key=fingerprint(normalized) if axis else None
    return dict(steps=steps,shape_key=shape_key,axis_key=axis_key,axis=axis,axis_value=value)
