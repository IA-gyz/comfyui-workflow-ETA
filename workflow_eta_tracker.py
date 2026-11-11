"""Workflow ETA - run tracker.

Wraps the PromptServer *instance* send_sync (the class method is shadowed by an
instance attribute in current ComfyUI builds), follows execution events, keeps
a live ETA, records history for completed runs only, and pushes `workflow.eta`
websocket messages. Feeds the virtual display and logs predictions versus actual timings.

ComfyUI-specific imports are lazy so this module can be tested standalone.
"""
import os
import math
from collections import deque
import statistics
import threading
import time
import types
import uuid

try:
    from .workflow_eta_history import WorkflowETAHistory, make_workflow_eta_signature
except ImportError:  # standalone testing
    from workflow_eta_history import WorkflowETAHistory, make_workflow_eta_signature

VERBOSE = os.environ.get("WORKFLOW_ETA_VERBOSE", "1") != "0"
PUSH_EVENT = "workflow.eta"
PING_EVENT = "workflow.eta.ping"
_WEAK_TIERS = ("runtime-media-profile", "machine-unmatched", "runtime-video-profile", "runtime-step-scaled", "runtime-video-learned", "runtime-video-provisional", "runtime-few", "runtime-profile", "class", "other-state", "class-other-state", "node-other-state", "node-class", "step-scaled", "resolution-learned", "batch-learned", "frames-learned", "reference-learned")


# ------------------------------------------------------------------ helpers
def _find_workflow_eta_prompt(server, prompt_id):
    """Return (prompt_graph, outputs_to_execute) for the running prompt, or (None, None)."""
    try:
        running = getattr(getattr(server, "prompt_queue", None), "currently_running", None)
        if not running:
            return None, None
        for item in list(running.values()):
            if len(item) > 2 and str(item[1]) == str(prompt_id):
                return item[2], (item[4] if len(item) > 4 else None)
    except Exception:
        pass
    return None, None


def _workflow_eta_reachable(graph, outputs):
    """Node ids that will execute if nothing is cached: ancestors of the outputs."""
    roots = [str(o) for o in outputs] if outputs else list(graph)
    seen, stack = set(), roots
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in graph:
            continue
        seen.add(nid)
        for value in (graph[nid].get("inputs") or {}).values():
            if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], int):
                stack.append(str(value[0]))
    return seen



def _workflow_eta_model_lineage(graph, nid):
    """Collect model filenames through ancestor links, tolerating cycles."""
    models, seen, stack = set(), set(), [str(nid)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        node = graph.get(current) or {}
        inputs = node.get("inputs") or {}
        _, direct = make_workflow_eta_signature(node.get("class_type", ""), inputs)
        if direct:
            models.update(direct.split("|"))
        for value in inputs.values():
            if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], int):
                stack.append(str(value[0]))
    return tuple(sorted(models))


# Workload fingerprints track settings, not an assumed linear pixels/time law.
_WORKLOAD_KEYS = {
    "width", "height", "batch_size", "batch", "batch_count", "amount",
    "length", "num_frames", "frames", "frame_count", "batch_index",
    "scale_by", "scale", "scale_factor", "upscale_by", "factor",
    "resolution", "size", "target_size", "megapixels", "longest_edge",
    "shortest_edge", "crop", "crop_width", "crop_height", "crop_size",
    "padding", "pad_left", "pad_right", "pad_top", "pad_bottom",
    "steps", "start_at_step", "end_at_step", "denoise", "sampler_name",
    "scheduler", "cfg", "add_noise", "return_with_leftover_noise",
    "max_frames", "force_fps", "custom_width", "custom_height",
    "w", "h", "frame_load_cap", "skip_first_frames", "select_every_nth", "force_rate", "force_size",
}
_SHAPE_OPERATIONS = ("resize", "scale", "crop", "pad", "batch", "repeat", "tile")


def _workflow_eta_is_link(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and isinstance(value[1], int) and not isinstance(value[1], bool))


_SOURCE_FIELDS = ("image", "images", "image_path", "image_file", "filename", "file", "filepath", "path", "video", "video_path", "latent", "latent_path", "file_path", "input_path", "image_filename", "video_filename", "imagefile")


# Declarative adapters describe semantics, not just a node name. Third-party nodes
# can expose WORKFLOW_ETA_VIDEO_ADAPTER with this same contract; no INPUT_TYPES,
# execution function or IS_CHANGED hook is called by ETA discovery.
_VIDEO_OUTPUTS = {2:"frames",3:"fps",4:"width",5:"height",6:"duration"}
_VIDEO_ADAPTERS = {
    "PixaromaLoadVideo": {"window":"cap_then_skip", "source":"video", "outputs":_VIDEO_OUTPUTS},
    "MagikVideoLoader": {"window":"cap_then_skip", "source":"video", "outputs":_VIDEO_OUTPUTS, "magik_cursor":True},
}

def _workflow_eta_video_class(cls):
    try:
        import nodes
        return nodes.NODE_CLASS_MAPPINGS.get(cls)
    except (ImportError,AttributeError):return None

def _workflow_eta_video_adapter(node):
    cls=node.get("class_type","")
    adapter=_VIDEO_ADAPTERS.get(cls)
    if adapter is None:
        adapter=getattr(_workflow_eta_video_class(cls),"WORKFLOW_ETA_VIDEO_ADAPTER",None)
    if not isinstance(adapter,dict) or adapter.get("window")!="cap_then_skip":return None
    outputs=adapter.get("outputs")
    if (not isinstance(outputs,dict) or not isinstance(adapter.get("source"),str)
        or any(type(k) is not int or k<0 or v not in _VIDEO_OUTPUTS.values() for k,v in outputs.items())):return None
    return adapter

def _workflow_eta_video_source(graph,nid,adapter):
    node=graph[nid];params=node.get("inputs") or {}
    field=adapter.get("source","video") if adapter else None
    fields=(field,) if field else _SOURCE_FIELDS
    value=next((v for key in fields if isinstance(v:=_workflow_eta_literal(graph,params.get(key)),str) and v),None)
    if not adapter or not adapter.get("magik_cursor"):return value
    # The loader owns its cursor; only read its existing state and resolve paths.
    # Never invoke load(), IS_CHANGED(), write_state() or advance the cursor.
    if params.get("loop_through_folder") is not False and "loop_through_folder" in params:
        looping=_workflow_eta_literal(graph,params.get("loop_through_folder"))
        if looping is not True:return None
        cls=_workflow_eta_video_class(node.get("class_type"))
        helpers=getattr(getattr(cls,"load",None),"__globals__",{})
        if not all(callable(helpers.get(k)) for k in ("read_state","resolve_folder","resolve_video")):return None
        try:
            folder=_workflow_eta_literal(graph,params.get("folder",""))
            sub=_workflow_eta_literal(graph,params.get("include_subfolders",True))
            reset=_workflow_eta_literal(graph,params.get("loop_reset",""))
            identity=_workflow_eta_literal(graph,params.get("loop_identity","")) or ("legacy:"+str(nid))
            if not isinstance(folder,str) or type(sub) is not bool or not isinstance(identity,str):return None
            import folder_paths
            scope=[helpers["resolve_folder"](folder) or os.path.realpath(folder_paths.get_input_directory()),sub]
            state=helpers["read_state"](identity)
            if state.get("scope")==scope and state.get("reset")==reset:
                candidate=state.get("next")
                if isinstance(candidate,str):
                    try:return helpers["resolve_video"](candidate,folder,sub)
                    except (OSError,ValueError):pass
            return helpers["resolve_video"](value,folder,sub) if value else None
        except (ImportError,OSError,ValueError,TypeError):return None
    folder=_workflow_eta_literal(graph,params.get("folder",""))
    if not isinstance(folder,str) or value is None:return None
    if folder:
        try:
            import folder_paths
            root=os.path.expanduser(folder)
            if not os.path.isabs(root):root=os.path.join(folder_paths.get_input_directory(),root)
            root=os.path.realpath(root);path=os.path.realpath(os.path.join(root,value))
            if os.path.commonpath([root,path])!=root:return None
            return path
        except (ImportError,ValueError,OSError):return None
    return value


def _workflow_eta_literal(graph, value, seen=None, video_info=None, allow_estimated_video=False):
    """Resolve whitelisted constants, switches and bounded arithmetic only."""
    if not _workflow_eta_is_link(value):
        return value if isinstance(value, (str, int, float, bool)) else None
    seen = set() if seen is None else set(seen)
    nid, slot = str(value[0]), value[1]
    if nid in seen or len(seen) > 64:
        return None
    seen.add(nid)
    node = graph.get(nid) or {}
    cls = node.get("class_type", "")
    inputs = node.get("inputs") or {}
    observed=((video_info or {}).get(nid) or {}).get('runtime_values',{})
    if slot in observed:return observed[slot]
    resolve = lambda v: _workflow_eta_literal(graph, v, seen, video_info, allow_estimated_video)
    # Constant-like nodes can declare scalar socket conversions. Magik.Float's
    # secondary socket is read from the installed class schema, never assumed.
    if cls=="Magik.Float" and set(inputs)=={"value"}:
        definition=_workflow_eta_video_class(cls)
        outputs=getattr(definition,"RETURN_TYPES",())
        if 0<=slot<len(outputs):
            value=resolve(inputs["value"])
            if type(value) in (int,float) and math.isfinite(value) and abs(value)<=1e9:
                if outputs[slot]=="INT":return int(value)
                if outputs[slot]=="FLOAT":return float(value)
        if slot!=0:return None
    if cls=="MagikCompare" and slot==0 and inputs.get("mode")=="Values":
        a,b=resolve(inputs.get("a")),resolve(inputs.get("b"))
        relation=inputs.get("relation")
        if a is None or b is None:return None
        if relation=="==":return a==b
        if relation=="!=":return a!=b
        if type(a) not in (int,float) or type(b) not in (int,float):return None
        if not math.isfinite(a) or not math.isfinite(b):return None
        return {"<":a<b,"<=":a<=b,">":a>b,">=":a>=b}.get(relation)
    if cls == "Basic data handling: Equal" and slot == 0:
        a,b=resolve(inputs.get("value1")),resolve(inputs.get("value2"))
        return a==b if a is not None and b is not None else None
    adapter = _workflow_eta_video_adapter(node)
    if adapter and slot in adapter["outputs"] and video_info is not None:
        loaded = _workflow_eta_loaded_video_info(graph,nid,video_info,seen)
        if not loaded:return None
        key=adapter["outputs"][slot]
        if key=="frames" and not loaded.get("exact_frames"):
            return loaded.get("estimated_frames") if allow_estimated_video else None
        return loaded.get(key)
    if cls in ("ComfySwitchNode", "LazySwitchKJ") and slot == 0:
        switch = resolve(inputs.get("switch"))
        if not isinstance(switch, bool):
            return None
        return resolve(inputs.get("on_true" if switch else "on_false"))
    if cls == "ComfyMathExpression" and slot in (0, 1):
        import ast
        import operator
        expression = inputs.get("expression")
        if not isinstance(expression, str) or len(expression) > 512:
            return None
        operators = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul,
                     ast.Div:operator.truediv, ast.FloorDiv:operator.floordiv, ast.Mod:operator.mod}
        functions = {"min":min, "max":max, "round":round, "int":int, "abs":abs}
        try:
            tree = ast.parse(expression, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 128:
                return None
            def evaluate(term):
                if isinstance(term, ast.Constant) and type(term.value) in (int,float):
                    result = term.value
                elif isinstance(term, ast.Name):
                    result = resolve(inputs.get("values." + term.id))
                elif isinstance(term, ast.BinOp) and type(term.op) in operators:
                    result = operators[type(term.op)](evaluate(term.left), evaluate(term.right))
                elif isinstance(term, ast.UnaryOp) and isinstance(term.op, (ast.UAdd,ast.USub)):
                    result = evaluate(term.operand) * (-1 if isinstance(term.op,ast.USub) else 1)
                elif (isinstance(term,ast.Call) and isinstance(term.func,ast.Name)
                      and term.func.id in functions and not term.keywords and 1 <= len(term.args) <= 8):
                    result = functions[term.func.id](*(evaluate(arg) for arg in term.args))
                else:
                    raise ValueError("unsupported expression")
                if type(result) not in (int,float) or not math.isfinite(result) or abs(result)>1e9:
                    raise ValueError("unbounded expression")
                return result
            result = evaluate(tree.body)
            return int(result) if slot == 1 else float(result)
        except (ValueError,TypeError,ArithmeticError,SyntaxError,RecursionError):
            return None
    if slot != 0:
        return None
    cls = cls.lower()
    if "random" in cls or not (cls=="easy int" or any(part in cls for part in ("primitive", "constant", "integer", "float", "string", "reroute"))):
        return None
    for key in ("value", "text", "string", "number", "input", "int", "float"):
        if key in inputs:
            return resolve(inputs[key])
    return None


def _workflow_eta_video_plan(run, nid, workloads):
    """H3's explicit generated length; reference inputs stay in the family hash."""
    node = run.graph.get(nid) or {}
    if node.get("class_type") != "SamplerCustomAdvanced":
        return None
    link = (node.get("inputs") or {}).get("latent_image")
    if not _workflow_eta_is_link(link) or link[1] != 1:
        return None
    owner = str(link[0])
    source = run.graph.get(owner) or {}
    if source.get("class_type") != "MiniMaxH3ReferenceToVideo":
        return None
    frames = _workflow_eta_literal(run.graph, (source.get("inputs") or {}).get("length"),video_info=getattr(run,"video_sources",None),allow_estimated_video=True)
    if type(frames) not in (int,float) or not math.isfinite(frames) or int(frames)!=frames or not 1<=frames<=100000:
        return None
    estimated=any(raw and raw.get("uncertain_frames") and not raw.get("runtime_exact_frames") for key,raw in getattr(run,"video_sources",{}).items() if key in _workflow_eta_reachable(run.graph,[nid]))
    family = _workflow_eta_family_signature(run,nid,workloads,{owner:{"length"}},"h3-frame-family-v1"+(":estimated" if estimated else ""))
    plan={"frames":int(frames), "frames_sig":family,"frame_count_estimated":estimated}
    cross=_workflow_eta_reference_family(run,nid,owner,int(frames),workloads)
    if cross:plan.update(cross)
    return plan


def _workflow_eta_reference_family(run,nid,owner,frames,workloads):
    """Same recipe across reference files, keeping spatial/model/config context strict."""
    import copy
    seen=set();stack=[nid]
    while stack:
        current=stack.pop()
        if current in seen:continue
        seen.add(current)
        stack.extend(str(v[0]) for v in (run.graph.get(current,{}).get("inputs") or {}).values() if _workflow_eta_is_link(v))
    videos=[key for key in seen if _workflow_eta_video_adapter(run.graph.get(key,{}))]
    if len(videos)!=1:return None  # do not flatten several independent temporal axes
    video=videos[0]
    loaded=_workflow_eta_loaded_video_info(run.graph,video,run.video_sources)
    if not loaded or not loaded.get("fps"):return None
    reference_frames=loaded.get("frames") or loaded.get("estimated_frames")
    if not reference_frames:return None
    estimated=not loaded.get("exact_frames",False)
    raw=run.video_sources.get(video) or {}
    if not (raw.get("width") or loaded.get("width")) or not (raw.get("height") or loaded.get("height")):return None
    graph=copy.deepcopy(run.graph)
    adapter=_workflow_eta_video_adapter(graph[video])
    graph[video]["inputs"][adapter["source"]]="<reference-video>"
    # Cursor identifiers select files, not model workload.
    for key in ("loop_identity","loop_reset"):
        if adapter.get("magik_cursor"):graph[video]["inputs"].pop(key,None)
    # The comparison axis is loaded reference frames. Keep generated frames fixed,
    # or allow them to follow the reference at exactly one output frame per input frame.
    relation="coupled-1:1" if frames==reference_frames else "fixed-output:"+str(frames)
    adjusted={nid:copy.deepcopy(workloads.get(nid,([],False)))}
    temporal={"frames","duration","source_frames","estimated_frames","estimated_source_frames"}
    for tokens,uncertain in adjusted.values():
        for token in tokens:
            if token[0]!=video:continue
            for key,info in token[2].items():
                if key.startswith("source_video") and isinstance(info,dict):
                    for field in temporal:info.pop(field,None)
    family=_workflow_eta_family_signature(types.SimpleNamespace(graph=graph),nid,adjusted,
        {owner:{"length"}},"h3-reference-family-v1:"+relation+(":estimated" if estimated else ""))
    return {"video_sig":family,"reference_frames":reference_frames}


def _workflow_eta_rate(value):
    try:
        from fractions import Fraction
        rate=float(Fraction(str(value)))
        return rate if math.isfinite(rate) and rate>0 else None
    except (ValueError,TypeError,ZeroDivisionError,OverflowError):return None


def _workflow_eta_collect_videos(graph):
    result={};cache={}
    for nid,node in graph.items():
        cls=node.get("class_type","").lower()
        adapter=_workflow_eta_video_adapter(node)
        if not adapter and ("load" not in cls or "video" not in cls):continue
        inputs=node.get("inputs") or {}
        filename=_workflow_eta_video_source(graph,nid,adapter)
        if filename is None:continue
        if filename not in cache:
            cache[filename]=_workflow_eta_video_info(filename) if len(cache)<3 else None
        result[str(nid)]=cache[filename]
    return result


def _workflow_eta_loaded_video_info(graph,nid,video_info,seen=None):
    """Merge verified runtime fields with safe header-derived loader metadata."""
    result=_workflow_eta_static_loaded_video_info(graph,nid,video_info,seen)
    raw=video_info.get(nid) or {}
    adapter=_workflow_eta_video_adapter(graph.get(nid,{}) or {})
    if not adapter:return result
    observed=raw.get('runtime_values',{})
    values={}
    for slot,key in adapter['outputs'].items():
        value=observed.get(slot)
        if key not in ('frames','fps','width','height','duration'):continue
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:continue
        if key in ('frames','width','height') and (int(value)!=value):continue
        values[key]=int(value) if key in ('frames','width','height') else value
    if not values:return result
    result=dict(result or {'frames':None,'exact_frames':False})
    if ('fps' in values and 'frames' not in values and
            result.get('fps')!=values['fps']):
        result['frames']=None
        result['exact_frames']=False
        result.pop('estimated_frames',None)
    result.update(values)
    if 'frames' in values:
        result['exact_frames']=True
        result.pop('estimated_frames',None)
    # A new FPS alone cannot establish how many frames resampling produced.
    # With a known count it does refine duration; an explicit duration wins.
    if 'duration' not in values and ('frames' in values or 'fps' in values):
        frames,fps=result.get('frames'),result.get('fps')
        result['duration']=frames/fps if frames is not None and fps else None
    result['reason']='Actual loader outputs merged with available source metadata'
    return result


def _workflow_eta_static_loaded_video_info(graph,nid,video_info,seen=None):
    """Predict only header-derived loader outputs; never decode or execute nodes."""
    raw=video_info.get(nid)
    if not raw:return None
    params=(graph.get(nid) or {}).get("inputs") or {}
    resolve=lambda key,default=0:_workflow_eta_literal(graph,params.get(key,default),seen,video_info)
    cap,skip,forced=resolve("max_frames"),resolve("skip_first_frames"),resolve("force_fps")
    if (any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (cap,skip,forced))
            or int(cap)!=cap or int(skip)!=skip):return None
    frames=raw.get("frames");fps=_workflow_eta_rate(raw.get("frame_rate"))
    exact=type(frames) is int and frames>0 and not raw.get("uncertain_frames",False)
    if type(frames) is not int or frames<=0:
        cw,ch=resolve("custom_width"),resolve("custom_height")
        w,h=raw.get("width"),raw.get("height")
        if cw==0 and ch==0:pass
        elif type(cw) is int and type(ch) is int and cw>0 and ch>0:w,h=cw,ch
        else:w=h=None
        estimate=raw.get("estimated_frames")
        estimate=(max(0,(min(estimate,int(cap)) if cap else estimate)-int(skip))
                  if type(estimate) is int and estimate>0 and not forced else None)
        return {"frames":None,"fps":fps,"duration":None,"exact_frames":False,
                "estimated_frames":estimate,"width":w,"height":h,
                "source_duration":raw.get("duration"),"estimated_source_frames":raw.get("estimated_frames"),
                "reason":"Video header has no exact frame count; duration/FPS estimate only"}
    # A changed FPS requires actual resampling; do not invent its exact output count.
    if forced and (fps is None or abs(forced-fps)>1e-9):
        w,h=raw.get('width'),raw.get('height')
        cw,ch=resolve('custom_width'),resolve('custom_height')
        if cw==0 and ch==0:pass
        elif type(cw) is int and type(ch) is int and cw>0 and ch>0:w,h=cw,ch
        else:w=h=None
        return {"frames":None,"fps":forced,"duration":None,"exact_frames":False,
                "width":w,"height":h,"source_frames":raw.get('frames'),
                "reason":"FPS conversion requires runtime frame count"}
    frames=max(0,min(frames,int(cap)) if cap else frames)
    frames=max(0,frames-int(skip))
    w,h=raw.get("width"),raw.get("height")
    cw,ch=resolve("custom_width"),resolve("custom_height")
    if cw==0 and ch==0:pass
    elif type(cw) is int and type(ch) is int and cw>0 and ch>0:w,h=cw,ch
    else:w=h=None  # proportional resizing can vary in rounding across loader versions
    return {"frames":frames,"fps":fps,"duration":frames/fps if fps else None,
            "width":w,"height":h,"exact_frames":exact,
            "source_frames":raw.get("frames"),"max_frames":int(cap),"skip_first_frames":int(skip)}


_workflow_eta_pixaroma_info = _workflow_eta_loaded_video_info


def _workflow_eta_watch_rows(run, workloads, device):
    """Expose the actual fingerprint fields, not an unrelated widget inventory."""
    import json
    rows = {}
    def add(nid, key, value, group, raw=None):
        cls = run.graph.get(nid, {}).get("class_type", "Context")
        def display(v):
            return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
        rows[(nid, key)] = {"id":json.dumps([nid,cls,key]), "node":nid, "class":cls,
            "parameter":key, "value":display(value), "group":group,
            "input":display(raw) if raw is not None else None}
    add("—", "device", device, "Context")
    for nid in sorted(run.reach):
        node = run.graph.get(nid, {})
        for key,value in (node.get("inputs") or {}).items():
            if key in ("seed","noise_seed","control_after_generate","control_before_generate"):
                continue
            if value is None or isinstance(value,(bool,int,float)) or (isinstance(value,str) and
                    (len(value)<=64 or value.lower().endswith((".safetensors",".ckpt",".pt",".pth",".bin",".gguf",".sft")))):
                add(nid,key,value,"Literal")
        add(nid,"model_lineage",run.model_map.get(nid,()),"Context")
        add(nid,"cold",run.node_cold.get(nid,False),"Context")
        for token in workloads.get(nid, ([],False))[0]:
            owner,cls,controls,scalar = token
            for key,value in controls.items():
                resolved = _workflow_eta_literal(run.graph,value,video_info=run.video_sources)
                add(owner,key,resolved if resolved is not None else value,
                    "Source" if key.startswith("source_") or key=="unresolved_source" else "Workload",value)
            for owner,cls,inputs in scalar:
                for key,value in inputs.items():
                    if key not in ("seed","noise_seed","control_after_generate","control_before_generate"):
                        resolved = _workflow_eta_literal(run.graph,value,video_info=run.video_sources)
                        add(owner,key,resolved if resolved is not None else value,"Dependency",value)
        plan=run.shape_plans.get(nid,{})
        if _workflow_eta_video_adapter(node):
            loaded=_workflow_eta_loaded_video_info(run.graph,nid,run.video_sources)
            if loaded:
                for key,value in loaded.items():add(nid,"loaded_"+key,value,"Derived")
        for key in ("width","height","batch","frames","reference_frames"):
            if key in plan:add(nid,"effective_"+key,plan[key],"Derived")
        if nid in run.step_plans:add(nid,"effective_steps",run.step_plans[nid]["steps"],"Derived")
    seen, stack = set(), list(run.step_plans)
    while stack:
        nid=stack.pop()
        if nid in seen:continue
        seen.add(nid)
        for key,value in (run.graph.get(nid,{}).get("inputs") or {}).items():
            if key in ("seed","noise_seed","control_after_generate","control_before_generate"):continue
            if (nid,key) not in rows:
                resolved=_workflow_eta_literal(run.graph,value,video_info=run.video_sources)
                add(nid,key,resolved if resolved is not None else value,"Family",value)
            if _workflow_eta_is_link(value):stack.append(str(value[0]))
    return sorted(rows.values(),key=lambda r:(r["node"],r["group"],r["parameter"]))


def _workflow_eta_source_path(filename):
    if not isinstance(filename, str) or not filename or "://" in filename:
        return None
    if os.path.isabs(filename):
        return filename if os.path.isfile(filename) else None
    try:
        import folder_paths
        path = folder_paths.get_annotated_filepath(filename)
        return path if os.path.isfile(path) else None
    except Exception:
        return None


def _workflow_eta_latent_info(filename):
    """Read only the bounded safetensors header; never load tensor payloads."""
    try:
        import json
        path = _workflow_eta_source_path(filename)
        if path is None:
            return None
        with open(path, "rb") as source:
            size = int.from_bytes(source.read(8), "little")
            if not 2 <= size <= 1024 * 1024:
                return None
            header = json.loads(source.read(size))
        for key in ("latent_tensor", "samples"):
            shape = (header.get(key) or {}).get("shape")
            if (isinstance(shape, list) and len(shape) in (4, 5)
                    and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in shape)):
                return {"latent_shape": shape, "batch": shape[0]}
    except Exception:
        pass
    return None


def _workflow_eta_video_info(filename):
    """Optional bounded ffprobe metadata query, with no frame/tensor decoding."""
    try:
        import shutil
        path = _workflow_eta_source_path(filename)
        binary = shutil.which("ffprobe")
        if path is None or binary is None:
            return None
        stat = os.stat(path)
        return _workflow_eta_video_header(binary, path, stat.st_size, stat.st_mtime_ns)
    except Exception:
        return None


from functools import lru_cache

@lru_cache(maxsize=128)
def _workflow_eta_video_header(binary, path, size, mtime_ns):
    import json
    import subprocess
    try:
        result = subprocess.run([binary, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames,duration,avg_frame_rate:stream_side_data=rotation:format=duration",
            "-of", "json", "-i", path], capture_output=True, text=True, timeout=0.5, check=True)
        if len(result.stdout) > 65536:
            return None
        metadata = json.loads(result.stdout)
        stream = metadata.get("streams", [{}])[0]
        width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
        if width <= 0 or height <= 0:
            return None
        for item in stream.get("side_data_list", []):
            if abs(float(item.get("rotation", 0))) % 180 == 90:
                width, height = height, width
        count = stream.get("nb_frames")
        frames = int(count) if str(count).isdigit() else None
        duration=stream.get("duration") or (metadata.get("format") or {}).get("duration")
        fps=_workflow_eta_rate(stream.get("avg_frame_rate"))
        estimated=None
        try:
            seconds=float(duration)
            if frames is None and fps and math.isfinite(seconds) and seconds>0:estimated=round(seconds*fps)
        except (TypeError,ValueError,OverflowError):pass
        return {"width": width, "height": height, "frames": frames,
                "duration": duration, "frame_rate": stream.get("avg_frame_rate"),
                "estimated_frames":estimated,"uncertain_frames": frames is None}
    except Exception:
        return None



def _workflow_eta_image_info(filename):
    """Inspect image metadata without decoding image tensors or using CUDA."""
    try:
        from PIL import Image
        path = _workflow_eta_source_path(filename)
        if path is None:
            return None
        with Image.open(path) as image:
            width, height = image.size
            if image.getexif().get(274) in (5, 6, 7, 8):
                width, height = height, width
            frames = int(getattr(image, "n_frames", 1))
        return {"width": width, "height": height, "frames": frames}
    except Exception:
        return None


def _workflow_eta_workload_lineage(graph, image_info_fn=None, video_info=None):
    """Return per-node (ancestor workload tokens, uncertain) without executing nodes.

    Preserve every upstream shape operation rather than guessing output tensor
    dimensions for arbitrary custom nodes. Linked shape controls include their
    scalar dependency graph. Shared image files are inspected only once.
    """
    image_info_fn = image_info_fn or _workflow_eta_image_info
    video_info = _workflow_eta_collect_videos(graph) if video_info is None else video_info
    direct, uncertain, image_cache = {}, {}, {}
    video_probes = 0
    for nid, node in graph.items():
        cls = node.get("class_type", "")
        inputs = node.get("inputs") or {}
        controls = {}
        scalar_roots = []
        shape_operation = any(word in cls.lower() for word in _SHAPE_OPERATIONS)
        for key, value in inputs.items():
            normalized = key.lower()
            shape_key = (normalized in _WORKLOAD_KEYS or
                         any(word in normalized for word in ("width", "height", "batch", "resolution", "scale_factor", "num_frames")))
            if shape_key:
                controls[key] = value
                if _workflow_eta_is_link(value):
                    scalar_roots.append(str(value[0]))
            elif shape_operation and _workflow_eta_is_link(value):
                controls[key] = value  # batch/crop/resize wiring affects output workload
            elif shape_operation and not isinstance(value, (list, tuple, dict)):
                # Include interpolation, resize mode, rounding and other custom
                # shape-operation settings, but not unrelated seeds/text.
                if normalized not in ("seed", "noise_seed", "text", "prompt"):
                    controls[key] = value
        unknown = bool(controls) and "random" in cls.lower()
        lower = cls.lower()
        adapter = _workflow_eta_video_adapter(node)
        source_kind = ("video" if adapter or "video" in lower else "latent" if "latent" in lower else "image")
        is_loader = bool(adapter) or ("load" in lower and any(kind in lower for kind in ("latent", "video", "image")))
        if is_loader:
            filename = next((resolved for key in _SOURCE_FIELDS
                             if (resolved := _workflow_eta_literal(graph, inputs.get(key)))
                             and isinstance(resolved, str)), None)
            info = None
            if filename is not None:
                cache_key = (source_kind, filename)
                if cache_key not in image_cache:
                    if source_kind == "image":
                        image_cache[cache_key] = image_info_fn(filename)
                    elif source_kind == "latent":
                        image_cache[cache_key] = _workflow_eta_latent_info(filename)
                    elif video_probes < 3:
                        video_probes += 1
                        image_cache[cache_key] = video_info.get(str(nid))
                    else:
                        image_cache[cache_key] = None
                info = image_cache[cache_key]
            if adapter:info=video_info.get(str(nid))
            if info is None:
                controls["unresolved_source"] = inputs
                unknown = True
            else:
                controls["source_" + source_kind] = ({k:v for k,v in info.items() if k not in ('runtime_values','runtime_exact_frames')} if source_kind=='video' else info)
                unknown |= bool(info.get("uncertain_frames"))
            # Custom folder/batch loaders can yield a runtime-selected collection,
            # even when the first source file's dimensions are known.
            if any(word in lower for word in ("folder", "directory", "batch", "random")):
                unknown = True
        if _workflow_eta_video_adapter(node):
            loaded=_workflow_eta_loaded_video_info(graph,str(nid),video_info)
            controls["source_video_loaded"]=loaded or {"unresolved":True}
            unknown |= not loaded or not loaded.get("exact_frames",False)
            if loaded and all(loaded.get(k) for k in ('frames','fps','width','height')) and (video_info.get(str(nid)) or {}).get('runtime_exact_frames'):
                unknown=False
        if cls=="MiniMaxH3ReferenceToVideo":
            length=_workflow_eta_literal(graph,inputs.get("length"),video_info=video_info)
            if type(length) in (int,float) and math.isfinite(length) and length>0:
                controls["effective_generated_frames"]=length
            else:unknown=True
        if controls:
            # Scalar dependencies may contain arbitrary expressions. Their input
            # fingerprints still separate deterministic parameter changes.
            seen, stack, scalar = set(), scalar_roots, []
            while stack:
                source = stack.pop()
                if source in seen:
                    continue
                seen.add(source)
                parent = graph.get(source) or {}
                parent_inputs = parent.get("inputs") or {}
                scalar.append((source, parent.get("class_type", ""), parent_inputs))
                if "random" in parent.get("class_type", "").lower():
                    unknown = True
                for value in parent_inputs.values():
                    if _workflow_eta_is_link(value):
                        stack.append(str(value[0]))
            direct[str(nid)] = (str(nid), cls, controls, sorted(scalar))
        uncertain[str(nid)] = unknown
    result = {}
    for nid in graph:
        seen, stack, tokens, unknown = set(), [str(nid)], [], False
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in direct:
                tokens.append(direct[current])
            unknown = unknown or uncertain.get(current, False)
            for value in (graph.get(current, {}).get("inputs") or {}).values():
                if _workflow_eta_is_link(value):
                    stack.append(str(value[0]))
        result[str(nid)] = (sorted(tokens), unknown)
    return result



def _workflow_eta_model_map(graph):
    """Extract each node's model literals once, then collect ancestor sets."""
    direct = {nid: set(make_workflow_eta_signature(node.get("class_type", ""),
                node.get("inputs") or {})[1].split("|")) - {""}
              for nid, node in graph.items()}
    parents = {nid: [str(v[0]) for v in (node.get("inputs") or {}).values()
                     if _workflow_eta_is_link(v)] for nid, node in graph.items()}
    result = {}
    for nid in graph:
        seen, stack, models = set(), [nid], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            models.update(direct.get(current, ()))
            stack.extend(parents.get(current, ()))
        result[nid] = tuple(sorted(models))
    return result


def _workflow_eta_progress_rates(cur):
    ticks = list(cur["ticks"])
    return [(b[0] - a[0]) / (b[1] - a[1])
            for a, b in zip(ticks, ticks[1:])
            if b[0] > a[0] and b[1] > a[1]]


# Only known single-pass sampler/scheduler interfaces are extrapolated.
_STEP_SCHEDULERS = {"BasicScheduler", "KarrasScheduler", "ExponentialScheduler",
                    "PolyexponentialScheduler", "BetaSamplingScheduler", "VPScheduler"}


def _workflow_eta_sampler_plan(run, nid, workloads, explicit_video=False):
    import hashlib
    import json
    node = run.graph.get(nid) or {}
    cls = node.get("class_type")
    owner = nid
    if cls in ("SamplerCustom", "SamplerCustomAdvanced"):
        link = (node.get("inputs") or {}).get("sigmas")
        if not _workflow_eta_is_link(link) or link[1] != 0:
            return None
        owner = str(link[0])
        if run.graph.get(owner, {}).get("class_type") not in _STEP_SCHEDULERS:
            return None
    elif cls not in ("KSampler", "KSamplerAdvanced"):
        return None
    inputs = run.graph.get(owner, {}).get("inputs") or {}
    steps = _workflow_eta_literal(run.graph, inputs.get("steps"),video_info=getattr(run,"video_sources",None))
    if not isinstance(steps, (int, float)) or isinstance(steps, bool) or not math.isfinite(steps) or steps < 1 or int(steps) != steps:
        return None
    steps = int(steps)
    if cls == "KSamplerAdvanced":
        own_inputs = node.get("inputs") or {}
        first = _workflow_eta_literal(run.graph, own_inputs.get("start_at_step", 0))
        last = _workflow_eta_literal(run.graph, own_inputs.get("end_at_step", 10000))
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or int(v) != v for v in (first,last)):
            return None
        steps = max(0, min(steps, int(last)) - max(0, int(first)))
    if steps < 1 or (nid in run.workload_unknown and not explicit_video):
        return None
    family = _workflow_eta_family_signature(run,nid,workloads,{owner:{"steps"}},"sampler-step-family-v1")
    return {"step_sig": family, "steps": steps}


def _workflow_eta_family_signature(run, nid, workloads, excluded, tag):
    import hashlib
    import json
    seen, stack, recipe = set(), [nid], []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        parent = run.graph.get(current)
        if parent is None:
            return None
        params = {}
        for key, value in (parent.get("inputs") or {}).items():
            if key in ("seed", "noise_seed", "control_after_generate", "control_before_generate"):
                continue
            if key in excluded.get(current, ()):
                continue
            params[key] = value
            if _workflow_eta_is_link(value):
                stack.append(str(value[0]))
        recipe.append((current, parent.get("class_type"), params))
    # External file dimensions are not necessarily literal node inputs.
    sources = []
    for token in workloads.get(nid, ([], False))[0]:
        if token[0] in seen:
            info = {key:value for key,value in token[2].items() if key.startswith("source_")}
            if info:
                sources.append((token[0], info))
    blob = json.dumps([tag, sorted(recipe), sorted(sources)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]

def _workflow_eta_shape_plan(run, nid, workloads):
    """Conservative adapters for explicit image-latent geometry controls."""
    import math as geometry_math
    node = run.graph.get(nid) or {}
    link = (node.get("inputs") or {}).get("latent_image")
    if not _workflow_eta_is_link(link) or link[1] != 0 or nid in run.workload_unknown:
        return None
    source = str(link[0])
    seen = set()
    def integer(value):
        v = _workflow_eta_literal(run.graph, value)
        return int(v) if isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0 and int(v)==v else None
    def infer_image(current, visited=None):
        visited=set() if visited is None else visited
        if current in visited:
            return None
        visited.add(current)
        entry=run.graph.get(current) or {}
        cls,params=entry.get("class_type"),entry.get("inputs") or {}
        if cls in ("LoadImage","LoadImageOutput"):
            for token in workloads.get(nid,([],False))[0]:
                if token[0]==current:
                    info=token[2].get("source_image") or {}
                    if info.get("frames")==1 and info.get("width",0)>0 and info.get("height",0)>0:
                        return {"width":info["width"],"height":info["height"],"batch":1,
                                "resolution_owner":None,"batch_owner":None,"batch_key":None}
        if cls=="ImageScale":
            parent=params.get("image");w=integer(params.get("width"));h=integer(params.get("height"))
            if w and h and _workflow_eta_is_link(parent) and parent[1]==0:
                shape=infer_image(str(parent[0]),visited)
                if shape:return dict(shape,width=w,height=h,resolution_owner=current)
        return None
    def infer(current):
        if current in seen:
            return None
        seen.add(current)
        entry = run.graph.get(current) or {}
        cls, inputs = entry.get("class_type"), entry.get("inputs") or {}
        if cls in ("EmptyLatentImage", "EmptySD3LatentImage"):
            w,h,b = (integer(inputs.get(key)) for key in ("width","height","batch_size"))
            if w and h and b and w%8==0 and h%8==0:
                return {"width":w,"height":h,"batch":b,"resolution_owner":current,"batch_owner":current,"batch_key":"batch_size"}
        if cls=="VAEEncode":
            parent=inputs.get("pixels")
            if _workflow_eta_is_link(parent) and parent[1]==0:
                shape=infer_image(str(parent[0]))
                # Require explicitly controlled, already aligned image dimensions.
                if (shape and shape["resolution_owner"] is not None and shape["width"]%8==0 and shape["height"]%8==0):
                    return shape
        if cls == "RepeatLatentBatch":
            parent = inputs.get("samples")
            amount = integer(inputs.get("amount"))
            if amount and _workflow_eta_is_link(parent) and parent[1]==0:
                shape=infer(str(parent[0]))
                if shape:
                    return dict(shape,batch=shape["batch"]*amount,batch_owner=current,batch_key="amount")
        if cls == "LatentUpscale":
            parent=inputs.get("samples");w=integer(inputs.get("width"));h=integer(inputs.get("height"))
            if w and h and w%8==0 and h%8==0 and _workflow_eta_is_link(parent) and parent[1]==0:
                shape=infer(str(parent[0]))
                if shape:return dict(shape,width=w,height=h,resolution_owner=current)
        return None
    shape = infer(source)
    if shape is None:
        return None
    aspect = geometry_math.gcd(shape["width"],shape["height"])
    aspect_tag = f"{shape['width']//aspect}:{shape['height']//aspect}"
    shape["resolution_sig"] = _workflow_eta_family_signature(run,nid,workloads,
        {shape["resolution_owner"]:{"width","height"}}, "sampler-resolution-v1:"+aspect_tag)
    shape["batch_sig"] = (_workflow_eta_family_signature(run,nid,workloads,
        {shape["batch_owner"]:{shape["batch_key"]}}, "sampler-batch-v1")
        if shape["batch_owner"] is not None else None)
    return shape






def workflow_eta_resident_identities():
    """Frozenset of (inner model type, model size) for every loaded model."""
    try:
        import comfy.model_management as mm
        out = set()
        for loaded in mm.current_loaded_models:
            patcher = getattr(loaded, "model", None)
            inner = getattr(patcher, "model", None)
            size_fn = getattr(patcher, "model_size", None)
            size = int(size_fn()) if callable(size_fn) else None
            out.add((type(inner).__name__, size))
        return frozenset(out)
    except Exception:
        return frozenset()


def _workflow_eta_device_name():
    try:
        import comfy.model_management as mm
        return str(mm.get_torch_device_name(mm.get_torch_device()))
    except Exception:
        return ""

def _workflow_eta_pressure_bucket(machine):
    """Coarse offload-pressure bucket from a machine snapshot: 0 <1GB, 1 = 1-5GB, 2 >=5GB offloaded."""
    if not isinstance(machine, dict):
        return None
    offloaded = sum(m.get("offloaded_bytes", 0) for m in machine.get("models", []) if isinstance(m, dict))
    if offloaded <= 0:
        return 0
    if offloaded < 5e9:
        return 1
    return 2


# ------------------------------------------------------------------ tracker
class WorkflowETATracker:
    def __init__(self, server, history, resident_fn=None, find_prompt_fn=None, device="",
                 clock=time.monotonic, wall=time.time, verbose=VERBOSE, start_thread=True, diagnostics=None):
        self.server = server
        self.diagnostics = diagnostics
        self.hist = history
        self.resident_fn = resident_fn or (lambda: frozenset())
        self.find_prompt_fn = find_prompt_fn or (lambda pid: _find_workflow_eta_prompt(server, pid))
        self.device = device
        self.clock = clock
        self.wall = wall
        self.verbose = verbose
        self.lock = threading.RLock()
        self.run = None
        self.log_session = uuid.uuid4().hex
        self.log_entries = deque(maxlen=250)
        self._log_sequence = 0
        self._last_log_sent = 0
        self.last_payload = None
        self.last_summary = None
        self.watch_baseline = None
        self.last_models = frozenset()
        self._tl = threading.local()
        self._ping_seen = False
        self._last_push = 0.0
        self._start_thread = start_thread
        self._stop_event=threading.Event()
        self._push_thread=None
        self._closed=False
        self._attached=False
        self._handlers = {
            "execution_start": self._on_start,
            "execution_cached": self._on_cached,
            "executing": self._on_executing,
            "progress": self._on_progress,
            "execution_success": self._on_success,
            "execution_error": lambda d: self._on_abort(d, "execution_error"),
            "execution_interrupted": lambda d: self._on_abort(d, "execution_interrupted"),
        }

    def _log(self, msg):
        message = str(msg)[:700]
        with self.lock:
            self._log_sequence += 1
            self.log_entries.append({"id": self._log_sequence, "ts": self.wall(),
                "prompt_id": getattr(self.run, "prompt_id", None), "message": message})
        if self.verbose:
            print(f"[workflow-eta] {message}", flush=True)
        self._diagnostic("tracker_log",{"message":str(msg)})

    def _diagnostic(self,kind,data,run=None):
        run=run or self.run
        if self.diagnostics is None or run is None or getattr(run,"diagnostic_disabled",False):return
        try:
            if kind=="start":self.diagnostics.start(run,self.device)
            else:self.diagnostics.write(run,kind,data,elapsed=self.clock()-run.t0)
        except Exception as error:
            run.diagnostic_disabled=True
            self._log(f"diagnostic recording failed; ETA tracking continues: {error!r}")

    def status_snapshot(self):
        with self.lock:
            return dict(self.last_payload or {"state": "idle"},
                        log_session=self.log_session, logs=list(self.log_entries))

    # ------------------------------------------------------- send_sync wrap
    def attach(self):
        with self.lock:
            if self._closed:raise RuntimeError('Tracker is closed')
            if self._attached:return
            self._install("startup")
            self._attached=True
            if self._start_thread:
                self._push_thread=threading.Thread(target=self._push_loop,name="workflow-eta-push",daemon=True)
                self._push_thread.start()

    def close(self):
        """Stop the worker and release retained snapshots and owned observers."""
        import sys
        with self.lock:
            if self._closed:return
            self._closed=True;self._stop_event.set()
            self.run=None;self.last_payload=None;self.last_summary=None;self.watch_baseline=None
            self.log_entries.clear();self._handlers.clear()
            for module,name in (('execution','get_output_data'),('comfy.model_management','load_models_gpu')):
                wrapper=getattr(sys.modules.get(module),name,None)
                if getattr(wrapper,'_workflow_eta_tracker',None) is self:wrapper._workflow_eta_tracker=None
            if self.server.send_sync is getattr(self,'_send_wrapper',None):
                self.server.send_sync=self._previous_send_sync
        thread=self._push_thread
        if thread is not None and thread is not threading.current_thread():thread.join(timeout=2)
        self.hist.close()

    def _install(self, reason):
        prev = self.server.send_sync
        import weakref
        tracker_ref=weakref.ref(self)

        def send_sync(event, data, *args, **kwargs):
            tracker=tracker_ref()
            if tracker is None or tracker._closed:return prev(event,data,*args,**kwargs)
            tl = tracker._tl
            if getattr(tl, "busy", False):  # re-entrant call through a wrapper chain
                return prev(event, data, *args, **kwargs)
            tl.busy = True
            try:
                tracker.on_event(event, data)
                return prev(event, data, *args, **kwargs)
            finally:
                tl.busy = False

        self._previous_send_sync=prev
        self._send_wrapper=send_sync
        self.server.send_sync = send_sync
        self._log(f"{reason}: wrapped instance send_sync "
                  f"(previous={getattr(prev, '__qualname__', repr(prev))})")

    def on_prompt(self, json_data):
        """Prompt handler: verify our wrapper is still in the chain, re-wrap if not."""
        if self._closed:return json_data
        try:
            model_installer=getattr(self,'model_installer',None)
            if model_installer:
                try:model_installer()
                except Exception:pass
            installer=getattr(self,'runtime_installer',None)
            if installer:
                try:installer()
                except Exception as error:self._log('runtime observer reinstall skipped: '+type(error).__name__)
            self._ping_seen = False
            self.server.send_sync(PING_EVENT, {})
            if not self._ping_seen:
                self._install("re-wrap (wrapper was dropped from the chain)")
        except Exception as e:
            self._log(f"on_prompt error: {e!r}")
        return json_data

    # ------------------------------------------------------------- dispatch
    def on_event(self, event, data):
        if self._closed:return
        try:
            if not isinstance(event, str) or not isinstance(data, dict):
                return  # binary preview events etc.
            if event.startswith("workflow.eta"):
                if event == PING_EVENT:
                    self._ping_seen = True
                return
            if event=="executed":
                with self.lock:
                    if self._mine(data) is not None:self._diagnostic("executed",data)
                return
            handler = self._handlers.get(event)
            if handler:
                with self.lock:
                    if event!="execution_start" and self._mine(data) is not None:
                        self._diagnostic(event,data)
                    handler(data)
        except Exception as e:
            self._log(f"tracker error on '{event}': {e!r}")

    def _mine(self, data):
        run = self.run
        pid = data.get("prompt_id")
        return run if run is not None and (pid is None or pid == run.prompt_id) else None

    def _machine_snapshot(self):
        try:return self.machine_fn()
        except Exception as error:
            if not getattr(self,'machine_error_logged',False):
                self.machine_error_logged=True;self._log('machine telemetry unavailable: '+type(error).__name__)
            return None

    def observe_model_management(self,pid,nid,seconds,machine):
        with self.lock:
            run=self.run
            if run is None or run.prompt_id!=pid:return
            if nid is not None:
                run.model_management[nid]=run.model_management.get(nid,0.)+seconds
                if run.cur and run.cur['node']==nid and run.cur.get('first_tick') is None:
                    run.model_management_first[nid]=run.model_management_first.get(nid,0.)+seconds
            self._diagnostic('model_management',{'node':nid,'seconds':seconds,'machine_after':machine,
                'scope':'load_models_gpu call; includes loading/offloading bookkeeping, not all first-step initialization'})

    def _runtime_recipe(self,run,nid,scaling=False):
        try:
            from .workflow_eta_runtime import fingerprint
        except ImportError:
            from workflow_eta_runtime import fingerprint
        recipe=[]
        for owner in sorted(_workflow_eta_reachable(run.graph,[nid])):
            entry=run.graph[owner];cls=entry.get('class_type','');inputs=dict(entry.get('inputs') or {})
            if 'load' in cls.lower() or _workflow_eta_video_adapter(entry):
                for key in _SOURCE_FIELDS:inputs.pop(key,None)
                adapter=_workflow_eta_video_adapter(entry)
                if adapter:inputs.pop(adapter['source'],None)
                for key in ('loop_identity','loop_reset'):inputs.pop(key,None)
            if scaling:
                if cls in _STEP_SCHEDULERS or cls in ('KSampler','KSamplerAdvanced'):inputs.pop('steps',None)
                if cls=='MiniMaxH3ReferenceToVideo':inputs.pop('length',None)
                if _workflow_eta_video_adapter(entry):
                    inputs.pop('max_frames',None);inputs.pop('skip_first_frames',None)
            sig,model=make_workflow_eta_signature(cls,inputs,run.model_map.get(owner,()))
            recipe.append((owner,cls,sig,model))
        machine=getattr(run,'runtime_machine',{}).get(nid,getattr(run,'machine_start',None))
        if machine is not None:
            try:from .workflow_eta_machine import context
            except ImportError:from workflow_eta_machine import context
            return fingerprint((recipe,context(machine)))
        return fingerprint(recipe)

    def _runtime_media_context(self,run,nid,temporal=False):
        sources=[]
        temporal_keys={'frames','duration','source_duration','source_frames','estimated_frames','estimated_source_frames','max_frames','skip_first_frames'}
        for owner,cls,controls,scalar in getattr(run,'workloads',{}).get(nid,([],False))[0]:
            for key in ('source_image','source_video','source_video_loaded','unresolved_source'):
                if key=='unresolved_source' and 'video' not in cls.lower():continue
                info=controls.get(key)
                if info:
                    if temporal and key.startswith('source_video') and isinstance(info,dict):
                        info={k:v for k,v in info.items() if k not in temporal_keys}
                    sources.append((owner,key,info))
        return sources

    def _runtime_video_context(self,run,nid):
        # Source spatial metadata and recipe define the queue-time hypothesis.
        # Effective frame count is the varying axis, not the filename/duration.
        try:
            from .workflow_eta_runtime import fingerprint
        except ImportError:
            from workflow_eta_runtime import fingerprint
        sources=[]
        video_count=0
        for owner,cls,controls,scalar in getattr(run,'workloads',{}).get(nid,([],False))[0]:
            if 'unresolved_source' in controls:return None
            for key in ('source_image','source_video'):
                info=controls.get(key)
                if info:
                    if key=='source_video':video_count+=1
                    if not info.get('width') or not info.get('height'):return None
                    sources.append((owner,key,{k:v for k,v in info.items() if key=='source_image' or k not in
                        ('frames','duration','source_frames','estimated_frames','estimated_source_frames','uncertain_frames','runtime_values','runtime_exact_frames')}))
        shape=run.shape_plans.get(nid,{})
        if not sources or video_count!=1 or not shape.get('frames'):return None
        relation=('coupled' if shape.get('frames')==shape.get('reference_frames') else 'fixed:'+str(shape.get('frames')))
        return fingerprint((self._runtime_recipe(run,nid,scaling=True),sources,relation,bool(shape.get('frame_count_estimated'))))

    def _project_video_history(self,run,nid,steps):
        """Bridge uncertain header counts to measured video history provisionally."""
        import copy
        shape=run.shape_plans.get(nid,{})
        frames=shape.get('reference_frames') or shape.get('frames')
        if not frames or not steps:return None
        # Rebuild each historical context using TODAY'S graph and source geometry.
        # Equality proves compatibility beyond frame count; filenames are already
        # stripped by the recipe builder. Machine mismatch is disclosed, not exact.
        for key,machine in self.hist.runtime_video_contexts(run.meta[nid][0],run.node_cold[nid],self.device,nid):
            current=getattr(run,'runtime_machine',{}).get(nid,getattr(run,'machine_start',None))
            if (current is None)!=(machine is None):continue
            if current is not None and any(current.get(k)!=machine.get(k) for k in
                    ('software','execution_options','vram_mode')):continue
            candidate=copy.copy(run)
            candidate.shape_plans=dict(run.shape_plans)
            candidate.shape_plans[nid]=dict(shape,frame_count_estimated=False)
            candidate.runtime_machine=dict(getattr(run,'runtime_machine',{}))
            if machine is not None:candidate.runtime_machine[nid]=machine
            if self._runtime_video_context(candidate,nid)!=key:continue
            prediction=self.hist.predict_runtime_video_initial(run.meta[nid][0],key,frames,steps,run.node_cold[nid],self.device,nid)
            if prediction is None:continue
            prediction['approximate']=True
            prediction['low']=min(prediction['low'],prediction['seconds']*.5)
            prediction['high']=max(prediction['high'],prediction['seconds']*1.5)
            prediction['calibration'].update(input_source='header frame estimate bridged to measured history; actual inputs pending',
                frame_count_estimated=bool(shape.get('frame_count_estimated')),
                machine_context_reused=True)
            return prediction
        return None

    def _project_media_history(self,run,nid):
        """Reuse a measured near-identical video workload; never scale total cost."""
        import copy,statistics
        try:from .workflow_eta_runtime import fingerprint
        except ImportError:from workflow_eta_runtime import fingerprint
        def normalize(media):
            normalized=[];counts={}
            for owner,key,info in media:
                if key=='unresolved_source' or not isinstance(info,dict):return None,None
                if key.startswith('source_video'):
                    if not info.get('width') or not info.get('height'):return None,None
                    count=info.get('frames') or info.get('estimated_frames')
                    if key=='source_video_loaded' and count:counts[owner]=count
                    info={k:v for k,v in info.items() if k not in (
                        'frames','estimated_frames','source_frames','estimated_source_frames',
                        'duration','source_duration','uncertain_frames','exact_frames','reason',
                        'runtime_values','runtime_exact_frames','max_frames','skip_first_frames')}
                normalized.append((owner,key,info))
            if len(counts)!=1:return None,None
            owner,count=next(iter(counts.items()))
            return fingerprint(normalized),(owner,count)
        target,axis=normalize(self._runtime_media_context(run,nid))
        if target is None:return None
        current=getattr(run,'runtime_machine',{}).get(nid,getattr(run,'machine_start',None))
        durations=[]
        for recipe,metadata,machine,duration in self.hist.runtime_media_candidates(run.meta[nid][0],run.node_cold[nid],self.device,nid):
            if (current is None)!=(machine is None):continue
            if current is not None and any(current.get(k)!=machine.get(k) for k in ('software','execution_options','vram_mode')):continue
            context,other=normalize(metadata.get('media_context',[]))
            if context!=target or other is None or other[0]!=axis[0]:continue
            # Header rounding is tolerated only locally. A new duration remains
            # uncalibrated instead of receiving an unjustified linear multiplier.
            if (abs(other[1]-axis[1])>max(2,axis[1]*.01) or
                    abs(other[1]/axis[1]-1)>.05):continue
            candidate=copy.copy(run);candidate.runtime_machine=dict(getattr(run,'runtime_machine',{}))
            if machine is not None:candidate.runtime_machine[nid]=machine
            if recipe!=self._runtime_recipe(candidate,nid):continue
            if type(duration) in (int,float) and math.isfinite(duration) and duration>0:durations.append(duration)
        if not durations or max(durations)/min(durations)>1.5:return None
        seconds=statistics.median(durations)
        return dict(seconds=seconds,low=min(durations)*.5,high=max(durations)*1.5,n=len(durations),
            tier='runtime-media-profile',approximate=True,calibration=dict(axis='near-matching video frames',
                target=axis[1],assumption='reuse measured duration; no total-time ratio',actual_inputs_pending=True))

    def exclude_runtime_timing(self,prompt_id,nid):
        # Pending async tasks/expansions do not share the synchronous node timing
        # boundary. Keep their observed shapes, but do not calibrate scheduling time.
        with self.lock:
            run=self.run
            if run is not None and run.prompt_id==prompt_id:
                run.runtime.pop(nid,None)
                self._diagnostic('runtime_timing_excluded',{'node':nid,'reason':'pending task or graph expansion'})

    def _refresh_runtime_values(self,run,updates,include_current=False):
        """Use only authoritative singleton scalars; never execute or reread sources."""
        changed=[];sources=dict(run.video_sources)
        for owner,slot,value in updates:
            if owner not in run.value_dependencies or type(value) not in (bool,int,float):continue
            if type(value) is not bool and (not math.isfinite(value) or abs(value)>1e9):continue
            prior=_workflow_eta_literal(run.graph,[owner,slot],video_info=sources)
            if type(prior) is type(value) and prior==value:continue
            info=dict(sources.get(owner) or {})
            actual=dict(info.get('runtime_values',{}));actual[slot]=value;info['runtime_values']=actual
            adapter=_workflow_eta_video_adapter(run.graph.get(owner,{}) or {})
            if adapter and adapter['outputs'].get(slot)=='frames' and type(value) is int and value>0:info['runtime_exact_frames']=True
            sources[owner]=info;changed.append((owner,slot,value))
        if not changed:return
        frozen={rec['node'] for rec in run.done}
        if run.cur and not include_current:frozen.add(run.cur['node'])
        snapshots={key:{nid:getattr(run,key).get(nid) for nid in frozen} for key in ('meta','preds','node_cold','step_plans','shape_plans')}
        runtime_rows=[r for r in run.watch_rows if r.get('group')=='Runtime']
        previous_predictions=run.preds
        candidate=types.SimpleNamespace(**vars(run))
        candidate.video_sources=sources;candidate.node_cold=dict(run.node_cold)
        self._prepare(candidate,refresh_sources=False)
        for key,values in snapshots.items():
            target=getattr(candidate,key)
            for nid,value in values.items():
                if value is None:target.pop(nid,None)
                else:target[nid]=value
        ids={row['id'] for row in runtime_rows}
        candidate.watch_rows=[r for r in candidate.watch_rows if r['id'] not in ids]+runtime_rows
        for key in ('video_sources','workloads','meta','preds','node_cold','step_plans','shape_plans','model_map','workload_unknown','watch_rows','cold_pred'):
            setattr(run,key,getattr(candidate,key))
        if include_current and run.cur:run.cur['hist_step']=None
        self._diagnostic('runtime_values_resolved',{'values':[{'node':n,'slot':slot,'value':v} for n,slot,v in changed],
            'pending_predictions':{n:p for n,p in run.preds.items() if n not in frozen and previous_predictions.get(n)!=p}})
        self._log(f"resolved {len(changed)} runtime workload values; refreshed downstream estimates")
        self._push(run,force=True)

    def observe_runtime_output(self,prompt_id,nid,metadata):
        with self.lock:
            run=self.run
            if run is None or run.prompt_id!=prompt_id:return
            if metadata.get('has_tensors') or metadata.get('scalar_outputs'):
                self._diagnostic('runtime_outputs',{'node':nid,'metadata':metadata})
            if metadata.get('complete'):
                updates=[(nid,item['slot'],item['value']) for item in metadata.get('scalar_outputs',[])
                         if item.get('count')==1 and item.get('item')==0]
                self._refresh_runtime_values(run,updates)

    def observe_runtime(self,prompt_id,nid,metadata):
        """Actual resolved inputs, including tensors supplied from ComfyUI's cache."""
        try:
            from .workflow_eta_runtime import fingerprint
        except ImportError:
            from workflow_eta_runtime import fingerprint
        with self.lock:
            run=self.run
            if run is None or run.prompt_id!=prompt_id or nid not in run.meta:return
            self._diagnostic('runtime_inputs',{'node':nid,'metadata':metadata})
            if metadata.get('complete'):
                updates=[]
                for key,description in (metadata.get('inputs') or {}).items():
                    link=(run.graph.get(nid,{}).get('inputs') or {}).get(key)
                    if key not in _WORKLOAD_KEYS or not _workflow_eta_is_link(link) or type(description) is not dict:continue
                    values=description.get('items',[])
                    if description.get('length')==1 and len(values)==1 and type(values[0]) is dict and 'scalar' in values[0]:
                        updates.append((str(link[0]),link[1],values[0]['scalar']))
                self._refresh_runtime_values(run,updates,include_current=True)
            media_context=self._runtime_media_context(run,nid)
            # Save/CreateVideo wrap images in an opaque video object. Record only
            # when the upstream source workload is resolved; never inspect/decode
            # that object to recover its frames.
            opaque_video=(run.meta[nid][0] in ('SaveVideo','CreateVideo') and
                any(key=='source_video_loaded' and info.get('exact_frames') and info.get('frames')
                    for owner,key,info in media_context if isinstance(info,dict)) and
                not any(key=='unresolved_source' for owner,key,info in media_context))
            if not metadata.get('complete') or not (metadata.get('has_tensors') or opaque_video):return
            if getattr(self,'machine_fn',None):
                machine=self._machine_snapshot()
                if machine is not None:
                    run.runtime_machine[nid]=machine
                    self._diagnostic('machine_sample',{'node':nid,'phase':'resolved_inputs','machine':machine})
            metadata=dict(metadata,media_context=media_context)
            observation={'recipe':self._runtime_recipe(run,nid),'input_sig':fingerprint(metadata),'metadata':metadata}
            try:
                from .workflow_eta_runtime import scaling_inputs
            except ImportError:
                from workflow_eta_runtime import scaling_inputs
            # Runtime sigma count alone does not prove an arbitrary sampler's
            # algorithm. Restrict scaling to known single-pass sampler interfaces.
            node=run.graph.get(nid,{})
            cls=run.meta[nid][0]
            sigma_link=(node.get('inputs') or {}).get('sigmas')
            supported=(cls=='KSampler' or (cls in ('SamplerCustom','SamplerCustomAdvanced') and
                _workflow_eta_is_link(sigma_link) and run.graph.get(str(sigma_link[0]),{}).get('class_type') in _STEP_SCHEDULERS))
            if supported:
                plan=scaling_inputs(metadata)
                if plan:
                    plan['family']=self._runtime_recipe(run,nid,scaling=True)
                    uncontextualized_shape=plan['shape_key']
                    video_count=len({owner for owner,key,info in media_context if key.startswith('source_video')})
                    frame_plan=run.shape_plans.get(nid,{})
                    reference=frame_plan.get('reference_frames')
                    if video_count:
                        # Opaque guiders can contain reference features not visible
                        # in the latent tensor. Keep all reference axes in matches.
                        if video_count==1 and reference:
                            coupled=frame_plan.get('frames')==reference
                            geometry=plan['axis_key'] if coupled else uncontextualized_shape
                            plan['axis']='reference_frames';plan['axis_value']=reference
                            relation='coupled' if coupled else 'fixed:'+str(frame_plan.get('frames'))
                            plan['axis_key']=fingerprint((geometry,self._runtime_media_context(run,nid,temporal=True),relation)) if geometry else None
                        else:
                            plan['axis']=None;plan['axis_value']=None;plan['axis_key']=None
                    elif plan.get('axis_key'):
                        plan['axis_key']=fingerprint((plan['axis_key'],media_context))
                    plan['shape_key']=fingerprint((uncontextualized_shape,media_context))
                    plan['frame_key']=self._runtime_video_context(run,nid)
                    plan['effective_frames']=frame_plan.get('reference_frames') or frame_plan.get('frames')
                    observation['scaling']=plan
            run.runtime[nid]=observation
            import json
            row={'id':json.dumps([nid,'Runtime','resolved_inputs']), 'node':nid,'class':run.meta[nid][0],
                 'parameter':'resolved_inputs','value':json.dumps(metadata['inputs'],sort_keys=True),
                 'group':'Runtime','input':None}
            run.watch_rows=[r for r in run.watch_rows if r['id']!=row['id']]+[row]
            if nid in run.runtime_machine:
                machine_id=json.dumps([nid,'Runtime','machine_state'])
                run.watch_rows=[r for r in run.watch_rows if r['id']!=machine_id]+[dict(id=machine_id,node=nid,
                    **{'class':run.meta[nid][0]},parameter='machine_state',value=json.dumps(run.runtime_machine[nid],sort_keys=True),group='Runtime',input=None)]
            prediction=self.hist.predict_runtime(run.meta[nid][0],observation['recipe'],observation['input_sig'],
                run.node_cold.get(nid,False),self.device,nid)
            if prediction is None and observation.get('scaling'):
                scaled=self.hist.predict_runtime_scaling(run.meta[nid][0],observation['scaling'],
                    run.node_cold.get(nid,False),self.device,nid)
                if scaled:
                    current=run.preds.get(nid) or {}
                    if (scaled['tier']!='runtime-video-provisional' or current.get('tier') not in
                        ('step-scaled','frames-learned','reference-learned','runtime-video-learned')):
                        prediction=scaled
            if prediction:
                prediction=self.hist.calibrate_runtime(prediction,run.meta[nid][0],observation['recipe'],run.node_cold.get(nid,False),self.device,nid)
                observation['prediction']=dict(prediction)
                run.preds[nid]=prediction
                if run.cur and run.cur['node']==nid:run.cur['hist_step']=None
                detail=prediction.get('calibration')
                if detail:
                    row_id=json.dumps([nid,'Runtime','workload_scaling'])
                    run.watch_rows=[r for r in run.watch_rows if r['id']!=row_id]+[dict(id=row_id,node=nid,
                        **{'class':run.meta[nid][0]},parameter='workload_scaling',value=json.dumps(detail,sort_keys=True),group='Runtime',input=None)]
                    self._diagnostic('runtime_prediction',{'node':nid,'prediction':prediction,'scaling_plan':observation.get('scaling')})
                self._log(f"node {nid}: {prediction['tier']} from {prediction['n']} measurements; predicted {prediction['seconds']:.2f}s")
                self._push(run,force=True)
            elif self.hist.has_runtime_recipe(run.meta[nid][0],observation['recipe'],run.node_cold.get(nid,False),self.device,nid):
                run.preds[nid]={'seconds':None,'low':None,'high':None,'n':0,'tier':'none'}
                if run.cur and run.cur['node']==nid:run.cur['hist_step']=None
                self._log(f"node {nid}: changed runtime input shapes; no matching measured timing yet")
                self._push(run,force=True)
            elif nid in run.runtime_machine:
                prior=run.preds.get(nid) or {}
                if prior.get('tier') in ('node-exact','node-few','exact','few','runtime-profile') and prior.get('seconds') is not None:
                    seconds=prior['seconds']
                    run.preds[nid]=dict(prior,tier='machine-unmatched',low=min(prior.get('low',seconds),seconds*.5),high=max(prior.get('high',seconds),seconds*2))
                    self._log(f"node {nid}: machine state has no matched runtime timing; static history remains provisional")
                    self._push(run,force=True)

    # --------------------------------------------------------------- events
    def _on_start(self, data):
        if self.run is not None:
            self._discard("new run started before the previous one finished")
        pid = data.get("prompt_id")
        graph, outputs = self.find_prompt_fn(pid)
        resident = self.resident_fn()
        run = types.SimpleNamespace(
            prompt_id=pid, t0=self.clock(), start_ts=self.wall(), graph=graph or {}, outputs=outputs, cold_pred=not resident, runtime={}, value_dependencies=set(), runtime_machine={}, model_management={}, model_management_first={},
            resident0=resident, reach=set(), cached=set(), todo=set(), meta={}, preds={},
            node_cold={}, done=[], cur=None, samples=deque(maxlen=4096), initial=None, approximate=not graph,
            pressure=None,
        )
        run.reach = _workflow_eta_reachable(run.graph, outputs) if graph else set()
        roots=[str(value[0]) for node in run.graph.values() for key,value in (node.get('inputs') or {}).items()
               if key in _WORKLOAD_KEYS and _workflow_eta_is_link(value)]
        run.value_dependencies=_workflow_eta_reachable(run.graph,roots) if roots else set()
        run.todo = set(run.reach)
        if getattr(self,"machine_fn",None):run.machine_start=self._machine_snapshot()
        run.pressure=_workflow_eta_pressure_bucket(getattr(run,"machine_start",None))
        self.run = run
        self._prepare(run)
        self._diagnostic("start",{},run)
        if not graph:
            self._log("graph unavailable (prompt_queue lookup failed): ETA will be a lower bound")

    def _on_cached(self, data):
        run = self._mine(data)
        if run is None:
            return
        run.cached = {str(n) for n in data.get("nodes", [])} & run.reach
        run.todo = run.reach - run.cached
        # Metadata and predictions are unchanged by cache pruning.
        for nid,shape in run.shape_plans.items():
            if shape.get("frame_count_estimated"):
                self._log(f"node {nid}: frame count estimated from duration/FPS; approximate calibration kept separate from exact frame counts")
        run.cold_pred = any(run.node_cold.get(nid, False) for nid in run.todo)
        self._announce(run)

    def _on_executing(self, data):
        run = self._mine(data)
        if run is None:
            return
        now = self.clock()
        if run.initial is None:  # no execution_cached seen
            self._announce(run)
        node = data.get("node")
        if run.cur is not None and node is not None and str(node) == run.cur["node"]:
            return  # duplicate start event
        self._finish_node(run, now)
        if node is None:
            return
        nid = str(node)
        run.todo.add(nid)
        run.reach.add(nid)
        run.cached.discard(nid)
        meta = run.meta.get(nid)
        if meta is None:
            run.approximate = True  # expanded/unknown node: timed but not recorded
        hist_step = None
        if meta:
            prediction = run.preds.get(nid) or {}
            hist_step = prediction.get("step_time")
            if hist_step is None:
                stats = self.hist.sampler_stats(meta[0], meta[1], run.node_cold[nid], self.device, node_id=nid)
                video = run.shape_plans.get(nid, {}).get("frames_sig")
                link=(run.graph.get(nid,{}).get("inputs") or {}).get("latent_image")
                unresolved_h3=(_workflow_eta_is_link(link) and run.graph.get(str(link[0]),{}).get("class_type")=="MiniMaxH3ReferenceToVideo" and nid not in run.shape_plans)
                if not unresolved_h3 and (not video or stats["tier"] in ("node-exact", "node-few", "exact", "few")):
                    hist_step = stats["step_time"]
        run.cur = {"node": nid, "t0": now, "ticks": deque(maxlen=128), "max": 0, "hist_step": hist_step,
                   "first_tick": None, "first_value": None, "repeated": False}
        eta = self._workflow_eta(run, now)
        label = meta[0] if meta else "?"
        prediction = run.preds.get(nid) or {}
        self._log(f"node {nid} {label} starts; ETA {eta['remaining']:.1f}s ({eta['confidence']}; "
                  f"prediction={prediction.get('tier', 'none')}, samples={prediction.get('n', 0)})")
        self._push(run, force=True)

    def _on_progress(self, data):
        run = self._mine(data)
        if run is None or run.cur is None:
            return
        node = data.get("node", data.get("node_id"))
        if node is not None and str(node) != run.cur["node"]:
            return
        value, maximum = data.get("value"), data.get("max")
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not isinstance(maximum, (int, float)) or isinstance(maximum, bool)
                or not math.isfinite(value) or not math.isfinite(maximum)
                or maximum <= 0 or value < 0 or value > maximum):
            return
        cur = run.cur
        ticks = cur["ticks"]
        now = self.clock()
        if ticks and (value < ticks[-1][1] or maximum != cur["max"]):
            cur["repeated"] = True
            ticks.clear()
        if ticks and value == ticks[-1][1]:
            return  # duplicate progress must not skew the rate
        if cur["first_tick"] is None and value > 0:
            cur["first_tick"] = now
            cur["first_value"] = value
        ticks.append((now, value))
        cur["max"] = maximum
        self._push(run)

    def _on_success(self, data):
        run = self._mine(data)
        if run is None:
            return
        now = self.clock()
        try:
            self._finish_node(run, now)
            total = now - run.t0
            # Residency heuristic follows completed execution even if saving fails.
            self.last_models = frozenset(m for nid in run.reach for m in run.model_map.get(nid, ()))
            try:
                if run.done:
                    self._flush(run, total)
            except Exception as error:
                self._log(f"history save failed; run completed: {error!r}")
            self._accuracy_log(run, total)
            self._push(run, force=True, state="done")
            self.watch_baseline = {"prompt_id":run.prompt_id,"rows":run.watch_rows,
                "nodes":self._watch_nodes(run)}
        finally:
            self.run = None

    def _on_abort(self, data, reason):
        if self._mine(data) is not None:
            self._discard(reason)

    def _discard(self, reason):
        if self.run is not None:
            self.run.abort_reason=reason
            self._log(f"run discarded, not recorded ({reason})")
            self._push(self.run, force=True, state="aborted")
        self.run = None
        self.last_models = frozenset()  # interruption makes model residency uncertain

    # ----------------------------------------------------------- prediction
    def _prepare(self, run, refresh_sources=True):
        run.meta, run.preds, run.step_plans, run.shape_plans = {}, {}, {}, {}
        if refresh_sources:run.video_sources = _workflow_eta_collect_videos(run.graph)
        workloads = _workflow_eta_workload_lineage(run.graph,video_info=run.video_sources)
        run.workloads=workloads
        run.model_map = _workflow_eta_model_map(run.graph)
        run.workload_unknown = {nid for nid, (_, unknown) in workloads.items() if unknown}
        for nid in run.reach:
            node = run.graph.get(nid) or {}
            cls = node.get("class_type")
            if cls:
                sig, model_key = make_workflow_eta_signature(cls, node.get("inputs") or {}, run.model_map.get(nid, ()), workload=workloads.get(nid, ([], False))[0])
                run.meta[nid] = (cls, sig, model_key)
                models = set(run.model_map.get(nid, ()))
                run.node_cold[nid] = bool(models) and (
                    not run.resident0 or not models.issubset(self.last_models))
        for nid in run.reach:
            video = _workflow_eta_video_plan(run, nid, workloads)
            plan = _workflow_eta_sampler_plan(run, nid, workloads, explicit_video=video is not None)
            if plan is not None:
                run.step_plans[nid] = plan
                shape = video or _workflow_eta_shape_plan(run,nid,workloads)
                if shape is not None:
                    run.shape_plans[nid] = shape
        for nid in run.todo:
            meta = run.meta.get(nid)
            if meta:
                prediction = self.hist.predict_node(meta[0], meta[1], run.node_cold[nid], self.device, node_id=nid, pressure=getattr(run,"pressure",None))
                if nid in run.step_plans and prediction["tier"] not in ("node-exact", "node-few", "exact", "few"):
                    plan = run.step_plans[nid]
                    scaled = self.hist.predict_sampler_steps(meta[0], plan["step_sig"], plan["steps"], run.node_cold[nid], self.device, nid, pressure=getattr(run,"pressure",None))
                    if scaled is not None:
                        prediction = scaled
                if (nid in run.shape_plans and prediction["tier"] not in
                        ("node-exact","node-few","exact","few","step-scaled")):
                    learned = self.hist.predict_sampler_workload(meta[0], run.shape_plans[nid],
                        run.step_plans[nid]["steps"],run.node_cold[nid],self.device,nid)
                    if learned is not None:
                        prediction = learned
                    elif run.shape_plans[nid].get("frames_sig"):
                        prediction = {"seconds":None,"low":None,"high":None,"n":0,"tier":"none"}
                        self._log(f"node {nid}: {run.shape_plans[nid]['frames']} generated frames; insufficient matching video-length calibration; ETA is a lower bound")
                link=(run.graph.get(nid,{}).get("inputs") or {}).get("latent_image")
                h3=(_workflow_eta_is_link(link) and run.graph.get(str(link[0]),{}).get("class_type")=="MiniMaxH3ReferenceToVideo")
                if h3 and nid not in run.shape_plans:
                    prediction={"seconds":None,"low":None,"high":None,"n":0,"tier":"none"}
                if run.shape_plans.get(nid,{}).get("frame_count_estimated"):
                    prediction["approximate"]=True
                frame_key=self._runtime_video_context(run,nid)
                shape=run.shape_plans.get(nid,{})
                steps=run.step_plans.get(nid,{}).get('steps')
                if frame_key and shape.get('frames') and steps and prediction.get('tier') not in (
                    'node-exact','node-few','exact','few','step-scaled','reference-learned','frames-learned'):
                    projected=self.hist.predict_runtime_video_initial(meta[0],frame_key,shape.get('reference_frames') or shape['frames'],steps,run.node_cold[nid],self.device,nid)
                    if projected is None:
                        projected=self._project_video_history(run,nid,steps)
                    if projected:prediction=projected
                # Reuse an initial profile only for fully resolved static workloads.
                if nid not in run.workload_unknown:
                    profile=self.hist.runtime_profile(meta[0],meta[1],run.node_cold[nid],self.device,nid)
                    if profile and profile.pop("runtime_recipe",None)==self._runtime_recipe(run,nid):prediction=profile
                if nid not in run.step_plans:
                    media=self._project_media_history(run,nid)
                    if media and prediction.get('tier') not in ('node-exact','exact','runtime-exact','runtime-profile'):
                        prediction=media
                run.preds[nid] = prediction

        for nid,shape in run.shape_plans.items():
            if shape.get("frame_count_estimated"):
                self._log(f"node {nid}: frame count estimated from duration/FPS; approximate calibration kept separate from exact frame counts")
        run.cold_pred = any(run.node_cold.get(nid, False) for nid in run.todo)
        run.watch_rows = _workflow_eta_watch_rows(run,workloads,self.device)
        run.watch_baseline = self.watch_baseline

    def _announce(self, run):
        if not getattr(run,"video_announced",False):
            run.video_announced=True
            for nid in sorted(run.video_sources):
                if _workflow_eta_video_adapter(run.graph.get(nid,{})):
                    loaded=_workflow_eta_loaded_video_info(run.graph,nid,run.video_sources)
                    raw=run.video_sources.get(nid) or {}
                    if loaded and loaded.get("frames") is not None:
                        duration=f"{loaded['duration']:.3f}s" if loaded.get("duration") is not None else "duration unresolved"
                        self._log(f"reference video #{nid}: loaded {loaded['frames']} frames at {loaded.get('fps')} fps ({duration}); exact count={loaded.get('exact_frames')}")
                    else:
                        reason=loaded.get("reason","Output count unresolved") if loaded else "Readable source file and ffprobe metadata required"
                        self._log(f"reference video #{nid}: loaded frame count unresolved; source duration={raw.get('duration','unknown')}s; {reason}")
            for nid,shape in run.shape_plans.items():
                if shape.get("frames"):self._log(f"sampler #{nid}: generated length {shape['frames']} frames")
        eta = self._workflow_eta(run, self.clock())
        if not hasattr(run,'initial_predictions'):
            import copy
            run.initial_predictions=copy.deepcopy(run.preds)
        run.initial = eta["remaining"]
        initial_detail=self._eta_explanation(run,self.clock())
        run.diagnostic_initial_full=initial_detail
        run.diagnostic_initial_elapsed=self.clock()-run.t0
        self._diagnostic("prediction_snapshot",{"elapsed":self.clock()-run.t0,"known":run.initial,
            "detail":initial_detail,"cached":sorted(run.cached),"todo":sorted(run.todo),"predictions":run.preds})
        tiers = ", ".join(f"{k}={v}" for k, v in sorted(eta["tiers"].items())) or "none"
        self._log(f"run {str(run.prompt_id)[:8]} device={self.device or 'unknown'}: {len(run.todo)} nodes to run, {len(run.cached)} cached, "
                  f"{'cold' if run.cold_pred else 'warm'} start; predicted {eta['remaining']:.1f}s "
                  f"({eta['confidence']}{', lower bound' if eta['lower_bound'] else ''}; tiers: {tiers}; "
                  f"unresolved workloads: {len(run.workload_unknown & run.todo)})")
        if run.workload_unknown & run.todo:
            sources = ", ".join(f"{nid}:{run.graph.get(nid, {}).get('class_type', '?')}"
                                for nid in sorted(run.workload_unknown & run.todo)[:8])
            self._log(f"dimension metadata unresolved for: {sources}")
        self._push(run, force=True)

    def _current_remaining(self, run, cur, pred, now):
        ticks = cur["ticks"]
        if ticks and cur["max"] >= 2 and not cur.get("repeated", False):
            left = max(cur["max"] - ticks[-1][1], 0)
            rates = _workflow_eta_progress_rates(cur)
            if len(rates) >= 3:
                step = statistics.median(rates[-10:])
            else:
                step = cur["hist_step"] if cur["hist_step"] is not None else (rates[-1] if rates else None)
            if step is not None:
                return max(left * step - (now - ticks[-1][0]), 0.0)
        if pred and pred["seconds"] is not None:
            return max(pred["seconds"] - (now - cur["t0"]), 0.0)
        return None

    def _workflow_eta(self, run, now):
        done_ids = {d["node"] for d in run.done}
        cur = run.cur
        remaining = low = high = 0.0
        unknown = 0
        weak = 0.0
        unresolved_workload = False
        tiers = {}
        for nid in run.todo:
            if nid in done_ids and (cur is None or nid != cur["node"]):
                continue
            pred = run.preds.get(nid)
            if cur is not None and nid == cur["node"]:
                rem = self._current_remaining(run, cur, pred, now)
                if rem is None:
                    unknown += 1
                    continue
                remaining += rem
                rates=_workflow_eta_progress_rates(cur)
                live = len(rates) >= 3 and cur["max"] >= 2 and not cur.get("repeated", False)
                if live:
                    center=statistics.median(rates[-10:])
                    spread=max(.1,(max(rates[-10:])-min(rates[-10:]))/center) if center>0 else .5
                    low+=max(0.,rem*(1-spread));high+=rem*(1+spread)+center
                elif pred and pred.get('low') is not None and pred.get('high') is not None:
                    elapsed_node=max(0.,now-cur['t0'])
                    low+=min(rem,max(0.,pred['low']-elapsed_node));high+=max(rem,pred['high']-elapsed_node)
                else:low+=rem;high+=rem
                tier = "live" if live else (pred["tier"] if pred else "none")
                unresolved_workload |= not live and (nid in run.workload_unknown or cur.get("repeated", False))
                tiers[tier] = tiers.get(tier, 0) + 1
                if not live and (not pred or tier in _WEAK_TIERS or nid in run.workload_unknown or pred["seconds"] is None):
                    weak += rem
                continue
            if not pred or pred["seconds"] is None:
                unknown += 1
                continue
            unresolved_workload |= nid in run.workload_unknown
            remaining += pred["seconds"]
            low += pred["low"]
            high += pred["high"]
            tiers[pred["tier"]] = tiers.get(pred["tier"], 0) + 1
            if pred["tier"] in _WEAK_TIERS or nid in run.workload_unknown:
                weak += pred["seconds"]
        if unknown or run.approximate:
            confidence = "lower-bound"
        elif unresolved_workload or (remaining > 0 and weak / remaining > 0.2):
            confidence = "estimated"
        else:
            confidence = "learned"
        elapsed = now - run.t0
        return {
            "remaining": remaining, "low": low, "high": high, "lower_bound": bool(unknown or run.approximate),
            "confidence": confidence, "tiers": tiers, "elapsed": elapsed,
            "fraction": elapsed / (elapsed + remaining) if elapsed + remaining > 0 else 0.0,
        }

    def _eta_explanation(self, run, now):
        done={rec["node"] for rec in run.done}
        subtotal=provisional=0.; missing=[]; guesses=[]
        cur=run.cur
        timing="Waiting for the next node"
        if cur:
            rates=_workflow_eta_progress_rates(cur)
            if cur.get("repeated"):
                timing="Repeated passes; using whole-node history"
            elif len(rates)>=3:
                timing="Sampling: measured speed from recent steps"
            elif rates:
                timing="Sampling: provisional speed (1–2 intervals)"
            elif cur.get("first_tick") is not None:
                timing="First step done; regular speed still unknown"
            elif (run.preds.get(cur["node"]) or {}).get("seconds") is not None:
                timing="Using history until live steps arrive"
            else:
                timing="Waiting for first step; no matching history"
        for nid in run.todo:
            if nid in done and (not cur or nid!=cur["node"]):continue
            pred=run.preds.get(nid) or {}
            value=self._current_remaining(run,cur,pred,now) if cur and nid==cur["node"] else pred.get("seconds")
            if value is not None:
                if pred.get('tier')=='machine-unmatched':
                    guesses.append('Machine state has no matched runtime calibration; static timing is provisional')
                if pred.get('tier')=='runtime-video-provisional':
                    guesses.append('Provisional video compute-cost ratio; nonlinear scaling is not yet calibrated')
                elif pred.get('tier')=='runtime-video-profile':
                    guesses.append('Video projection uses source metadata; actual sampler input shapes are pending')
                    if (pred.get('calibration') or {}).get('machine_context_reused'):
                        guesses.append('Provisional history bridge: header count and memory residency may differ from measured runs')
                elif pred.get('tier')=='runtime-media-profile':
                    guesses.append('Preparation/finishing forecast uses a near-matching video length; actual inputs pending')
                subtotal+=value;provisional+=value;continue
            guess=None
            if (cur and nid==cur["node"] and not cur.get("repeated") and cur.get("ticks")
                    and cur.get("max",0)>=2 and cur.get("first_tick") is not None and cur.get("first_value",0)):
                guess=max(0.,(cur["first_tick"]-cur["t0"])/cur["first_value"] *
                    max(0,cur["max"]-cur["ticks"][-1][1])-(now-cur["ticks"][-1][0]))
                guesses.append("First-step guess includes startup; may be high")
            else:
                meta=run.meta.get(nid)
                if meta:
                    fallback=self.hist.predict_node(meta[0],meta[1],run.node_cold.get(nid,False),self.device,node_id=nid)
                    guess=fallback.get("seconds")
                    if guess is not None:
                        if cur and nid==cur["node"]:guess=max(0.,guess-(now-cur["t0"]))
                        guesses.append("Fallback guess uses a different workload")
            if guess is None:
                missing.append({"node":nid,"class":run.graph.get(nid,{}).get("class_type","Unknown")})
            else:provisional+=guess
        downstream=[entry for entry in missing if not cur or entry["node"]!=cur["node"]]
        reason=(str(len(missing))+" nodes still have no duration" if missing else
                "Missing timings filled with rough guesses" if guesses else
                "All remaining nodes have timing estimates")
        if any('Provisional video' in guess or 'Video projection' in guess for guess in guesses):
            reason='Includes provisional video workload scaling'
        if run.approximate:reason="Graph incomplete; extra work may be missing"
        return {"timing_message":timing,"coverage_message":reason,"known_remaining":round(subtotal,2),
            "provisional_remaining":round(provisional,2) if provisional>0 else None,
            "unpriced_nodes":missing,"downstream_unpriced":len(downstream),
            "provisional_notes":list(dict.fromkeys(guesses)),"graph_incomplete":run.approximate}

    # -------------------------------------------------------------- results
    def _finish_node(self, run, now):
        cur = run.cur
        if cur is None:
            return
        run.cur = None
        meta = run.meta.get(cur["node"])
        duration = now - cur["t0"]
        sampler = None
        ticks = cur["ticks"]
        rates = _workflow_eta_progress_rates(cur)
        if (meta and cur["max"] >= 2 and rates and not cur.get("repeated", False)
                and cur.get("first_tick") is not None):
            step = statistics.median(rates[-10:])
            sampler = (cur["max"], cur["first_tick"] - cur["t0"], step)
        existing = next((rec for rec in run.done if rec["node"] == cur["node"]), None)
        if existing is not None:
            existing["duration"] += duration
            existing["sampler"] = None  # resumed/expanded node is not a single sampling pass
            existing["step_sig"] = None
            existing["shape_plan"] = None
            existing["runtime"] = None
            existing["model_management_seconds"] = None
        else:
            plan = run.step_plans.get(cur["node"])
            step_sig = None
            if (sampler and plan and cur["max"] == plan["steps"] and cur.get("first_value") == 1
                    and ticks and ticks[-1][1] == cur["max"]):
                step_sig = plan["step_sig"]
            tail = max(0., now - ticks[-1][0]) if step_sig else 0.
            run.done.append({"node": cur["node"], "duration": duration, "meta": meta, "sampler": sampler,
                             "step_sig": step_sig, "tail": tail,
                             "shape_plan": run.shape_plans.get(cur["node"]) if step_sig else None,
                             "runtime":getattr(run,"runtime",{}).get(cur["node"]),
                             "runtime_tail":max(0.,now-ticks[-1][0]) if ticks else 0.,
                             "model_management_seconds":getattr(run,"model_management_first",{}).get(cur["node"]),
                             "machine":getattr(run,"runtime_machine",{}).get(cur["node"]),
                             "runtime_progress_valid":bool(sampler and cur.get('first_value')==1 and ticks and ticks[-1][1]==cur['max'])})
        pred = run.preds.get(cur["node"])
        self._diagnostic("node_end",{"node":cur["node"],"meta":meta,"duration":duration,
            "prediction":pred,"sampler":sampler,"first_progress":cur.get("first_value"),
            "progress_max":cur.get("max"),"repeated":cur.get("repeated",False),
            "recent_progress":[(timestamp-run.t0,value) for timestamp,value in ticks],"shape_plan":run.shape_plans.get(cur["node"]),
            "cumulative_duration":next((rec["duration"] for rec in run.done if rec["node"]==cur["node"]),duration)})
        if meta:
            ptxt = f"predicted {pred['seconds']:.2f}s, {pred['tier']}" if pred and pred["seconds"] is not None else "no prediction"
            extra = f", t_first {sampler[1]:.2f}s, {sampler[2] * 1000:.0f}ms/step" if sampler else ""
            self._log(f"node {cur['node']} {meta[0]}: {duration:.2f}s ({ptxt}{extra})")

    def _flush(self, run, total):
        cold = any(run.node_cold.get(rec["node"], False) for rec in run.done)
        records = [dict(rec, cold=run.node_cold.get(rec["node"], False))
                   for rec in run.done if rec["meta"]]
        self.hist.record_completed_run(records, run.prompt_id, total,
                                       len(run.done), len(run.cached), cold, self.device,
                                       pressure=getattr(run,"pressure",None))
        self.last_summary = {"initial": run.initial, "actual": total, "cold": cold}
        init = f"{run.initial:.1f}s" if run.initial is not None else "n/a"
        n_cold = sum(bool(run.node_cold.get(rec["node"])) for rec in run.done if rec["meta"])
        n_recorded = sum(bool(rec["meta"]) for rec in run.done)
        self._log(f"run done: actual {total:.2f}s, initial prediction {init}, "
                  f"recorded {n_recorded} nodes ({n_cold} cold, {n_recorded - n_cold} warm)")

    def _accuracy_log(self, run, total):
        pts = [s for s in run.samples if s[1] is not None]
        for frac in (0.1, 0.5, 0.9):
            if not pts or total <= 0:
                break
            at, predicted = min(pts, key=lambda s: abs(s[0] - frac * total))
            self._log(f"  ETA check @ {at:.1f}s: predicted {predicted:.1f}s left, actual {total - at:.1f}s left")

    def _watch_nodes(self, run):
        actual = {rec["node"]:rec.get("duration") for rec in run.done}
        return {nid:{"class":run.graph.get(nid,{}).get("class_type"),
            "predicted":pred.get("seconds"),"tier":pred.get("tier"),
            "samples":pred.get("n",0),"low":pred.get("low"),"high":pred.get("high"),
            "initial_predicted":getattr(run,'initial_predictions',{}).get(nid,{}).get('seconds'),
            "initial_tier":getattr(run,'initial_predictions',{}).get(nid,{}).get('tier'),
            "calibration":pred.get('calibration'),
            "actual":actual.get(nid),"cached":nid in run.cached,
            "unresolved":nid in run.workload_unknown}
            for nid,pred in run.preds.items()}

    def _watch_snapshot(self, run):
        previous = run.watch_baseline or {}
        old = {row["id"]:row for row in previous.get("rows",[])}
        current = {row["id"]:row for row in run.watch_rows}
        rows=[]
        for row in run.watch_rows:
            before=old.get(row["id"])
            rows.append(dict(row,previous=before["value"] if before else None,
                change=("same" if before["value"]==row["value"] else "changed") if before else
                    ("added" if previous else "baseline")))
        for key,row in old.items():
            if key not in current:rows.append(dict(row,previous=row["value"],value=None,change="removed"))
        return {"rows":rows,"nodes":self._watch_nodes(run),"previous_nodes":previous.get("nodes",{}),
            "baseline_prompt":previous.get("prompt_id"),"prompt_id":run.prompt_id}

    # ----------------------------------------------------------------- push
    def _push(self, run, force=False, state="running"):
        now = self.clock()
        if not force and now - self._last_push < 0.2:
            return
        self._last_push = now
        if state == "running":
            eta = self._workflow_eta(run, now)
            run.samples.append((eta["elapsed"], eta["remaining"]))
        else:
            eta = {"remaining": 0.0, "low": 0.0, "high": 0.0, "lower_bound": False,
                   "confidence": "learned", "fraction": 1.0 if state == "done" else 0.0}
        cur = run.cur
        meta = run.meta.get(cur["node"]) if cur else None
        done_ids = {rec["node"] for rec in run.done}
        unresolved = run.workload_unknown & (run.todo - done_ids)
        if cur is not None:
            if (len(_workflow_eta_progress_rates(cur)) >= 3 and not cur.get("repeated", False)):
                unresolved.discard(cur["node"])
            elif cur["node"] in run.workload_unknown:
                unresolved.add(cur["node"])
        unresolved_sources = [
            {"node": nid, "class": run.graph.get(nid, {}).get("class_type", "?")}
            for nid in sorted(unresolved)][:8]
        if getattr(self,'machine_fn',None) and now-getattr(run,'machine_last_sample',-math.inf)>=2:
            run.machine_last_sample=now
            try:self._diagnostic('machine_sample',{'node':cur['node'] if cur else None,'phase':'periodic','machine':self.machine_fn()})
            except Exception:pass
        detail = self._eta_explanation(run,now) if state=="running" else None
        if detail:
            message=(detail["timing_message"],detail["coverage_message"],tuple(detail["provisional_notes"]))
            if getattr(run,"eta_message",None)!=message:
                run.eta_message=message
                self._log("ETA status: "+message[0]+"; "+message[1]+("; "+"; ".join(message[2]) if message[2] else ""))
        payload = {
            "log_session": self.log_session,
            "logs": [entry for entry in self.log_entries if entry["id"] > self._last_log_sent],
            "prompt_id": run.prompt_id, "state": state,
            "start_ts": run.start_ts,
            "expected_total": (round(now-run.t0,2) if state=="done" else
                round(now-run.t0+detail["provisional_remaining"],2) if detail and detail["provisional_remaining"] is not None else None),
            "expected_total_incomplete": bool(detail and (detail["unpriced_nodes"] or detail["graph_incomplete"])),
            "elapsed": round(now - run.t0, 2), "nodes_cached": len(run.cached),
            "device": self.device,
            "workload_scaling": next((pred["tier"].split("-")[0] for nid,pred in run.preds.items()
                if state=="running" and pred.get("tier") in ("resolution-learned","batch-learned", "frames-learned", "reference-learned")
                and nid in run.todo and (nid not in done_ids or (cur and nid==cur["node"]))), None),
            "runtime_scaling": next((dict(tier=pred['tier'],calibration=pred.get('calibration')) for nid,pred in run.preds.items()
                if state=='running' and pred.get('tier') in ('runtime-step-scaled','runtime-video-learned','runtime-video-provisional','runtime-video-profile')
                and nid in run.todo and nid not in done_ids),None),
            "step_scaled": state == "running" and any(pred.get("tier") in ("step-scaled","runtime-step-scaled") and nid in run.todo and
                (nid not in done_ids or (cur and nid == cur["node"])) for nid,pred in run.preds.items()), "workload_approximate": bool(unresolved) if state == "running" else False,
            "unresolved_dimensions": unresolved_sources if state == "running" else [],
            "remaining": round(eta["remaining"], 2), "low": round(eta["low"], 2),
            "high": round(eta["high"], 2), "finish_ts": round(self.wall() + eta["remaining"], 1),
            "fraction": round(eta["fraction"], 3), "lower_bound": eta["lower_bound"],
            "confidence": eta["confidence"], "cold": run.cold_pred,
            "node": cur["node"] if cur else None, "node_class": meta[0] if meta else None,
            "nodes_done": sum(rec["node"] != (cur["node"] if cur else None) for rec in run.done), "nodes_total": len(run.todo),
            "approximate": run.approximate,
            "watched": self._watch_snapshot(run),
            "eta_detail": detail,
        }
        if state=="running":
            self._diagnostic("eta_sample",{k:v for k,v in payload.items() if k not in ("watched","logs")})
        elif not getattr(run,"diagnostic_ended",False):
            run.diagnostic_ended=True
            initial_detail=getattr(run,"diagnostic_initial_full",{})
            self._diagnostic("run_end",{"state":state,"reason":getattr(run,"abort_reason",None),"elapsed":now-run.t0,"initial_known":run.initial,
                "initial_full":initial_detail.get("provisional_remaining"),"initial_coverage":initial_detail,
                "initial_elapsed":getattr(run,"diagnostic_initial_elapsed",None),
                "initial_expected_total":(getattr(run,"diagnostic_initial_elapsed",0)+initial_detail["provisional_remaining"] if initial_detail.get("provisional_remaining") is not None else None),
                "nodes":self._watch_nodes(run),"completed_node_records":run.done,
                "unfinished_current_node":({"node":cur["node"],"observed_elapsed":now-cur["t0"]} if cur else None),
                "cached":sorted(run.cached),"final_status":{k:v for k,v in payload.items() if k not in ("watched","logs")}})
        self.last_payload = payload
        try:
            self.server.send_sync(PUSH_EVENT, payload)
            if payload["logs"]:
                self._last_log_sent = payload["logs"][-1]["id"]
        except Exception as e:
            self._log(f"push failed: {e!r}")

    def _push_loop(self):
        while not self._stop_event.wait(0.5):
            try:
                with self.lock:
                    if self.run is not None:
                        self._push(self.run, force=True)
            except Exception as e:
                self._log(f"push loop error: {e!r}")


# ------------------------------------------------------------------ install_workflow_eta
def install_workflow_eta():
    import folder_paths
    from server import PromptServer

    path = os.path.join(folder_paths.get_user_directory(), "workflow_eta", "history.sqlite")
    from .workflow_eta_diagnostics import WorkflowETADiagnostics, WorkflowETAExportBusy
    diagnostic_error=None
    try:
        diagnostics=WorkflowETADiagnostics(os.path.join(os.path.dirname(path),"diagnostics")) if os.environ.get("WORKFLOW_ETA_DIAGNOSTICS","1")!="0" else None
    except Exception as error:
        diagnostics=None;diagnostic_error=repr(error)
    tracker = WorkflowETATracker(PromptServer.instance, WorkflowETAHistory(path), resident_fn=workflow_eta_resident_identities,
                      device=_workflow_eta_device_name(),diagnostics=diagnostics)
    if diagnostic_error:tracker._log("diagnostic directory unavailable: "+diagnostic_error)
    from aiohttp import web

    @PromptServer.instance.routes.get("/workflow/eta/status")
    async def workflow_eta_status(request):
        with tracker.lock:
            return web.json_response(tracker.status_snapshot(), headers={"Cache-Control": "no-store"})

    @PromptServer.instance.routes.get("/workflow/eta/diagnostics/export")
    async def workflow_eta_export_diagnostics(request):
        if tracker.diagnostics is None:return web.json_response({"error":"Diagnostic recording is disabled"},status=409)
        try:limit=int(request.query.get("limit","20"))
        except ValueError:return web.json_response({"error":"limit must be an integer from 1 to 100"},status=400)
        if not 1<=limit<=100:return web.json_response({"error":"limit must be between 1 and 100"},status=400)
        import asyncio
        try:blob=await asyncio.to_thread(tracker.diagnostics.export,limit)
        except WorkflowETAExportBusy:
            return web.json_response({"error":"A diagnostic export is already running; try again after it finishes"},status=429)
        except Exception as error:
            tracker._log(f"diagnostic export failed: {error!r}")
            return web.json_response({"error":"Could not export diagnostic files; see tracker log"},status=500)
        return web.Response(body=blob,content_type="application/zip",headers={"Content-Disposition":'attachment; filename="Workflow_ETA_diagnostics.zip"',"Cache-Control":"no-store"})

    try:
        from .workflow_eta_runtime import install_runtime_observer
        tracker.runtime_installer=lambda:install_runtime_observer(tracker)
        runtime_enabled=tracker.runtime_installer()
        tracker._log("runtime input observation: "+("enabled" if runtime_enabled else "unavailable for this executor; static ETA remains active"))
    except Exception as error:
        tracker._log("runtime input observation unavailable: "+type(error).__name__)
    try:
        if os.environ.get("WORKFLOW_ETA_MACHINE","1")=="0":raise RuntimeError("disabled by WORKFLOW_ETA_MACHINE")
        from .workflow_eta_machine import snapshot,install_model_observer
        import re
        match=re.search(r"cuda:(\d+)",tracker.device)
        tracker.machine_fn=lambda:snapshot(int(match.group(1)) if match else None)
        tracker.model_installer=lambda:install_model_observer(tracker)
        tracker._log('model-management observation: '+('enabled' if tracker.model_installer() else 'unavailable'))
    except Exception as error:tracker._log('machine observation unavailable: '+type(error).__name__)
    tracker.attach()
    tracker._log(f"device: {tracker.device or 'unknown'}")
    PromptServer.instance.add_on_prompt_handler(tracker.on_prompt)
    tracker._log(f"history at {path}")
    return tracker
