"""Workflow ETA - persistent timing history (SQLite).

Stores per-node durations, sampler step timings and whole-run totals, each
tagged cold/warm (models resident or not at node start). The tracker is
responsible for never recording interrupted or failed runs.

Thread-safe: ComfyUI's executor thread records, the server loop reads.
"""
import hashlib
import json
import math
import os
import sqlite3
import threading
import time

SCHEMA_VERSION = 9

# Inputs that never affect timing, or that change every run.
_IGNORED_KEYS = {"seed", "noise_seed", "control_after_generate", "control_before_generate"}
_MAX_LITERAL_LEN = 64  # longer strings are prompts/text, irrelevant to timing
_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")


def make_workflow_eta_signature(class_type, inputs, lineage=(), workload=()):
    """Return (sig, model_key) for a node.

    sig: short hash of class, literal widget values and model lineage (links, seeds and long
    text excluded). model_key: sorted model filenames joined by '|'.
    """
    literals = {}
    models = list(lineage)
    for key in sorted(inputs):
        if key in _IGNORED_KEYS:
            continue
        value = inputs[key]
        if isinstance(value, (list, tuple, dict)):
            continue  # link to another node, or structured data
        if isinstance(value, str):
            if value.lower().endswith(_MODEL_EXTS):
                models.append(value)
            elif len(value) > _MAX_LITERAL_LEN:
                continue
        literals[key] = value
    signature_parts = [class_type, literals, sorted(set(models))]
    if workload:
        signature_parts.append(["workload-v1", workload])
    blob = json.dumps(signature_parts, sort_keys=True, default=str)
    sig = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return sig, "|".join(sorted(set(models)))


def _workflow_eta_weighted_quantile(values_newest_first, q, half_life):
    """Recency-weighted quantile. Sample i (0 = newest) has weight 0.5**(i/half_life)."""
    pairs = sorted(
        ((v, 0.5 ** (i / half_life)) for i, v in enumerate(values_newest_first)),
        key=lambda p: p[0],
    )
    total = sum(w for _, w in pairs)
    target = q * total
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= target:
            return value
    return pairs[-1][0]


def _workflow_eta_valid(x, allow_zero=False):
    return isinstance(x, (int, float)) and math.isfinite(x) and (x >= 0 if allow_zero else x > 0)


class WorkflowETAHistory:
    HALF_LIFE = 8.0  # samples

    def __init__(self, path, per_sig_cap=100, total_cap=50000, runs_cap=5000):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._per_sig_cap = per_sig_cap
        self._total_cap = total_cap
        self._runs_cap = runs_cap
        self._since_prune = 0
        self._init_schema()

    # ---------------------------------------------------------------- schema
    def _init_schema(self):
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS node_runs (
                    id INTEGER PRIMARY KEY, ts REAL, class_type TEXT, sig TEXT,
                    model_key TEXT, device TEXT, cold INTEGER, duration REAL, work REAL);
                CREATE INDEX IF NOT EXISTS ix_node ON node_runs (class_type, sig, cold, id);
                CREATE TABLE IF NOT EXISTS sampler_runs (
                    id INTEGER PRIMARY KEY, ts REAL, class_type TEXT, sig TEXT,
                    model_key TEXT, device TEXT, cold INTEGER, steps INTEGER,
                    t_first REAL, step_time REAL, work REAL);
                CREATE INDEX IF NOT EXISTS ix_sampler ON sampler_runs (class_type, sig, cold, id);
                CREATE TABLE IF NOT EXISTS eta_errors (
                    id INTEGER PRIMARY KEY, ts REAL, class_type TEXT, recipe TEXT, node_id TEXT,
                    device TEXT, cold INTEGER, predicted REAL, actual REAL, low REAL, high REAL, tier TEXT);
                CREATE INDEX IF NOT EXISTS ix_eta_errors ON eta_errors(class_type,recipe,node_id,device,cold,id);
                CREATE TABLE IF NOT EXISTS runtime_runs (
                    id INTEGER PRIMARY KEY, ts REAL, class_type TEXT, recipe TEXT,
                    input_sig TEXT, static_sig TEXT, node_id TEXT, device TEXT, cold INTEGER,
                    duration REAL, metadata TEXT);
                CREATE INDEX IF NOT EXISTS ix_runtime_shape ON runtime_runs(class_type,recipe,input_sig,node_id,device,cold,id);
                CREATE INDEX IF NOT EXISTS ix_runtime_profile ON runtime_runs(class_type,static_sig,node_id,device,cold,id);
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, ts REAL, prompt_id TEXT, duration REAL,
                    n_nodes INTEGER, n_cached INTEGER, cold INTEGER);
                """
            )
            runtime_columns={row[1] for row in self._db.execute("PRAGMA table_info(runtime_runs)")}
            for name,definition in (("machine","TEXT"),("model_management_seconds","REAL"),("frame_key","TEXT"),("effective_frames","INTEGER"),("family","TEXT"),("shape_key","TEXT"),("axis_key","TEXT"),("steps","INTEGER"),
                                    ("axis_value","INTEGER"),("first","REAL"),("rate","REAL"),("tail","REAL")):
                if name not in runtime_columns:self._db.execute(f"ALTER TABLE runtime_runs ADD COLUMN {name} {definition}")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_runtime_scaling ON runtime_runs(class_type,family,node_id,device,cold,id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_runtime_frame_context ON runtime_runs(class_type,frame_key,node_id,device,cold,id)")
            for table in ("node_runs", "sampler_runs"):
                columns = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
                if "node_id" not in columns:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN node_id TEXT")
                self._db.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_identity "
                    f"ON {table} (class_type, sig, node_id, device, cold, id)")
            sampler_columns = {row[1] for row in self._db.execute("PRAGMA table_info(sampler_runs)")}
            for name, definition in (("step_sig", "TEXT"), ("tail", "REAL DEFAULT 0"),
                                     ("resolution_sig", "TEXT"), ("batch_sig", "TEXT"),
                                     ("width", "INTEGER"), ("height", "INTEGER"), ("batch", "INTEGER"),
                                     ("frames_sig", "TEXT"), ("frames", "INTEGER"),
                                     ("video_sig", "TEXT"), ("reference_frames", "INTEGER")):
                if name not in sampler_columns:
                    self._db.execute(f"ALTER TABLE sampler_runs ADD COLUMN {name} {definition}")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_video_family ON sampler_runs (class_type,video_sig,steps,cold,device,node_id,id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_resolution_family ON sampler_runs (class_type,resolution_sig,steps,cold,device,node_id,id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_batch_family ON sampler_runs (class_type,batch_sig,steps,cold,device,node_id,id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS ix_step_family ON sampler_runs (class_type,step_sig,cold,device,node_id,id)")
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # --------------------------------------------------------------- writing
    def record_node(self, class_type, sig, duration, cold, device="", model_key="", work=None, node_id=None):
        if not _workflow_eta_valid(duration):
            return
        self._insert(
            "INSERT INTO node_runs (ts, class_type, sig, model_key, device, cold, duration, work, node_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), class_type, sig, model_key, device, int(bool(cold)), float(duration), work, None if node_id is None else str(node_id)),
        )

    def record_sampler(self, class_type, sig, steps, t_first, step_time, cold,
                       device="", model_key="", work=None, node_id=None):
        if not (_workflow_eta_valid(t_first, allow_zero=True) and _workflow_eta_valid(step_time) and int(steps) >= 2):
            return
        self._insert(
            "INSERT INTO sampler_runs (ts, class_type, sig, model_key, device, cold, steps, "
            "t_first, step_time, work, node_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), class_type, sig, model_key, device, int(bool(cold)), int(steps),
             float(t_first), float(step_time), work, None if node_id is None else str(node_id)),
        )

    def record_run(self, prompt_id, duration, n_nodes, n_cached, cold):
        if not _workflow_eta_valid(duration):
            return
        self._insert(
            "INSERT INTO runs (ts, prompt_id, duration, n_nodes, n_cached, cold) VALUES (?,?,?,?,?,?)",
            (time.time(), prompt_id, float(duration), int(n_nodes), int(n_cached), int(bool(cold))),
        )

    def record_completed_run(self, records, prompt_id, duration, n_nodes, n_cached, cold, device=""):
        """Atomically save all measurements for a successful run."""
        stamp = time.time()
        node_rows, sampler_rows = [], []
        for rec in records:
            cls, sig, model_key = rec["meta"]
            state = int(bool(rec["cold"]))
            if _workflow_eta_valid(rec["duration"]):
                node_rows.append((stamp, cls, sig, model_key, device, state,
                                  rec["duration"], None, str(rec["node"])))
            if rec["sampler"]:
                steps, first, rate = rec["sampler"]
                if (_workflow_eta_valid(first, allow_zero=True) and _workflow_eta_valid(rate)
                        and isinstance(steps, (int, float)) and math.isfinite(steps)
                        and steps >= 2 and int(steps) == steps):
                    shape = rec.get("shape_plan") or {}
                    sampler_rows.append((stamp, cls, sig, model_key, device, state,
                                         int(steps), first, rate, None, str(rec["node"]), rec.get("step_sig"), rec.get("tail", 0.),
                        shape.get("resolution_sig"),shape.get("batch_sig"),shape.get("width"),shape.get("height"),shape.get("batch"),shape.get("frames_sig"),shape.get("frames"),shape.get("video_sig"),shape.get("reference_frames")))
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO node_runs (ts,class_type,sig,model_key,device,cold,duration,work,node_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)", node_rows)
            self._db.executemany(
                "INSERT INTO sampler_runs (ts,class_type,sig,model_key,device,cold,steps,t_first,step_time,work,node_id,step_sig,tail,resolution_sig,batch_sig,width,height,batch,frames_sig,frames,video_sig,reference_frames) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sampler_rows)
            for rec in records:
                observation=rec.get('runtime')
                if observation and _workflow_eta_valid(rec['duration']):
                    cls,sig,model_key=rec['meta']
                    self._db.execute("INSERT INTO runtime_runs (ts,class_type,recipe,input_sig,static_sig,node_id,device,cold,duration,metadata) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (stamp,cls,observation['recipe'],observation['input_sig'],sig,str(rec['node']),device,
                         int(bool(rec['cold'])),rec['duration'],json.dumps(observation['metadata'],sort_keys=True)))
                    runtime_row=self._db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    self._db.execute("UPDATE runtime_runs SET machine=?,model_management_seconds=? WHERE id=last_insert_rowid()",
                        (json.dumps(rec.get('machine')),rec.get('model_management_seconds')))
                    forecast=observation.get('prediction') or {}
                    if _workflow_eta_valid(forecast.get('seconds')):
                        self._db.execute("INSERT INTO eta_errors (ts,class_type,recipe,node_id,device,cold,predicted,actual,low,high,tier) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (stamp,cls,observation['recipe'],str(rec['node']),device,int(bool(rec['cold'])),
                             forecast['seconds'],rec['duration'],forecast.get('low'),forecast.get('high'),forecast.get('tier')))
                    plan=observation.get('scaling');sampler=rec.get('sampler')
                    if (plan and sampler and rec.get('runtime_progress_valid') and sampler[0]==plan['steps']
                        and _workflow_eta_valid(sampler[1],True) and _workflow_eta_valid(sampler[2])):
                        self._db.execute("UPDATE runtime_runs SET family=?,shape_key=?,axis_key=?,steps=?,axis_value=?,first=?,rate=?,tail=?,frame_key=?,effective_frames=? WHERE id=?",
                            (plan['family'],plan['shape_key'],plan['axis_key'],plan['steps'],plan['axis_value'],
                             sampler[1],sampler[2],rec.get('runtime_tail',0.),plan.get('frame_key'),plan.get('effective_frames'),runtime_row))
            if _workflow_eta_valid(duration):
                self._db.execute(
                    "INSERT INTO runs (ts,prompt_id,duration,n_nodes,n_cached,cold) VALUES (?,?,?,?,?,?)",
                    (stamp, prompt_id, duration, n_nodes, n_cached, int(bool(cold))))
            self._since_prune += len(node_rows) + len(sampler_rows) + 1
            if self._since_prune >= 200:
                self._prune_locked()
                self._since_prune = 0

    def _insert(self, sql, params):
        with self._lock, self._db:
            self._db.execute(sql, params)
            self._since_prune += 1
            if self._since_prune >= 200:
                self._since_prune = 0
                self._prune_locked()

    # --------------------------------------------------------------- reading
    def _rows(self, table, cols, class_type, sig, cold, device, limit=50, node_id=None):
        where = ["class_type = ?", "cold = ?"]
        params = [class_type, int(bool(cold))]
        if sig is not None:
            where.append("sig = ?")
            params.append(sig)
        if node_id is not None:
            where.append("node_id = ?")
            params.append(str(node_id))
        if device:
            where.append("device = ?")
            params.append(device)
        sql = f"SELECT {cols} FROM {table} WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _ladder(self, table, cols, class_type, sig, cold, device, node_id=None):
        """Pick the best available sample set. Returns (rows, tier)."""
        steps = []
        if node_id is not None:
            steps.extend([(sig, cold, node_id, "node-exact", 3),
                          (sig, cold, node_id, "node-few", 1)])
        steps.extend([(sig, cold, None, "exact", 3),
                      (sig, cold, None, "few", 1),
                      ])
        if node_id is not None:
            steps.append((None, cold, node_id, "node-class", 1))
        steps.append((None, cold, None, "class", 1))
        if node_id is not None:
            steps.append((sig, not cold, node_id, "node-other-state", 1))
        steps.extend([(sig, not cold, None, "other-state", 1),
                      (None, not cold, None, "class-other-state", 1)])
        for s, c, identity, tier, minimum in steps:
            rows = self._rows(table, cols, class_type, s, c, device, node_id=identity)
            if len(rows) >= minimum:
                return rows, tier
        return [], "none"

    def predict_runtime(self, class_type, recipe, input_sig, cold, device, node_id):
        with self._lock:
            rows=self._db.execute("SELECT duration FROM runtime_runs WHERE class_type=? AND recipe=? AND input_sig=? AND cold=? AND device=? AND node_id=? ORDER BY id DESC LIMIT 30",
                (class_type,recipe,input_sig,int(bool(cold)),device,str(node_id))).fetchall()
        values=[r[0] for r in rows if _workflow_eta_valid(r[0])]
        if not values:return None
        import statistics
        seconds=_workflow_eta_weighted_quantile(values,.5,self.HALF_LIFE)
        low=_workflow_eta_weighted_quantile(values,.1,self.HALF_LIFE)*.9
        high=_workflow_eta_weighted_quantile(values,.9,self.HALF_LIFE)*1.1
        prediction=dict(seconds=seconds,low=low,high=high,n=len(values),tier='runtime-exact' if len(values)>=3 else 'runtime-few')
        return self.calibrate_runtime(prediction,class_type,recipe,cold,device,node_id)

    def calibrate_runtime(self,prediction,cls,recipe,cold,device,node_id):
        with self._lock:
            rows=self._db.execute("SELECT predicted,actual FROM eta_errors WHERE class_type=? AND recipe=? AND cold=? AND device=? AND node_id=? ORDER BY id DESC LIMIT 60",
                (cls,recipe,int(bool(cold)),device,str(node_id))).fetchall()
        errors=sorted(abs(actual-predicted)/predicted for predicted,actual in rows
                      if _workflow_eta_valid(predicted) and _workflow_eta_valid(actual))
        if len(errors)>=9:
            rank=min(len(errors)-1,math.ceil((len(errors)+1)*.9)-1)
            spread=max(.1,errors[rank]);seconds=prediction['seconds']
            prediction['low']=min(prediction['low'],max(0.,seconds*(1-spread)))
            prediction['high']=max(prediction['high'],seconds*(1+spread))
            prediction['interval_calibration']={'samples':len(errors),'relative_error_quantile':errors[rank],
                'target_coverage':.9,'note':'empirical rolling error envelope; not a statistical guarantee'}
        return prediction

    def runtime_video_contexts(self,cls,cold,device,node_id):
        """Bounded context candidates; caller must revalidate recipe/spatial hashes."""
        import json
        with self._lock:
            rows=self._db.execute("SELECT frame_key,machine FROM runtime_runs WHERE class_type=? AND cold=? AND device=? AND node_id=? AND rate>0 AND frame_key IS NOT NULL ORDER BY id DESC LIMIT 300",
                (cls,int(bool(cold)),device,str(node_id))).fetchall()
        result=[];seen=set()
        for key,machine in rows:
            if key in seen:continue
            seen.add(key)
            try:machine=json.loads(machine) if machine else None
            except (ValueError,TypeError):continue
            result.append((key,machine))
            if len(result)>=16:break
        return result

    def runtime_media_candidates(self,cls,cold,device,node_id):
        """Read-only candidates for conservative queue-time workload matching."""
        with self._lock:
            rows=self._db.execute('SELECT recipe,metadata,machine,duration FROM runtime_runs WHERE class_type=? AND cold=? AND device=? AND node_id=? ORDER BY id DESC LIMIT 100',
                (cls,int(bool(cold)),device,str(node_id))).fetchall()
        result=[]
        for recipe,metadata,machine,duration in rows:
            try:result.append((recipe,json.loads(metadata),json.loads(machine) if machine else None,duration))
            except (ValueError,TypeError):continue
        return result

    def predict_runtime_video_initial(self,cls,frame_key,frames,steps,cold,device,node_id):
        # Same recipe/source spatial context; frame count is statically resolved.
        with self._lock:
            rows=self._db.execute("SELECT shape_key,frame_key,effective_frames,first,rate,tail,model_management_seconds,metadata FROM runtime_runs WHERE class_type=? AND frame_key=? AND cold=? AND device=? AND node_id=? AND rate>0 ORDER BY id DESC LIMIT 300",
                (cls,frame_key,int(bool(cold)),device,str(node_id))).fetchall()
        # Rich nested-container observations change shape keys. Do not let the
        # legacy opaque representation invalidate a newly observed shape forever.
        # Within either representation, heterogeneous shapes still fail closed.
        representation={}
        filtered=[]
        for row in rows:
            nested='"nested_tensors"' in (row[7] or '')
            representation.setdefault(row[2],nested)
            if nested==representation[row[2]]:filtered.append(row[:7])
        rows=filtered
        plan=dict(steps=steps,shape_key=None,axis_key=frame_key,axis='effective_frames',axis_value=frames)
        prediction=self._runtime_scaling_from_rows(rows,plan,initial=True)
        if prediction:
            prediction['calibration']['projection_kind']=prediction['tier']
            prediction['tier']='runtime-video-profile'
            prediction['calibration']['input_source']='queue-time frame count; actual input shapes pending'
        return prediction

    def predict_runtime_scaling(self,cls,plan,cold,device,node_id):
        """Measured startup/rate/tail; one controlled axis, never a total-time ratio."""
        import statistics
        with self._lock:
            rows=self._db.execute("SELECT shape_key,axis_key,axis_value,first,rate,tail,model_management_seconds FROM runtime_runs WHERE class_type=? AND family=? AND cold=? AND device=? AND node_id=? AND rate>0 ORDER BY id DESC LIMIT 300",
                (cls,plan['family'],int(bool(cold)),device,str(node_id))).fetchall()
        return self._runtime_scaling_from_rows(rows,plan)

    def _runtime_scaling_from_rows(self,rows,plan,initial=False):
        import statistics
        rows=[r for r in rows if all(_workflow_eta_valid(v,True) for v in r[3:6])]
        same=[r for r in rows if r[0]==plan['shape_key']]
        steps=plan['steps']
        def result(first,rate,tail,tier,n,spread,calibration):
            seconds=first+(steps-1)*rate+tail
            if not _workflow_eta_valid(seconds):return None
            return dict(seconds=seconds,low=seconds*(1-spread),high=seconds*(1+spread),n=n,tier=tier,
                        step_time=rate,calibration=calibration)
        if same:
            first,rate,tail=[statistics.median(r[i] for r in same) for i in (3,4,5)]
            return result(first,rate,tail,'runtime-step-scaled',len(same),.25,
                          dict(axis='steps',target=steps,startup=first,step_rate=rate,tail=tail))
        target=plan.get('axis_value')
        if not target or not plan.get('axis_key'):return None
        groups={}
        for r in rows:
            if r[1]==plan['axis_key'] and r[2] and r[2]>0:groups.setdefault(r[2],[]).append(r)
        points={};initializers={}
        for x,values in groups.items():
            rates=[r[4] for r in values]
            if initial and len(set(r[0] for r in values))!=1:continue
            if max(rates)/min(rates)>1.5:continue
            points[x]=tuple(_workflow_eta_weighted_quantile([r[i] for r in values],.5,self.HALF_LIFE) for i in (3,4,5))
            measured=[min(r[3],r[6]) for r in values if len(r)>6 and _workflow_eta_valid(r[6],True)]
            if measured:initializers[x]=statistics.median(measured)
        xs=sorted(points)
        if not xs:return None
        if target in points and len(groups[target])>=2:
            return result(*points[target],'runtime-video-learned',len(groups[target]),.3,
                dict(axis=plan['axis'],target=target,measured_target=True))
        lower=[x for x in xs if x<target];upper=[x for x in xs if x>target]
        pair=None;extrapolated=not(lower and upper);limit=1.25
        if len(xs)>=3:
            edge=xs[-3:] if target>xs[-1] else xs[:3]
            slopes=[math.log(points[b][1]/points[a][1])/math.log(b/a) for a,b in zip(edge,edge[1:])]
            if all(0<=p<=4 for p in slopes) and max(slopes)-min(slopes)<=.75:limit=2.
        if lower and upper:pair=(max(lower),min(upper))
        elif len(lower)>=2 and target<=xs[-1]*limit:pair=tuple(xs[-2:])
        elif len(upper)>=2 and target>=xs[0]/limit:pair=tuple(xs[:2])
        if pair:
            a,b=pair;left,right=points[a],points[b]
            slope=math.log(right[1]/left[1])/math.log(b/a)
            if not 0<=slope<=4:return None  # measured discontinuity is not sparse ignorance
            supported=(len(xs)>=3 or all(len(groups[x])>=2 for x in pair))
            if supported and b/a<=4 and 0<=slope<=4:
                weight=math.log(target/a)/math.log(b/a)
                # Separate first-step compute from fixed initialization overhead.
                linit=initializers.get(a,max(0.,left[0]-left[1]));rinit=initializers.get(b,max(0.,right[0]-right[1]))
                lparts=(linit,left[0]-linit,left[1],left[2])
                rparts=(rinit,right[0]-rinit,right[1],right[2])
                components=[x*(y/x)**weight if x>0 and y>0 else max(0.,x+(y-x)*weight) for x,y in zip(lparts,rparts)]
                startup,first_compute,rate,tail=components
                return result(startup+first_compute,rate,tail,'runtime-video-learned',sum(len(groups[x]) for x in pair),
                    .5 if extrapolated else .3,dict(axis=plan['axis'],target=target,anchors=list(pair),power_slope=slope,extrapolated=extrapolated,initialization_source='measured model management' if a in initializers and b in initializers else 'first-step residual'))
        # Sparse-data fallback is a provisional ratio of COMPUTE cost only.
        # Startup/finishing costs stay separate. This is not a learned video law.
        if len(xs)>=2 and extrapolated and pair is None:return None
        nearest=min(xs,key=lambda x:abs(math.log(target/x)))
        ratio=target/nearest
        if not .5<=ratio<=2:return None
        first,rate,tail=points[nearest];startup=initializers.get(nearest,max(0.,first-rate));first_compute=first-startup;new_rate=rate*ratio
        prediction=result(startup+first_compute*ratio,new_rate,tail,'runtime-video-provisional',len(groups[nearest]),.75,
            dict(axis=plan['axis'],target=target,anchor=nearest,ratio=ratio,assumption='provisional compute-cost ratio; video scaling can be nonlinear'))
        if prediction:
            prediction['low']=min(prediction['low'],startup+rate*min(ratio,ratio**3)*steps+tail)
            prediction['high']=max(prediction['high'],startup+rate*max(ratio,ratio**3)*steps+tail)
        return prediction

    def has_runtime_recipe(self,class_type,recipe,cold,device,node_id):
        with self._lock:
            return self._db.execute("SELECT 1 FROM runtime_runs WHERE class_type=? AND recipe=? AND cold=? AND device=? AND node_id=? LIMIT 1",
                (class_type,recipe,int(bool(cold)),device,str(node_id))).fetchone() is not None

    def runtime_profile(self, class_type, static_sig, cold, device, node_id):
        with self._lock:
            rows=self._db.execute("SELECT recipe,input_sig FROM runtime_runs WHERE class_type=? AND static_sig=? AND cold=? AND device=? AND node_id=? ORDER BY id DESC LIMIT 3",
                (class_type,static_sig,int(bool(cold)),device,str(node_id))).fetchall()
        # A source/config profile is usable only after consistent observations.
        if len(rows)<3 or len(set(rows))!=1:return None
        prediction=self.predict_runtime(class_type,rows[0][0],rows[0][1],cold,device,node_id)
        if prediction:prediction.update(tier='runtime-profile',runtime_recipe=rows[0][0])
        return prediction

    def predict_node(self, class_type, sig, cold, device="", node_id=None):
        """Return {'seconds','low','high','n','tier'}; seconds is None when no data."""
        rows, tier = self._ladder("node_runs", "duration", class_type, sig, cold, device, node_id)
        if not rows:
            return {"seconds": None, "low": None, "high": None, "n": 0, "tier": "none"}
        values = [r[0] for r in rows]
        return {
            "seconds": _workflow_eta_weighted_quantile(values, 0.5, self.HALF_LIFE),
            "low": _workflow_eta_weighted_quantile(values, 0.25, self.HALF_LIFE),
            "high": _workflow_eta_weighted_quantile(values, 0.75, self.HALF_LIFE),
            "n": len(values),
            "tier": tier,
        }

    def predict_sampler_steps(self, class_type, step_sig, steps, cold, device, node_id):
        """Extrapolate only same-family, same-device, same-state sampler rows."""
        with self._lock:
            rows = self._db.execute(
                "SELECT t_first,step_time,tail FROM sampler_runs WHERE class_type=? AND step_sig=? "
                "AND cold=? AND device=? AND node_id=? ORDER BY id DESC LIMIT 50",
                (class_type, step_sig, int(bool(cold)), device, str(node_id))).fetchall()
        rows = [row for row in rows if _workflow_eta_valid(row[0], True) and _workflow_eta_valid(row[1])
                and _workflow_eta_valid(row[2], True)]
        if not rows:
            return None
        durations = [first + (steps - 1) * rate + tail for first,rate,tail in rows]
        return {"seconds": _workflow_eta_weighted_quantile(durations, .5, self.HALF_LIFE),
                "low": _workflow_eta_weighted_quantile(durations, .25, self.HALF_LIFE),
                "high": _workflow_eta_weighted_quantile(durations, .75, self.HALF_LIFE),
                "step_time": _workflow_eta_weighted_quantile([row[1] for row in rows], .5, self.HALF_LIFE),
                "n":len(rows), "tier":"step-scaled"}

    def predict_sampler_workload(self, class_type, shape, steps, cold, device, node_id):
        """Local empirical interpolation, or tightly bounded extrapolation."""
        for axis, signature_key, column in (("resolution","resolution_sig","width * height"),
                                            ("batch","batch_sig","batch"),
                                            ("frames","frames_sig","frames"),
                                            ("reference","video_sig","reference_frames")):
            family = shape.get(signature_key)
            if not family:
                continue
            target = shape["width"] * shape["height"] if axis=="resolution" else shape["reference_frames" if axis=="reference" else axis if axis=="frames" else "batch"]
            with self._lock:
                rows = self._db.execute(
                    f"SELECT {column},t_first,step_time,tail FROM sampler_runs WHERE class_type=? "
                    f"AND {signature_key}=? AND steps=? AND cold=? AND device=? AND node_id=? "
                    "ORDER BY id DESC LIMIT 300",
                    (class_type,family,steps,int(bool(cold)),device,str(node_id))).fetchall()
            groups = {}
            for x,first,rate,tail in rows:
                if (_workflow_eta_valid(x) and _workflow_eta_valid(first,True)
                        and _workflow_eta_valid(rate) and _workflow_eta_valid(tail,True)):
                    groups.setdefault(x,[]).append((first,rate,tail))
            points = {}
            for x,values in groups.items():
                if len(values)<(1 if axis=="reference" else 2):
                    continue
                rate_values=[v[1] for v in values]
                low=_workflow_eta_weighted_quantile(rate_values,.25,self.HALF_LIFE)
                high=_workflow_eta_weighted_quantile(rate_values,.75,self.HALF_LIFE)
                if high/low>1.5:
                    continue  # this configuration is too unstable to calibrate
                points[x]=values
            xs=sorted(points)
            if len(xs)<2 or (axis=="reference" and len(xs)<3 and any(len(points[x])<2 for x in xs)):
                continue
            if target in points:
                values=points[target]
                estimates=[first+(steps-1)*rate+tail for first,rate,tail in values]
                return {"seconds":_workflow_eta_weighted_quantile(estimates,.5,self.HALF_LIFE),
                        "low":_workflow_eta_weighted_quantile(estimates,.25,self.HALF_LIFE),
                        "high":_workflow_eta_weighted_quantile(estimates,.75,self.HALF_LIFE),
                        "step_time":_workflow_eta_weighted_quantile([v[1] for v in values],.5,self.HALF_LIFE),
                        "n":len(values),"tier":axis+"-learned","extrapolated":False,
                        "calibration":{"axis":axis,"family":family,"target":target,"measured_target":True,"samples":len(values)}}
            limit=1.25
            if axis=="reference" and len(xs)>=3:
                # Permit a useful 5s -> 10s projection only when several measured
                # lengths show a consistent local power slope. This cannot predict VRAM cliffs.
                recent=xs[-3:] if target>xs[-1] else xs[:3]
                rates=[_workflow_eta_weighted_quantile([v[1] for v in points[x]],.5,self.HALF_LIFE) for x in recent]
                slopes=[math.log(b/a)/math.log(y/x) for x,y,a,b in zip(recent,recent[1:],rates,rates[1:])]
                if all(0<=v<=4 for v in slopes) and max(slopes)-min(slopes)<=.75:limit=2.
            lower=[x for x in xs if x<target];upper=[x for x in xs if x>target]
            extrapolated=not (lower and upper)
            if lower and upper:
                lo,hi=max(lower),min(upper)
            elif len(lower)>=2 and target<=max(xs)*limit:
                lo,hi=xs[-2:]
            elif len(upper)>=2 and target>=min(xs)/limit:
                lo,hi=xs[:2]
            else:
                continue
            if hi/lo>(2.5 if axis=="resolution" else 4):
                continue  # do not bridge sparsely measured workloads
            weight=(math.log(target/lo)/math.log(hi/lo) if axis in ("frames","reference") else (target-lo)/(hi-lo))
            left,right=points[lo],points[hi]
            rate_left=_workflow_eta_weighted_quantile([v[1] for v in left],.5,self.HALF_LIFE)
            rate_right=_workflow_eta_weighted_quantile([v[1] for v in right],.5,self.HALF_LIFE)
            if rate_right<rate_left*.8 or (rate_right/rate_left) > ((hi/lo)**4 if axis in ("frames","reference") else (hi/lo)*1.75):
                continue  # observed discontinuity/slowdown: no smooth scaling claim
            components=[]
            for index in range(3):
                a=_workflow_eta_weighted_quantile([v[index] for v in left],.5,self.HALF_LIFE)
                b=_workflow_eta_weighted_quantile([v[index] for v in right],.5,self.HALF_LIFE)
                components.append(a*(b/a)**weight if axis in ("frames","reference") and a>0 and b>0
                                  else max(0.,(1-weight)*a+weight*b))
            first,rate,tail=components
            if rate<=0:
                continue
            seconds=first+(steps-1)*rate+tail
            # These are heuristic ranges, not statistical confidence intervals.
            spread=(.5 if extrapolated else .35) if axis=="reference" else (.3 if extrapolated else .2)
            return {"seconds":seconds,"low":seconds*(1-spread),"high":seconds*(1+spread),
                    "step_time":rate,"n":len(left)+len(right),"tier":axis+"-learned",
                    "extrapolated":extrapolated,"calibration":{"axis":axis,"family":family,"target":target,
                        "anchor_low":lo,"anchor_high":hi,"step_rate_low":rate_left,"step_rate_high":rate_right,
                        "interpolation_weight":weight,"power_slope":math.log(rate_right/rate_left)/math.log(hi/lo) if axis in ("frames","reference") else None,
                        "startup":first,"step_rate":rate,"tail":tail,"sample_count_low":len(left),"sample_count_high":len(right),
                        "extrapolation_limit":limit,"range_fraction":spread}}
        return None


    def sampler_stats(self, class_type, sig, cold, device="", node_id=None):
        """Return {'t_first','step_time','n','tier'}; values None when no data."""
        rows, tier = self._ladder("sampler_runs", "t_first, step_time", class_type, sig, cold, device, node_id)
        if not rows:
            return {"t_first": None, "step_time": None, "n": 0, "tier": "none"}
        return {
            "t_first": _workflow_eta_weighted_quantile([r[0] for r in rows], 0.5, self.HALF_LIFE),
            "step_time": _workflow_eta_weighted_quantile([r[1] for r in rows], 0.5, self.HALF_LIFE),
            "n": len(rows),
            "tier": tier,
        }

    def stats(self):
        with self._lock:
            return {
                t: self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("node_runs", "sampler_runs", "runs")
            }

    # --------------------------------------------------------------- pruning
    def prune(self):
        with self._lock, self._db:
            self._prune_locked()

    def _prune_locked(self):
        self._db.execute("DELETE FROM eta_errors WHERE id NOT IN (SELECT id FROM eta_errors ORDER BY id DESC LIMIT ?)",(self._total_cap,))
        self._db.execute("DELETE FROM runtime_runs WHERE id NOT IN (SELECT id FROM runtime_runs ORDER BY id DESC LIMIT ?)",(self._total_cap,))
        for table in ("node_runs", "sampler_runs"):
            self._db.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT id FROM ("
                f"SELECT id, ROW_NUMBER() OVER (PARTITION BY class_type, sig, node_id, device, cold "
                f"ORDER BY id DESC) AS rn FROM {table}) WHERE rn > ?)",
                (self._per_sig_cap,),
            )
        for table, cap in (("node_runs", self._total_cap), ("sampler_runs", self._total_cap),
                           ("runs", self._runs_cap)):
            self._db.execute(
                f"DELETE FROM {table} WHERE id <= "
                f"(SELECT id FROM {table} ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (cap,),
            )

    def close(self):
        with self._lock:
            self._db.close()
