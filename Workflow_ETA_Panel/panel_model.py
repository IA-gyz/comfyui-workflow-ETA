"""Pure presentation logic for Workflow ETA status snapshots; no Qt dependency."""
from datetime import datetime
import math
import re
from urllib.parse import urlsplit, urlunsplit

MAX_REPLY = 8 * 1024 * 1024

def number(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None

def duration(value):
    value=number(value)
    if value is None:return '—'
    seconds=math.ceil(value);hours,rest=divmod(seconds,3600);minutes,seconds=divmod(rest,60)
    return f'{hours}h {minutes:02d}m' if hours else f'{minutes}m {seconds:02d}s' if minutes else f'{seconds}s'

def server_url(text):
    try:
        parts=urlsplit(text.strip())
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.query or parts.fragment or any(ord(c)<32 for c in text):raise ValueError
        _=parts.port
        return urlunsplit((parts.scheme,parts.netloc,parts.path.rstrip('/'),'',''))
    except (ValueError,AttributeError):
        raise ValueError('Use a ComfyUI URL such as http://127.0.0.1:8188, without a query, fragment or embedded credentials.') from None

def server_port(url):
    """Effective TCP port of a validated server URL; keys the per-server panel instance."""
    parts=urlsplit(url)
    return parts.port or (443 if parts.scheme=='https' else 80)

# Colour themes: (name, background, card, text, muted, accent, warning, danger). The Workflow ETA node chooses the theme
# and the extension reports it in the status payload; the panel only follows it and has no theme setting of its own.
# test_panel_contract.py fails if this table drifts from the node's (js/workflow_eta.js) or the server's.
DEFAULT_THEME='ocean'
THEMES={
 'ocean':('Ocean','#15232e','#243b4c','#f1f7fa','#8ca5b5','#67d5bf','#ffba70','#ff8895'),
 'midnight':('Midnight','#101426','#202744','#f3f5ff','#9caacb','#8ba8ff','#f5be7b','#ff8f9f'),
 'graphite':('Graphite','#202226','#383b40','#ffffff','#a3a9b3','#b4d3e5','#f7bd77','#ff929a'),
 'forest':('Forest','#132a21','#234338','#f3fff5','#98bba8','#88d99a','#eec477','#ff9294'),
 'ember':('Ember','#2b201b','#50352a','#fff7f0','#c0a391','#ffa867','#ffd17f','#ff9696'),
 'amethyst':('Amethyst','#251a31','#423052','#fcf5ff','#b8a0cb','#d2a0ff','#f2c37c','#ff95ba'),
 'paper':('Paper','#f4f7f9','#c9d8df','#122936','#536b7c','#087b69','#915500','#b42640')}

# The /workflow/eta/status contract this panel understands. Compared with the extension's STATUS_SCHEMA;
# test_panel_contract.py fails if the two or PANEL_FIELDS drift from what the extension really sends.
SUPPORTED_SCHEMA=1
PANEL_FIELDS=('state','prompt_id','log_session','elapsed','remaining','expected_total','expected_total_incomplete',
    'start_ts','finish_ts','lower_bound','confidence','node','node_class','nodes_done','nodes_total','nodes_cached','fraction','cold','eta_detail','theme')
_VERSION_TEXT=re.compile(r'[0-9A-Za-z.+-]{1,24}')

def valid_payload(data):
    if not isinstance(data,dict) or data.get('state') not in ('idle','running','done','aborted'):
        raise ValueError('This is not a Workflow ETA status response. Install Workflow ETA in this ComfyUI server.')
    return data

def local_date(ts,clock_only=False):
    if number(ts) is None:return '—'
    try:
        stamp=datetime.fromtimestamp(ts).astimezone()
        return stamp.strftime('%H:%M:%S') if clock_only and stamp.date()==datetime.now().astimezone().date() else stamp.strftime('%d %b %Y · %H:%M:%S')
    except (OverflowError,ValueError,OSError):return '—'

class Snapshot:
    def __init__(self):self.reset()
    def reset(self):
        self.data={};self.received_at=0.;self.changed_at=0.;self.fingerprint=None
        self.connected=False;self.error='Connecting to ComfyUI…';self.ever_received=False;self.attempted=False
        self.schema=None;self.version='';self.theme=None
    def accept(self,payload,now):
        valid_payload(payload)
        # Copy only the status fields: logs and watched graph data are not retained here.
        self.data={k:payload.get(k) for k in PANEL_FIELDS}
        schema=payload.get('schema');version=payload.get('version')
        self.schema=schema if isinstance(schema,int) and not isinstance(schema,bool) else None  # absent in older extensions
        self.version=version if isinstance(version,str) and _VERSION_TEXT.fullmatch(version) else ''
        theme=payload.get('theme');self.theme=theme if isinstance(theme,str) and theme in THEMES else None  # absent in older extensions
        fingerprint=tuple(self.data.get(k) for k in ('prompt_id','log_session','state','elapsed','node','nodes_done'))
        if fingerprint!=self.fingerprint:self.changed_at=now;self.fingerprint=fingerprint
        self.received_at=now;self.connected=True;self.error='';self.ever_received=True;self.attempted=True
    def fail(self,message):self.connected=False;self.error=str(message);self.attempted=True
    def view(self,now,poll_seconds=1.):
        p=self.data;state=p.get('state','idle');running=state=='running';done=state=='done'
        age=max(0.,now-self.received_at)
        stale=self.connected and running and now-self.changed_at>max(10.,poll_seconds*4)
        online=self.connected and not stale
        detail=p.get('eta_detail') if isinstance(p.get('eta_detail'),dict) else {}
        remaining=number(detail.get('provisional_remaining'))
        if remaining is None and not detail:remaining=number(p.get('remaining'))
        elapsed=number(p.get('elapsed'))
        # Interpolate only fresh running snapshots. Never invent a countdown offline.
        if online and running:
            elapsed=elapsed+age if elapsed is not None else None
            remaining=max(0.,remaining-age) if remaining is not None else None
        full_remaining=remaining
        unpriced=detail.get('unpriced_nodes')
        unpriced=len(unpriced) if isinstance(unpriced,list) else 0
        incomplete=bool(p.get('expected_total_incomplete') or detail.get('graph_incomplete') or unpriced)
        partial=bool(p.get('lower_bound'))
        total=number(p.get('expected_total'))
        finish=None
        if online and running:
            if elapsed is not None and remaining is not None:total=elapsed+remaining
            if total is not None and number(p.get('start_ts')) is not None:finish=p['start_ts']+total
        elif done:finish=number(p.get('finish_ts'))
        if done or state=='aborted':total=elapsed
        title='Final duration' if done else 'Provisional remaining' if partial else 'Estimated remaining'
        value=duration(elapsed if done else remaining) if (online and (running or done)) else 'Stopped' if online and state=='aborted' else 'Ready' if online else '—'
        if stale:status='Status not updating';message='ComfyUI replies, but the running timer is stale. Last values are frozen; check Workflow ETA.'
        elif not online:status='Disconnected' if self.attempted else 'Connecting';message=self.error
        elif done:status='Completed';message='Workflow finished. Final durations stay visible until the next run.'
        elif state=='aborted':status='Stopped / failed';message='Generation stopped. Elapsed time is not a completed runtime.'
        elif not running:status='Ready';message='Waiting for a workflow to start.'
        else:
            status='Running'
            message=str(detail.get('timing_message') or 'Using Workflow ETA history and live timing.')
            notes=detail.get('provisional_notes') or []
            if notes:message+=' · '+str(notes[0])
        newer=online and self.schema is not None and self.schema>SUPPORTED_SCHEMA
        extension=('Workflow ETA v'+self.version) if self.version else ''
        badge=(f'{unpriced} nodes unpriced' if unpriced else 'Graph incomplete' if detail.get('graph_incomplete') else 'Provisional estimate' if partial else 'Measured / learned estimate' if running else '')
        if newer:
            badge='Panel older than extension'
            message=(extension or 'The extension')+f' sends status format {self.schema}; this panel understands {SUPPORTED_SCHEMA}. Values may be missing; update this panel to the version bundled with the extension. · '+message
        node=str(p.get('node_class') or 'Waiting for node')+(f" · #{p['node']}" if p.get('node') is not None else '')
        count=lambda k:int(number(p.get(k)) or 0)
        fraction=number(p.get('fraction')) or 0
        return dict(online=online,stale=stale,state=state,status=status,message=message,title=title,value=value,
            badge=badge,elapsed=duration(elapsed),total=duration(total),total_title='Actual total' if done else 'Elapsed before stop' if state=='aborted' else 'Expected total',
            finish=local_date(finish),finish_short=local_date(finish,True),started=local_date(p.get('start_ts')),node=node,
            counts=f"{count('nodes_done')} / {count('nodes_total')} nodes · {count('nodes_cached')} cached",
            fraction=1000 if done else min(990,int(min(1.,fraction)*1000)),incomplete=incomplete,partial=partial,
            countdown=full_remaining,last_age=age,received_at=self.received_at,cold=bool(p.get('cold')),extension=extension,newer=newer,theme=self.theme)
