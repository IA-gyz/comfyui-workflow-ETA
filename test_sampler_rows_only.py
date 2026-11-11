"""sampler_runs must only record step timing for real sampler classes."""
import sys, tempfile, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_eta_history import WorkflowETAHistory
from workflow_eta_tracker import WorkflowETATracker


def graph():
    return {'model': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'a.safetensors'}},
            'latent': {'class_type': 'EmptyLatentImage', 'inputs': {'width': 512, 'height': 512, 'batch_size': 1}},
            'sampler': {'class_type': 'KSampler', 'inputs': {'steps': 8, 'model': ['model', 0], 'latent_image': ['latent', 0],
                       'cfg': 7., 'denoise': 1., 'sampler_name': 'euler', 'scheduler': 'normal', 'seed': 10}},
            'upscale': {'class_type': 'LCRTXSuperResolution', 'inputs': {'video': ['sampler', 0]}},
            'decode': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sampler', 0], 'vae': ['model', 2]}}}


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


with tempfile.TemporaryDirectory() as d:
    h = WorkflowETAHistory(d + '/h.sqlite')
    c = Clock()
    t = WorkflowETATracker(types.SimpleNamespace(send_sync=lambda *a: None), h,
                          find_prompt_fn=lambda pid: (graph(), ['decode']), clock=c, verbose=False,
                          start_thread=False, device='gpu', resident_fn=lambda: frozenset({'a.safetensors'}))
    t._on_start({'prompt_id': 'p'})
    t._on_cached({'prompt_id': 'p', 'nodes': ['model', 'latent']})
    t._on_executing({'prompt_id': 'p', 'node': 'sampler'})
    for step in range(1, 9):
        c.now = 2.0 + (step - 1) * 0.5
        t._on_progress({'node': 'sampler', 'value': step, 'max': 8})
    c.now += 0.2
    t._on_executing({'prompt_id': 'p', 'node': 'upscale'})
    for step in range(1, 33):
        c.now += 0.01
        t._on_progress({'node': 'upscale', 'value': step, 'max': 32})
    c.now += 0.2
    t._on_executing({'prompt_id': 'p', 'node': None})
    t._on_success({'prompt_id': 'p'})
    rows = h._db.execute('SELECT class_type, COUNT(*) FROM sampler_runs GROUP BY class_type').fetchall()
    assert rows == [('KSampler', 1)], rows
    n_all = h._db.execute('SELECT COUNT(*) FROM sampler_runs').fetchone()[0]
    assert n_all == 1, n_all
    h.close()

print('PASS: sampler_runs records step timing only for sampler classes; fast progress nodes stay in node_runs')
