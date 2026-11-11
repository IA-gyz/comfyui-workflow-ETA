import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const WORKFLOW_CONFIDENCE_COLORS = ["#e5484d", "#f76b15", "#ffd43b", "#9ce09c", "#1a9e4b"];
const WORKFLOW_STRONG_TIERS = ["node-exact", "exact", "runtime-exact"];
const WORKFLOW_WEAK_TIERS = ["class", "other-state", "class-other-state", "node-other-state", "node-class",
    "machine-unmatched", "runtime-media-profile", "runtime-video-profile", "runtime-video-provisional",
    "runtime-few", "runtime-profile", "step-scaled", "resolution-learned", "batch-learned", "frames-learned",
    "reference-learned", "runtime-step-scaled", "runtime-video-learned"];
function workflowConfidenceLevel(p) {
    if (!p || p.state === "idle") return -1;
    if (p.confidence === "lower-bound" || p.lower_bound) return 0;
    if (p.confidence === "estimated") {
        if (p.runtime_scaling?.tier === "runtime-video-provisional" || p.workload_approximate) return 1;
        return 2;
    }
    const tiers = p.tiers || {};
    const strong = WORKFLOW_STRONG_TIERS.some(k => (tiers[k] || 0) > 0);
    const weak = WORKFLOW_WEAK_TIERS.some(k => (tiers[k] || 0) > 0);
    return strong && !weak ? 4 : 3;
}

const UI_SCALE_MIN = 0.5;
const UI_SCALE_MAX = 3;
const UI_SCALE_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
function normalizeWorkflowUIScale(value) {
    return typeof value === "number" && Number.isFinite(value) ?
        Math.max(UI_SCALE_MIN, Math.min(UI_SCALE_MAX, value)) : 1;
}

const WORKFLOW_ETA_THEMES = {
    ocean: { name: "Ocean", title: "#243b4c", bg: "#15232e", text: "#c8d6df", muted: "#8ca5b5", strong: "#f1f7fa", accent: "#67d5bf", warning: "#ffba70", danger: "#ff8895", track: "#304552" },
    midnight: { name: "Midnight", title: "#202744", bg: "#101426", text: "#d0d7ee", muted: "#9caacb", strong: "#f3f5ff", accent: "#8ba8ff", warning: "#f5be7b", danger: "#ff8f9f", track: "#2d3655" },
    graphite: { name: "Graphite", title: "#383b40", bg: "#202226", text: "#d9dce1", muted: "#a3a9b3", strong: "#ffffff", accent: "#b4d3e5", warning: "#f7bd77", danger: "#ff929a", track: "#464b54" },
    forest: { name: "Forest", title: "#234338", bg: "#132a21", text: "#cfe3d6", muted: "#98bba8", strong: "#f3fff5", accent: "#88d99a", warning: "#eec477", danger: "#ff9294", track: "#355344" },
    ember: { name: "Ember", title: "#50352a", bg: "#2b201b", text: "#ead5c6", muted: "#c0a391", strong: "#fff7f0", accent: "#ffa867", warning: "#ffd17f", danger: "#ff9696", track: "#5b4234" },
    amethyst: { name: "Amethyst", title: "#423052", bg: "#251a31", text: "#e0d2ef", muted: "#b8a0cb", strong: "#fcf5ff", accent: "#d2a0ff", warning: "#f2c37c", danger: "#ff95ba", track: "#514064" },
    paper: { name: "Paper", title: "#c9d8df", bg: "#f4f7f9", text: "#263b48", muted: "#536b7c", strong: "#122936", accent: "#087b69", warning: "#915500", danger: "#b42640", track: "#d0dce3" }
};
// Dedicated high-contrast log palettes; semantic colors are independent of UI accents.
const WORKFLOW_LOG_PALETTES = {
    ocean: {info:"#b8cedd", run:"#8fcaff", node:"#8de0d0", success:"#a7e995", timing:"#dbc0ff", warning:"#ffd08a", error:"#ff9da9"},
    midnight: {info:"#bac7e9", run:"#a8baff", node:"#8edce7", success:"#a4e9bc", timing:"#dcb1ff", warning:"#ffcf91", error:"#ff9fb8"},
    graphite: {info:"#c3c8d1", run:"#a9ceff", node:"#91dce0", success:"#b2e29b", timing:"#d9baff", warning:"#ffd08c", error:"#ffa0a6"},
    forest: {info:"#b5d2c2", run:"#9ecfff", node:"#8ee0c2", success:"#c3e897", timing:"#d4bfff", warning:"#f5d18b", error:"#ffa7a0"},
    ember: {info:"#dcc6b8", run:"#a8ccff", node:"#8edbce", success:"#bddf9c", timing:"#e5b7eb", warning:"#ffce91", error:"#ffa4ad"},
    amethyst: {info:"#d2bee3", run:"#acccff", node:"#99e0d3", success:"#b9e5ad", timing:"#e6bcff", warning:"#f8d291", error:"#ffa3c1"},
    paper: {info:"#465b6a", run:"#2558a4", node:"#006e74", success:"#326523", timing:"#76449c", warning:"#875400", error:"#ac2540"}
};
function workflowLogKind(message) {
    // Check failures before completion: e.g. "history save failed; run completed".
    if (/\b(error|failed|exception|traceback)\b/i.test(message)) return "error";
    if (/\b(interrupt(?:ed)?|discarded|unresolved|unavailable|insufficient|unpriced|unknown|rough|lower.bound)\b/i.test(message)) return "warning";
    if (/^run done:/i.test(message)) return "success";
    if (/^\s*ETA check\b/i.test(message)) return "timing";
    if (/^run\b/i.test(message)) return "run";
    if (/^node\b/i.test(message)) return "node";
    return "info";
}
const WORKFLOW_LOG_LABELS = {info:"INFO",run:"RUN",node:"NODE",success:"DONE",timing:"CHECK",warning:"WARN",error:"ERROR"};
function workflowThemeKey(value) { return Object.hasOwn(WORKFLOW_ETA_THEMES, value) ? value : "ocean"; }
function workflowTheme(node) { return WORKFLOW_ETA_THEMES[workflowThemeKey(node.properties?.theme)]; }

const WORKFLOW_BAR_ANIMATIONS = {
    static: "Static", stripes: "Flowing stripes", shimmer: "Shimmer", pulse: "Pulse",
    scanner: "Scanner", comet: "Comet", wave: "Wave", rainbow: "Rainbow"
};
function workflowBarAnimation(value) { return Object.hasOwn(WORKFLOW_BAR_ANIMATIONS, value) ? value : "stripes"; }
let workflowAnimationFrame = 0;
let workflowLastFrame = 0;
const workflowReducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
function workflowCanAnimate() {
    return connected && latest.state === "running" && !globalThis.document?.hidden && !workflowReducedMotion?.matches &&
        [...displays].some(node => !node.flags?.collapsed && workflowBarAnimation(node.properties?.bar_animation) !== "static");
}
function scheduleWorkflowBarAnimation() {
    if (!workflowAnimationFrame && workflowCanAnimate() && typeof requestAnimationFrame === "function") {
        workflowAnimationFrame = requestAnimationFrame(tickWorkflowBarAnimation);
    }
}
function tickWorkflowBarAnimation(timestamp) {
    workflowAnimationFrame = 0;
    if (!workflowCanAnimate()) return;
    if (timestamp - workflowLastFrame >= 1000 / 30) {
        workflowLastFrame = timestamp;
        for (const node of displays) {
            if (!node.flags?.collapsed && workflowBarAnimation(node.properties?.bar_animation) !== "static") node.setDirtyCanvas(true, false);
        }
    }
    scheduleWorkflowBarAnimation();
}
function drawWorkflowProgressBar(ctx, node, theme, color, width, fraction, running) {
    const x = 18, y = 279, height = 7, total = width - 36, filled = total * fraction;
    ctx.fillStyle = theme.track; ctx.fillRect(x, y, total, height);
    ctx.fillStyle = color; ctx.fillRect(x, y, filled, height);
    const style = workflowBarAnimation(node.properties?.bar_animation);
    if (!running || !connected || style === "static" || filled <= 0 || workflowReducedMotion?.matches) return;
    const t = (globalThis.performance?.now?.() ?? Date.now()) / 1000;
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, filled, height); ctx.clip();
    ctx.fillStyle = theme.strong;
    if (style === "stripes") {
        ctx.globalAlpha = .28;
        for (let offset = -20 + (t * 24) % 20; offset < filled + 20; offset += 20) {
            ctx.beginPath(); ctx.moveTo(x + offset, y); ctx.lineTo(x + offset + 7, y);
            ctx.lineTo(x + offset, y + height); ctx.lineTo(x + offset - 7, y + height); ctx.closePath(); ctx.fill();
        }
    } else if (style === "pulse") {
        ctx.globalAlpha = .08 + .28 * (1 + Math.sin(t * 3)) / 2;
        ctx.fillRect(x, y, filled, height);
    } else if (style === "wave") {
        ctx.globalAlpha = .35;
        for (let offset = 0; offset < filled; offset += 3) {
            const amplitude = 2 + 4 * (1 + Math.sin(offset / 14 - t * 4)) / 2;
            ctx.fillRect(x + offset, y + height - amplitude, 2, amplitude);
        }
    } else if (style === "rainbow") {
        const gradient = ctx.createLinearGradient(x, y, x + total, y);
        for (let i = 0; i <= 6; i++) gradient.addColorStop(i / 6, "hsl(" + ((i * 60 + t * 50) % 360) + " 85% 65%)");
        ctx.globalAlpha = .75; ctx.fillStyle = gradient; ctx.fillRect(x, y, filled, height);
    } else {
        const length = style === "comet" ? 48 : style === "shimmer" ? 65 : 24;
        const position = style === "scanner" ? (filled - length) * (1 + Math.sin(t * 2)) / 2 :
            ((t * 65) % (filled + length * 2)) - length;
        const gradient = ctx.createLinearGradient(x + position, y, x + position + length, y);
        gradient.addColorStop(0, "transparent"); gradient.addColorStop(style === "comet" ? .85 : .5, theme.strong); gradient.addColorStop(1, "transparent");
        ctx.globalAlpha = .7; ctx.fillStyle = gradient; ctx.fillRect(x + position, y, length, height);
    }
    ctx.restore();
}

const WORKFLOW_LOG_PANEL_HEIGHT = 480;
const WORKFLOW_WATCH_HEIGHT = 260;
function workflowWatchHeight(node) { return node.properties?.watch_collapsed ? 40 : WORKFLOW_WATCH_HEIGHT; }
function workflowLogHeight(node) { return WORKFLOW_LOG_PANEL_HEIGHT - WORKFLOW_WATCH_HEIGHT + workflowWatchHeight(node); }
const WORKFLOW_BASE_HEIGHT = 492;
let workflowLogSession = null;
let workflowLogEntries = [];
let workflowLogRevision = 0;
function mergeWorkflowLogs(payload) {
    if (typeof payload?.log_session !== "string" || !Array.isArray(payload.logs)) return;
    const changedSession = workflowLogSession !== payload.log_session;
    if (changedSession) { workflowLogSession = payload.log_session; workflowLogEntries = []; }
    const entries = new Map(workflowLogEntries.map(entry => [entry.id, entry]));
    let changed = changedSession;
    for (const entry of payload.logs) {
        if (!Number.isSafeInteger(entry?.id) || entry.id < 1 || typeof entry.message !== "string" || !Number.isFinite(entry.ts)) continue;
        if (!entries.has(entry.id)) changed = true;
        entries.set(entry.id, {...entry, message: entry.message.slice(0, 700)});
    }
    if (changed) {
        workflowLogEntries = [...entries.values()].sort((a,b) => a.id-b.id).slice(-250);
        workflowLogRevision++;
        for (const node of displays) node.setDirtyCanvas(true, false);
    }
}
function workflowLogRows(node, width) {
    const columns = Math.max(12, Math.floor((width-52)/6.6));
    if (node._workflowLogCache?.revision === workflowLogRevision && node._workflowLogCache.columns === columns) return node._workflowLogCache.rows;
    const rows = [];
    for (const entry of workflowLogEntries) {
        const stamp = new Date(entry.ts*1000).toLocaleTimeString(undefined, {hour12:false});
        const kind = workflowLogKind(entry.message);
        const message = stamp + " [" + WORKFLOW_LOG_LABELS[kind] + "] " + entry.message;
        for (let i=0; i<message.length; i+=columns) rows.push({text:message.slice(i,i+columns),kind});
    }
    node._workflowLogCache = {revision:workflowLogRevision,columns,rows};
    return rows;
}
function drawWorkflowLogs(ctx,node,theme,width,height,text) {
    const visible = Boolean(node.properties?.logs_visible);
    const toggleY = height-36;
    text(visible ? "Verbose log" : "Show verbose log",18,height-16,"11px sans-serif",theme.muted);
    ctx.fillStyle = visible ? theme.accent : theme.track;
    ctx.fillRect(width-46,toggleY,28,28);
    text(visible ? "▾" : "≡",width-40,toggleY+20,"19px sans-serif",visible ? theme.bg : theme.strong);
    if (!visible) return;
    const fullHeight=height;
    height-=workflowWatchHeight(node);
    ctx.fillStyle=theme.track;ctx.fillRect(12,460,width-24,height-504);
    ctx.fillStyle=theme.bg;ctx.fillRect(13,461,width-26,height-506);
    text("Verbose log · "+workflowLogEntries.length,22,482,"bold 12px sans-serif",theme.text);
    for(const [offset,label] of [[106,"↑"],[76,"↓"],[46,"⇥"]]) {
        ctx.fillStyle=theme.track;ctx.fillRect(width-offset,466,26,22);
        text(label,width-offset+7,482,"14px sans-serif",theme.strong);
    }
    const rows=workflowLogRows(node,width);
    const count=Math.max(1,Math.floor((height-44-500)/14));
    const maximum=Math.max(0,rows.length-count);
    node._workflowLogScroll=Math.max(0,Math.min(maximum,node._workflowLogScroll||0));
    node._workflowLogMax=maximum;node._workflowLogPage=count;
    const end=rows.length-node._workflowLogScroll;
    const selected=rows.slice(Math.max(0,end-count),end);
    ctx.save();ctx.beginPath();ctx.rect(20,490,width-40,height-534);ctx.clip();
    if (!rows.length) text("Waiting for tracker log data…",22,506,"11px monospace",theme.muted);
    const palette = WORKFLOW_LOG_PALETTES[workflowThemeKey(node.properties?.theme)];
    selected.forEach((line,index)=>text(line.text,22,506+index*14,"11px monospace",palette[line.kind]));
    ctx.restore();
    drawWorkflowWatch(ctx,node,theme,width,fullHeight,text);
}

function workflowWatchRows(node) {
    const rows=latest.watched?.rows || [];
    return node.properties?.watch_changes_only ? rows.filter(row=>row.change!=="same" && row.change!=="baseline") : rows;
}
function workflowWatchValue(value) { return value===null || value===undefined ? "—" : String(value); }
function workflowWatchFit(ctx,value,width) {
    const original=workflowWatchValue(value),candidate=original.slice(0,2048);
    if(ctx.measureText(candidate).width<=width)return candidate+(candidate.length<original.length?"…":"");
    const available=Math.max(0,width-ctx.measureText("…").width);
    let low=0,high=candidate.length;
    while(low<high){const mid=Math.ceil((low+high)/2);if(ctx.measureText(candidate.slice(0,mid)).width<=available)low=mid;else high=mid-1;}
    return candidate.slice(0,low)+"…";
}
function drawWorkflowWatch(ctx,node,theme,width,height,text) {
    const top=height-40-workflowWatchHeight(node), rows=workflowWatchRows(node), page=8;
    const maximum=Math.max(0,Math.ceil(rows.length/page)-1);
    node._workflowWatchPage=Math.min(maximum,Math.max(0,node._workflowWatchPage||0));
    text((node.properties?.watch_collapsed?"▸ ":"▾ ")+"Watched data · "+rows.length,20,top+18,"bold 12px sans-serif",theme.strong);
    text("Compare ↗",width-96,top+18,"bold 12px sans-serif",theme.accent);
    if(node.properties?.watch_collapsed)return;
    text(node.properties?.watch_changes_only?"Changed only":"All values",20,top+40,"11px sans-serif",theme.accent);
    text("‹    "+(node._workflowWatchPage+1)+"/"+(maximum+1)+"    ›",width-110,top+40,"11px sans-serif",theme.text);
    text("NODE / PARAMETER",20,top+61,"10px sans-serif",theme.muted);
    text("CURRENT VALUE",width*.57,top+61,"10px sans-serif",theme.muted);
    ctx.save();ctx.beginPath();ctx.rect(18,top+65,width-36,151);ctx.clip();
    const selected=rows.slice(node._workflowWatchPage*page,(node._workflowWatchPage+1)*page);
    ctx.font="11px monospace";
    selected.forEach((row,i)=>{
        const y=top+80+i*18, changed=row.change!=="same" && row.change!=="baseline";
        text(workflowWatchFit(ctx,(changed?"• ":"")+row.node+" · "+row.parameter,width*.57-38),20,y,"11px monospace",changed?theme.warning:theme.text);
        text(workflowWatchFit(ctx,row.value,width*.43-24),width*.57,y,"11px monospace",changed?theme.warning:theme.muted);
    });
    if(!rows.length) text(latest.watched?"No matching changes":"Run a workflow to inspect tracked data",20,top+82,"11px sans-serif",theme.muted);
    ctx.restore();
    text(latest.watched?.baseline_prompt?"Compared with last completed run · session":"No comparison baseline yet",20,top+235,"10px sans-serif",theme.muted);
}
let workflowWatchDialog=null;
function openWorkflowWatch(node) {
    if(workflowWatchDialog?.isConnected) { workflowWatchDialog.focus(); return; }
    const theme=workflowTheme(node), palette=WORKFLOW_LOG_PALETTES[workflowThemeKey(node.properties?.theme)];
    const make=(tag,label)=>{const e=document.createElement(tag);if(label!==undefined)e.textContent=label;return e;};
    const dialog=make("dialog");workflowWatchDialog=dialog;
    dialog.setAttribute("aria-label","Workflow ETA watched data and comparison");
    dialog.style.cssText=`width:min(1400px,94vw);height:85vh;box-sizing:border-box;padding:22px;border:1px solid ${theme.track};border-radius:14px;background:${theme.bg};color:${theme.text};font:14px system-ui;overflow:auto`;
    const heading=make("h2","Watched data & run comparison");heading.style.margin="0 0 10px";
    const close=make("button","Close");close.style.float="right";close.onclick=()=>dialog.close();
    const note=make("p","Initial forecast is frozen at run startup; current node forecast can improve as inputs become known. Live remaining time is shown on the main timer. Estimated ranges are provisional, not guaranteed limits. Hover a timing row for its calibration details. Comparisons use the last completed run in this server session; timing changes do not prove causation.");
    note.style.cssText=`color:${theme.muted};max-width:1000px;line-height:1.5`;
    const controls=make("div");controls.style.cssText="display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:center";
    const search=make("input");search.type="search";search.placeholder="Search node, parameter, value…";search.setAttribute("aria-label","Search watched data");search.style.minWidth="280px";
    const view=make("select");view.setAttribute("aria-label","Table view");
    for(const [v,label] of [["values","Parameter values"],["timing","Node timing & impact"]]){const o=make("option",label);o.value=v;view.append(o);}
    const filter=make("select");filter.setAttribute("aria-label","Change filter");
    for(const [v,label] of [["all","All values"],["changed","Changed / added / removed"],["unresolved","Unresolved nodes"]]){const o=make("option",label);o.value=v;filter.append(o);}
    const group=make("select");group.setAttribute("aria-label","Data group");
    for(const v of ["All groups","Literal","Workload","Dependency","Source","Derived","Context","Family","Runtime"]){const o=make("option",v);o.value=v;group.append(o);}
    const pager=make("span"), back=make("button","Previous"), forward=make("button","Next");
    let tablePage=0;
    search.value="";view.value="values";filter.value="all";group.value="All groups";
    const summary=make("div");summary.style.cssText=`padding:12px;background:${theme.track};border-radius:6px;margin-bottom:12px`;
    const scroller=make("div");scroller.style.cssText="overflow:auto;max-height:55vh";
    const table=make("table");table.style.cssText="width:100%;border-collapse:collapse;text-align:left;font-size:13px";
    const head=make("thead"),body=make("tbody");table.append(head,body);scroller.append(table);
    const seconds=value=>Number.isFinite(value)?value.toFixed(2)+" s":"—";
    const delta=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)?(a-b>=0?"+":"")+(a-b).toFixed(2)+" s":"—";
    const basis=tier=>({"runtime-media-profile":"Similar video workload · provisional","runtime-video-profile":"Video history projection · provisional","runtime-exact":"Matching runtime inputs","runtime-few":"Matching runtime inputs · few samples","machine-unmatched":"History · memory state unmatched","step-scaled":"Measured step-time scaling","runtime-step-scaled":"Runtime step-time scaling","runtime-video-learned":"Measured video-length curve","runtime-video-provisional":"Video compute ratio · provisional","none":"No matched timing"}[tier]||String(tier||"No matched timing").replace(/-/g," "));
    const render=()=>{
        const data=latest.watched||{}, nodes=data.nodes||{}, old=data.previous_nodes||{}, all=data.rows||[];
        const changedRows=all.filter(r=>r.change!=="same" && r.change!=="baseline");
        const changed=changedRows.length, changedNodes=new Set(changedRows.map(r=>r.node));
        summary.textContent=`${all.length} tracked fields · ${data.baseline_prompt?changed+" changed / added / removed":"no completed-run baseline"} · ${Object.keys(nodes).length} nodes · Current run: ${data.prompt_id||"—"}`;
        const timing=view.value==="timing", query=search.value.toLowerCase();
        head.replaceChildren();body.replaceChildren();
        const header=make("tr");
        const columns=timing?["Node","Estimate source","Samples","Initial forecast","Estimated range","Previous forecast","Current forecast","Forecast Δ","Previous actual","Current actual","Initial error (actual − forecast)","Actual Δ","Status"]:["Node","Group","Parameter","Previous","Current","Change","Estimate method"];
        for(const label of columns){const th=make("th",label);th.style.cssText=`position:sticky;top:0;background:${theme.title};padding:10px;white-space:nowrap`;header.append(th);}head.append(header);
        const records=timing?Object.entries(nodes).map(([id,n])=>({node:id,...n})):all;
        const matched=[];
        for(const r of records){
            const n=nodes[r.node]||{}, previous=old[r.node]||{};
            const prev=previous.class===n.class?previous:{};
            if(group.value!=="All groups" && !timing && r.group!==group.value)continue;
            if(filter.value==="unresolved" && !n.unresolved)continue;
            if(filter.value==="changed" && (timing?!changedNodes.has(r.node):["same","baseline"].includes(r.change)))continue;
            const range=Number.isFinite(r.low)&&Number.isFinite(r.high)?seconds(r.low)+" – "+seconds(r.high):"—";
            const values=timing?[r.node+" · "+(r.class||""),basis(r.tier),Number.isFinite(r.samples)?String(r.samples):"—",seconds(r.initial_predicted),range,seconds(prev.predicted),seconds(r.predicted),delta(r.predicted,prev.predicted),seconds(prev.actual),seconds(r.actual),delta(r.actual,r.initial_predicted),delta(r.actual,prev.actual),r.cached?"Cached":!Number.isFinite(r.predicted)?"No matched timing":r.unresolved?"Inputs unresolved":"Tracked"]:[r.node+" · "+r.class,r.group,r.parameter,workflowWatchValue(r.previous),workflowWatchValue(r.value),r.change,n.tier||"—"];
            if(query && !values.join(" ").toLowerCase().includes(query))continue;
            matched.push({r,values});
        }
        const pages=Math.max(1,Math.ceil(matched.length/100));tablePage=Math.min(tablePage,pages-1);
        pager.textContent=`Page ${tablePage+1}/${pages} · ${matched.length} matching rows`;
        back.disabled=tablePage===0;forward.disabled=tablePage>=pages-1;
        for(const {r,values} of matched.slice(tablePage*100,(tablePage+1)*100)) {
            const tr=make("tr");tr.style.color=!timing && ["changed","added","removed"].includes(r.change)?palette.warning:theme.text;
            for(const value of values){const td=make("td",String(value));td.style.cssText=`padding:9px 10px;border-bottom:1px solid ${theme.track};vertical-align:top;overflow-wrap:anywhere;max-width:400px;white-space:pre-wrap`;tr.append(td);}
            if(!timing && r.input)tr.title="Original input: "+r.input;
            if(timing)tr.title="Current method: "+(r.tier||"none")+"\nInitial method: "+(r.initial_tier||"unavailable")+"\n"+JSON.stringify(r.calibration||{},null,2);
            body.append(tr);
        }
        if(!body.children.length){const tr=make("tr"),td=make("td","No matching data. Run a workflow or adjust the filters.");td.colSpan=columns.length;tr.append(td);body.append(tr);}
    };
    for(const e of [search,view,filter,group,close,back,forward]){e.style.cssText+=`;padding:8px 12px;border:1px solid ${theme.muted};border-radius:6px;background:${theme.title};color:${theme.strong}`;}
    controls.append(search,view,filter,group,back,forward,pager);dialog.append(close,heading,note,controls,summary,scroller);
    for(const e of [search,view,filter,group])e.addEventListener("input",()=>{tablePage=0;render();});
    back.onclick=()=>{tablePage=Math.max(0,tablePage-1);render();};
    forward.onclick=()=>{tablePage++;render();};
    dialog._workflowRender=render;
    dialog._workflowOwner=node;
    dialog.addEventListener("close",()=>{dialog.remove();if(workflowWatchDialog===dialog)workflowWatchDialog=null;});
    document.body.append(dialog);render();dialog.showModal();
}

function openWorkflowETABrowserPanel() {
    // Independent browser window titled "Workflow ETA"; repeated clicks focus the same window.
    const w = typeof window !== "undefined" ? window : globalThis;
    if (typeof w.open === "function") w.open("/workflow/eta/panel", "workflow-eta-panel", "width=560,height=760");
}

async function openWorkflowETAPanel() {
    // Same behavior as Comfy Kicker's "Open Timer Widget": the server launches the
    // installed desktop panel; its single-instance guard activates an existing window.
    try {
        const response = await api.fetchApi("/workflow/eta/open_panel", { method: "POST" });
        if (!response.ok) {
            let message = "HTTP " + response.status;
            try { message = (await response.json()).error || message; } catch {}
            console.warn("[workflow-eta] could not open the desktop panel:", message);
        }
    } catch (error) {
        console.warn("[workflow-eta] panel request failed", error);
    }
}

function openWorkflowETASettings(node) {
    if (node._workflowSettingsDialog?.isConnected) { node._workflowSettingsDialog.focus(); return; }
    const make = (tag, text) => {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        return element;
    };
    const dialog = make("dialog");
    dialog.setAttribute("aria-label", "Workflow ETA settings");
    dialog.style.cssText = "width:min(340px,calc(100vw - 48px));box-sizing:border-box;padding:22px;border:1px solid;border-radius:12px;font:14px system-ui;box-shadow:0 16px 60px #0008;";
    const header = make("div"); header.style.cssText = "display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;gap:12px";
    const title = make("strong", "Workflow ETA settings"); title.style.fontSize = "17px";
    const close = make("button", "✕"); close.type = "button"; close.setAttribute("aria-label", "Close settings");
    header.append(title, close); dialog.append(header);
    const scaleLabel = make("label"); scaleLabel.style.cssText = "display:block;margin-bottom:8px";
    const scaleText = make("span", "Scale UI");
    const percentage = make("output"); percentage.style.cssText = "float:right;font-variant-numeric:tabular-nums";
    const scale = make("input"); scale.type = "range"; scale.min = "0.5"; scale.max = "3"; scale.step = "0.05";
    scale.value = String(normalizeWorkflowUIScale(node.properties.ui_scale)); scale.style.cssText = "width:100%;margin:8px 0 22px";
    scale.setAttribute("aria-label", "Scale UI");
    percentage.textContent = Math.round(Number(scale.value) * 100) + "%";
    scaleLabel.append(scaleText, percentage, scale); dialog.append(scaleLabel);
    const themeLabel = make("label", "Theme"); themeLabel.style.cssText = "display:block";
    const themes = make("select"); themes.style.cssText = "display:block;width:100%;margin-top:9px;padding:9px;border:1px solid;border-radius:6px;font:inherit";
    themes.setAttribute("aria-label", "Theme");
    for (const [key, theme] of Object.entries(WORKFLOW_ETA_THEMES)) {
        const option = make("option", theme.name); option.value = key; themes.append(option);
    }
    themes.value = workflowThemeKey(node.properties.theme);
    themeLabel.append(themes); dialog.append(themeLabel);
    const animationLabel = make("label", "Progress bar animation"); animationLabel.style.cssText = "display:block;margin-top:18px";
    const animations = make("select"); animations.style.cssText = themes.style.cssText;
    animations.setAttribute("aria-label", "Progress bar animation");
    for (const [key, label] of Object.entries(WORKFLOW_BAR_ANIMATIONS)) {
        const option = make("option", label); option.value = key; animations.append(option);
    }
    animations.value = workflowBarAnimation(node.properties.bar_animation);
    animationLabel.append(animations); dialog.append(animationLabel);
    const diagnosticLabel=make("label","Recent runs to export (1–100)");diagnosticLabel.style.cssText="display:block;margin-top:18px";
    const diagnosticCount=make("input");diagnosticCount.type="number";diagnosticCount.min="1";diagnosticCount.max="100";diagnosticCount.value="20";diagnosticCount.style.cssText=themes.style.cssText;
    diagnosticLabel.append(diagnosticCount);dialog.append(diagnosticLabel);
    const exportLog=make("button","Export diagnostic log (ZIP)");exportLog.type="button";exportLog.style.cssText="display:block;width:100%;margin-top:12px;padding:12px;font:inherit;cursor:pointer";
    const exportStatus=make("p","Includes parameters, prompts, file paths, ETA samples and actual timings. Media files are excluded.");exportStatus.style.fontSize="12px";exportStatus.setAttribute("role","status");
    dialog.append(exportLog,exportStatus);
    let exportController=null;
    exportLog.addEventListener("click",async()=>{
        if(exportLog.disabled)return;
        const count=Number(diagnosticCount.value);
        if(!Number.isInteger(count)||count<1||count>100){exportStatus.textContent="Choose 1 to 100 runs.";return;}
        exportLog.disabled=true;exportStatus.textContent="Preparing diagnostic bundle…";
        exportController=typeof AbortController==="function"?new AbortController():null;
        try {
            const response=await api.fetchApi("/workflow/eta/diagnostics/export?limit="+count,exportController?{signal:exportController.signal}:{});
            if(!response.ok){let message="Diagnostic export failed (HTTP "+response.status+")";try{message=(await response.json()).error||message;}catch{}throw Error(message);}
            const blob=await response.blob();
            if(!dialog.isConnected || exportController?.signal.aborted)return;
            const url=URL.createObjectURL(blob),link=make("a");link.href=url;link.download="Workflow_ETA_diagnostics.zip";
            try{document.body.append(link);link.click();}
            finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
            exportStatus.textContent="Downloaded: Workflow_ETA_diagnostics.zip. Start with RUN_SUMMARY.md and REVIEW_GUIDE.md.";
        } catch(error){exportStatus.textContent=error.message||"Diagnostic export failed";}
        finally{exportLog.disabled=false;exportController=null;}
    });
    const paint = () => {
        const theme = workflowTheme(node);
        for(const e of [diagnosticCount,exportLog]){e.style.backgroundColor=theme.title;e.style.color=theme.strong;e.style.borderColor=theme.track;}
        exportStatus.style.color=theme.muted;
        dialog.style.backgroundColor = theme.bg; dialog.style.color = theme.text; dialog.style.borderColor = theme.track;
        for (const control of [close, themes, animations]) {
            control.style.backgroundColor = theme.title; control.style.color = theme.strong; control.style.borderColor = theme.track;
        }
        close.style.cssText += ";padding:5px 9px;border-radius:6px;border:1px solid;cursor:pointer;font:inherit";
        scale.style.accentColor = theme.accent;
    };
    scale.addEventListener("input", () => { node.setWorkflowUIScale(Number(scale.value)); percentage.textContent = Math.round(Number(scale.value) * 100) + "%"; });
    themes.addEventListener("change", () => { node.setWorkflowETATheme(themes.value); paint(); });
    animations.addEventListener("change", () => node.setWorkflowBarAnimation(animations.value));
    close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { exportController?.abort();dialog.remove(); if (node._workflowSettingsDialog === dialog) node._workflowSettingsDialog = null; });
    dialog.addEventListener("click", event => {
        if (event.target !== dialog) return;
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    for (const event of ["pointerdown", "mousedown", "wheel"]) dialog.addEventListener(event, event => event.stopPropagation());
    node._workflowSettingsDialog = dialog;
    paint(); document.body.append(dialog); dialog.showModal();
}

const displays = new Set();
let latest = { state: "idle" };
let connected = true;
let revision = 0;
let snapshotRequest = 0;

function updateWorkflowETA(payload) {
    if (!payload || typeof payload !== "object") return;
    mergeWorkflowLogs(payload);
    latest = payload;
    workflowWatchDialog?._workflowRender?.();
    revision++;
    for (const node of displays) node.setDirtyCanvas(true, false);
    scheduleWorkflowBarAnimation();
}

function formatWorkflowDuration(value) {
    if (!Number.isFinite(value)) return "—";
    const n = Math.max(0, Math.ceil(value));
    if (n < 60) return n + " s";
    if (n < 3600) return Math.floor(n / 60) + "m " + (n % 60) + "s";
    return Math.floor(n / 3600) + "h " + Math.floor((n % 3600) / 60) + "m";
}

async function restoreWorkflowETA() {
    const version = revision;
    const request = ++snapshotRequest;
    try {
        const response = await api.fetchApi("/workflow/eta/status");
        if (!response.ok) return;
        const payload = await response.json();
        if (request === snapshotRequest && connected && (version === revision || payload.log_session === workflowLogSession)) mergeWorkflowLogs(payload);
        // Never overwrite a fresher WebSocket event or a later request.
        if (version === revision && request === snapshotRequest && connected) updateWorkflowETA(payload);
    } catch (error) {
        console.debug("[workflow-eta] status unavailable", error);
    }
}

app.registerExtension({
    name: "Workflow.ETA",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WorkflowETA") return;
        class WorkflowETADisplayMethods {
            onNodeCreated() {
                this.isVirtualNode = true;
                this.serialize_widgets = false;
                this._workflowRestoring = true;
                this.properties ??= {};
                this.properties.ui_scale = normalizeWorkflowUIScale(this.properties.ui_scale);
                this._workflowUIScale = this.properties.ui_scale;
                this.properties.logs_visible = this.properties.logs_visible === true;
                this._workflowLogsVisible = this.properties.logs_visible;
                this._workflowLogScroll = 0;
                this.size = [540 * this._workflowUIScale,
                    (WORKFLOW_BASE_HEIGHT + (this._workflowLogsVisible ? workflowLogHeight(this) : 0)) * this._workflowUIScale];
                this.properties.bar_animation = workflowBarAnimation(this.properties.bar_animation);
                this.properties.theme = workflowThemeKey(this.properties.theme);
                this.color = workflowTheme(this).title;
                this.bgcolor = workflowTheme(this).bg;
            }
            onAdded() {
                displays.add(this); this.setDirtyCanvas(true, false); scheduleWorkflowBarAnimation();
                if (typeof queueMicrotask === "function") queueMicrotask(() => { this._workflowRestoring = false; });
            }
            onRemoved() {
                if(workflowWatchDialog?._workflowOwner===this)workflowWatchDialog.close();
                displays.delete(this);
                this._workflowSettingsDialog?.close();
                this._workflowLogCache=null;
            }
            setWorkflowLogVisible(value) {
                const visible = value === true;
                const previous = Boolean(this._workflowLogsVisible);
                const scale = normalizeWorkflowUIScale(this.properties?.ui_scale);
                this.properties ??= {};
                this.properties.logs_visible = visible;
                this._workflowLogsVisible = visible;
                const minimum = (WORKFLOW_BASE_HEIGHT + (visible ? workflowLogHeight(this) : 0))*scale;
                const size = [this.size[0],Math.max(minimum,this.size[1]+(Number(visible)-Number(previous))*workflowLogHeight(this)*scale)];
                if (this.setSize) this.setSize(size); else this.size=size;
                this.setDirtyCanvas(true,true);this.graph?.change?.();
                if (visible) restoreWorkflowETA();
            }
            setWorkflowBarAnimation(value) {
                this.properties ??= {};
                this.properties.bar_animation = workflowBarAnimation(value);
                this.setDirtyCanvas(true, false); this.graph?.change?.();
                scheduleWorkflowBarAnimation();
            }
            setWorkflowETATheme(value) {
                this.properties ??= {};
                this.properties.theme = workflowThemeKey(value);
                const theme = workflowTheme(this);
                this.color = theme.title; this.bgcolor = theme.bg;
                this.setDirtyCanvas(true, true);
                this.graph?.change?.();
            }
            onMouseDown(event, pos) {
                if (event?.button !== undefined && event.button !== 0) return;
                const scale = normalizeWorkflowUIScale(this.properties?.ui_scale);
                const x = pos[0] / scale, y = pos[1] / scale;
                const width = this.size[0] / scale;
                const height = this.size[1] / scale;
                if (!this.flags?.collapsed && x >= width-46 && x <= width-18 && y >= height-36 && y <= height-8) {
                    this.setWorkflowLogVisible(!this.properties.logs_visible);return true;
                }
                if (!this.flags?.collapsed && this.properties.logs_visible && y >= height-40-workflowWatchHeight(this) && y <= height-48) {
                    const top=height-40-workflowWatchHeight(this);
                    if(y<=top+24) {
                        if(x>=width-108)openWorkflowWatch(this);
                        else {
                            const before=workflowWatchHeight(this);
                            this.properties.watch_collapsed=!this.properties.watch_collapsed;
                            const size=[this.size[0],this.size[1]+(workflowWatchHeight(this)-before)*scale];
                            if(this.setSize)this.setSize(size);else this.size=size;
                            this.graph?.change?.();
                        }
                    }
                    else if(y<=top+50) {
                        if(x<width/2){this.properties.watch_changes_only=!this.properties.watch_changes_only;this._workflowWatchPage=0;this.graph?.change?.();}
                        else this._workflowWatchPage=Math.max(0,(this._workflowWatchPage||0)+(x<width-64?-1:1));
                    } else openWorkflowWatch(this);
                    this.setDirtyCanvas(true,false);return true;
                }
                if (!this.flags?.collapsed && this.properties.logs_visible && y >= 466 && y <= 488) {
                    const page=Math.max(1,(this._workflowLogPage||10)-1);
                    if(x>=width-106 && x<=width-80) { this._workflowLogScroll=Math.min(this._workflowLogMax||0,(this._workflowLogScroll||0)+page); }
                    else if(x>=width-76 && x<=width-50) { this._workflowLogScroll=Math.max(0,(this._workflowLogScroll||0)-page); }
                    else if(x>=width-46 && x<=width-20) { this._workflowLogScroll=0; }
                    else return;
                    this.setDirtyCanvas(true,false);return true;
                }
                if (!this.flags?.collapsed && x >= width - 110 && x <= width - 82 && y >= 8 && y <= 36) {
                    openWorkflowETABrowserPanel();
                    return true;
                }
                if (!this.flags?.collapsed && x >= width - 78 && x <= width - 50 && y >= 8 && y <= 36) {
                    openWorkflowETAPanel();
                    return true;
                }
                if (!this.flags?.collapsed && x >= width - 46 && x <= width - 18 && y >= 8 && y <= 36) {
                    openWorkflowETASettings(this);
                    return true;
                }
            }
            setWorkflowUIScale(value) {
                const scale = normalizeWorkflowUIScale(value);
                const oldScale = this._workflowUIScale ?? 1;
                this.properties ??= {};
                this.properties.ui_scale = scale;
                this._workflowUIScale = scale;
                const size = [Math.max(480 * scale, this.size[0] * scale / oldScale),
                              Math.max((WORKFLOW_BASE_HEIGHT+(this.properties.logs_visible?workflowLogHeight(this):0))*scale, this.size[1] * scale / oldScale)];
                if (this.setSize) this.setSize(size);
                else this.size = size;
                this.setDirtyCanvas(true, true);
                this.graph?.change?.();
            }
            onConfigure() {
                this.properties ??= {};
                this.properties.ui_scale = normalizeWorkflowUIScale(this.properties.ui_scale);
                this._workflowUIScale = this.properties.ui_scale;
                this.properties.bar_animation = workflowBarAnimation(this.properties.bar_animation);
                this.properties.theme = workflowThemeKey(this.properties.theme);
                this.color = workflowTheme(this).title; this.bgcolor = workflowTheme(this).bg;
                this.properties.logs_visible = this.properties.logs_visible === true;
                this._workflowLogsVisible = this.properties.logs_visible;
                // Saved heights can contain an old expanded-log layout or load-time
                // minimum computed before ui_scale/log visibility were restored.
                // Refit height from the final properties instead of retaining that excess.
                const scale = this._workflowUIScale;
                const width = Number.isFinite(this.size?.[0]) ? Math.max(480 * scale, this.size[0]) : 540 * scale;
                const size = [width, (WORKFLOW_BASE_HEIGHT + (this._workflowLogsVisible ? workflowLogHeight(this) : 0)) * scale];
                if (this.setSize) this.setSize(size); else this.size = size;
                this.setDirtyCanvas(true, false);
                this._workflowRestoring = false;
            }
            onPropertyChanged(name, value) {
                if (this._workflowRestoring) return true;
                if (name === "ui_scale") { this.setWorkflowUIScale(value); return true; }
                if (name === "theme") { this.setWorkflowETATheme(value); return true; }
                if (name === "bar_animation") { this.setWorkflowBarAnimation(value); return true; }
                if (name === "logs_visible") { this.setWorkflowLogVisible(value); return true; }
            }
            getExtraMenuOptions(_, options) {
                const current = normalizeWorkflowUIScale(this.properties?.ui_scale);
                options.push({ content: "Settings…", callback: () => openWorkflowETASettings(this) });
                options.push({ content: this.properties.logs_visible ? "Hide verbose log" : "Show verbose log", callback: () => this.setWorkflowLogVisible(!this.properties.logs_visible) });
                options.push({
                    content: "Scale UI · " + Math.round(current * 100) + "%",
                    submenu: { options: UI_SCALE_PRESETS.map(scale => ({
                        content: (scale === current ? "✓ " : "") + Math.round(scale * 100) + "%",
                        callback: () => {
                            this.graph?.beforeChange?.();
                            try { this.setWorkflowUIScale(scale); }
                            finally { this.graph?.afterChange?.(); }
                        }
                    })) }
                });
            }
            computeSize() {
                const scale = normalizeWorkflowUIScale(this.properties?.ui_scale);
                return [540 * scale, (WORKFLOW_BASE_HEIGHT + (this.properties?.logs_visible ? workflowLogHeight(this) : 0)) * scale];
            }
            onResize(size) {
                const scale = normalizeWorkflowUIScale(this.properties?.ui_scale);
                size[0] = Math.max(480 * scale, size[0]);
                size[1] = Math.max((WORKFLOW_BASE_HEIGHT + (this.properties?.logs_visible ? workflowLogHeight(this) : 0)) * scale, size[1]);
            }
            onDrawForeground(ctx) {
                if (this.flags?.collapsed) return;
                const p = latest;
                const scale = normalizeWorkflowUIScale(this.properties?.ui_scale);
                const width = this.size[0] / scale;
                const running = p.state === "running";
                const done = p.state === "done";
                const abort = p.state === "aborted";
                const theme = workflowTheme(this);
                const completedDurationColor = done ? (workflowThemeKey(this.properties?.theme)==="paper" ? "#806000" : "#ffe066") : theme.strong;
                const color = !connected ? theme.warning : abort ? theme.danger : theme.accent;
                const text = (value, x, y, font = "13px sans-serif", fill = theme.text) => {
                    ctx.font = font; ctx.fillStyle = fill; ctx.fillText(String(value), x, y);
                };
                ctx.save();
                ctx.beginPath(); ctx.rect(0, 0, this.size[0], this.size[1]); ctx.clip();
                ctx.scale(scale, scale);
                text(!connected ? "Disconnected" : done ? "Completed" : abort ? "Stopped / failed" :
                     running ? "Workflow running" : "Ready", 18, 28, "bold 14px sans-serif", color);
                ctx.fillStyle = theme.track; ctx.fillRect(width - 110, 8, 28, 28);
                ctx.strokeStyle = theme.strong; ctx.lineWidth = 1.8; ctx.lineCap = "round";
                ctx.strokeRect(width - 103, 15, 14, 12);
                ctx.beginPath(); ctx.moveTo(width - 96, 11); ctx.lineTo(width - 91, 11); ctx.lineTo(width - 91, 16); ctx.stroke();
                ctx.fillStyle = theme.track; ctx.fillRect(width - 78, 8, 28, 28);
                ctx.strokeStyle = theme.strong; ctx.lineWidth = 1.8; ctx.lineCap = "round";
                ctx.beginPath(); ctx.arc(width - 64, 22, 8.5, 0, Math.PI * 2); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(width - 64, 22); ctx.lineTo(width - 64, 16.5); ctx.lineTo(width - 58.5, 25); ctx.stroke();
                ctx.fillStyle = theme.track; ctx.fillRect(width - 46, 8, 28, 28);
                text("⚙", width - 41, 29, "20px sans-serif", theme.strong);
                const remaining = Number.isFinite(p.remaining) ? p.remaining : null;
                const main = !connected ? "—" : done ? formatWorkflowDuration(p.elapsed) :
                    abort ? "Stopped" : running ? remaining === null || (remaining === 0 && !p.eta_detail) ? "Estimating…" :
                    (p.lower_bound ? "Known ≥ " : "≈ ") + formatWorkflowDuration(p.lower_bound && Number.isFinite(p.low) ? p.low : remaining) : "—";
                text(main, 18, 76, "bold 30px sans-serif", completedDurationColor);
                text(done ? "Total duration" : p.lower_bound ? "Known-time subtotal · missing work excluded" : "Estimated remaining time", 18, 98, "12px sans-serif", theme.muted);
                const finish = connected && running && remaining > 0 && Number.isFinite(p.finish_ts) ?
                    new Date(p.finish_ts * 1000).toLocaleTimeString() : "—";
                text("Finish " + (p.lower_bound ? "known timings only ≈ " : "≈ ") + finish, 18, 126);
                const confidence = p.confidence === "lower-bound" ? "Partial estimate · incomplete history" :
                    p.confidence === "estimated" ? (p.runtime_scaling?.tier === "runtime-video-provisional" ? "Estimated · provisional video ratio" :
                        p.runtime_scaling?.tier === "runtime-video-learned" ? "Estimated · learned runtime video curve" :
                        p.runtime_scaling?.tier === "runtime-video-profile" ? "Estimated · source-based video projection" :
                        p.runtime_scaling?.tier === "runtime-step-scaled" ? "Estimated · runtime sampler step scaling" : p.workload_approximate ? "Estimated · dimensions unresolved" : p.workload_scaling ? "Estimated · learned " + p.workload_scaling + " scaling" : p.step_scaled ? "Estimated · sampler step scaling" : "Estimated · fallback history") : "Learned from history";
                const detail=p.eta_detail;
                const fit=(value)=>workflowWatchFit(ctx,value,width-42);
                ctx.font="12px sans-serif";
                text(running ? fit(detail?.timing_message || confidence) : "Updates automatically for every workflow",18,149,"12px sans-serif",theme.muted);
                // Primary timing card: large value with persistent uncertainty wording.
                ctx.fillStyle=theme.track;ctx.fillRect(12,158,width-24,100);
                const confidenceLevel=workflowConfidenceLevel(p);
                if(confidenceLevel>=0){ctx.globalAlpha=.25;ctx.fillStyle=WORKFLOW_CONFIDENCE_COLORS[confidenceLevel];ctx.fillRect(12,158,width-24,100);ctx.globalAlpha=1;}
                const stripX=width-72;
                const confidenceGradient=ctx.createLinearGradient(stripX,158,stripX,258);
                confidenceGradient.addColorStop(0,WORKFLOW_CONFIDENCE_COLORS[4]);
                confidenceGradient.addColorStop(1,WORKFLOW_CONFIDENCE_COLORS[0]);
                ctx.fillStyle=confidenceGradient;ctx.fillRect(stripX,158,60,100);
                ctx.textAlign="center";
                text("+",stripX+30,174,"bold 14px sans-serif","#10181c");
                text("ETA",stripX+30,202,"bold 12px sans-serif","#10181c");
                text("confidence",stripX+30,216,"10px sans-serif","#10181c");
                text("−",stripX+30,250,"bold 14px sans-serif","#10181c");
                ctx.textAlign="left";
                const full=running ? (detail?(Number.isFinite(detail.provisional_remaining)?detail.provisional_remaining:null):remaining) : done?p.elapsed:null;
                const fullRange=running && p.lower_bound && Number.isFinite(p.low) && Number.isFinite(p.high) && p.high>p.low
                    ? "≈ "+formatWorkflowDuration(p.low)+" – "+formatWorkflowDuration(p.high) : null;
                const fullLabel=done?"Actual total duration":running?(p.lower_bound?"Provisional full remaining estimate":"Full remaining estimate"):"Full remaining estimate";
                text(fullLabel,26,181,"bold 17px sans-serif",theme.strong);
                text(!connected?"Disconnected":fullRange?fullRange:Number.isFinite(full)?(done?"":"≈ ")+formatWorkflowDuration(full):running?"Waiting for timing…":"—",26,221,"bold 38px sans-serif",completedDurationColor);
                const omitted=detail?.unpriced_nodes?.length||0;
                const caveat=omitted?omitted+" nodes unpriced · "+(detail.provisional_notes?.[0]||"Their duration is excluded"):
                    detail?.graph_incomplete?"Graph incomplete; additional work may be missing":detail?.provisional_notes?.[0]||
                    (running?"Live step timing refines this estimate":"");
                ctx.font="11px sans-serif";
                text(fit(caveat),26,246,"11px sans-serif",p.lower_bound?theme.warning:theme.text);
                const fraction = done ? 1 : running && Number.isFinite(p.fraction) ?
                    Math.max(0, Math.min(.99, p.fraction)) : 0;
                drawWorkflowProgressBar(ctx, this, theme, color, width, fraction, running);
                if (running) scheduleWorkflowBarAnimation();
                text(running || done ? (p.nodes_done ?? 0) + " / " + (p.nodes_total ?? 0) +
                    " nodes · " + (p.nodes_cached ?? 0) + " cached" : "No connections required", 18, 310);
                text(running||done||abort ? "Elapsed: "+formatWorkflowDuration(p.elapsed):"Elapsed: —",18,336,"bold 20px sans-serif",theme.strong);
                text(running?(p.cold?"Cold start":"Warm start"):done?"Completed":abort?"Stopped":"",width-130,336,"12px sans-serif",theme.muted);
                const label = running && p.node_class ? "Current: " + p.node_class + " (#" + p.node + ")" : "";
                ctx.font = "12px sans-serif";
                let fitted = label;
                while (fitted && ctx.measureText(fitted).width > width - 45) fitted = fitted.slice(0, -1);
                text(fitted === label ? fitted : fitted + "…", 18, 358, "12px sans-serif", theme.muted);
                const started=Number.isFinite(p.start_ts)?new Date(p.start_ts*1000).toLocaleString(undefined,{
                    year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";
                text("Started: "+started,18,380,"12px sans-serif",theme.text);
                // Expected total and expected finish share a two-column row.
                const half=(width-48)/2, right=30+half;
                const expected=Number.isFinite(p.expected_total)?p.expected_total:null;
                const totalValue=abort?formatWorkflowDuration(p.elapsed):Number.isFinite(expected)?formatWorkflowDuration(expected):running?"Pending":"—";
                const finishTs=running && Number.isFinite(expected) && Number.isFinite(p.start_ts)?p.start_ts+expected:
                    done && Number.isFinite(p.finish_ts)?p.finish_ts:null;
                const finishDate=finishTs===null?null:new Date(finishTs*1000);
                const today=new Date();
                const finishValue=finishDate?finishDate.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";
                const nextDay=finishDate && finishDate.toDateString()!==today.toDateString()?finishDate.toLocaleDateString(undefined,{month:"short",day:"numeric"}):"";
                const qualifier=running?(p.expected_total_incomplete?" · incomplete":p.lower_bound?" · provisional":""):"";
                ctx.fillStyle=theme.track;ctx.fillRect(12,392,width-24,62);
                text(done?"Actual total":abort?"Elapsed before stop":"Expected total"+qualifier,22,412,"bold 15px sans-serif",theme.strong);
                text(done?"Finished at":"Expected finish time",right,412,"bold 15px sans-serif",theme.strong);
                text((running && expected!==null?"≈ ":"")+totalValue,22,441,"bold 23px sans-serif",completedDurationColor);
                ctx.font="bold 20px sans-serif";
                text(workflowWatchFit(ctx,finishValue+(nextDay?" · "+nextDay:""),half-8),right,441,"bold 20px sans-serif",p.expected_total_incomplete?theme.warning:theme.strong);
                drawWorkflowLogs(ctx,this,theme,width,this.size[1]/scale,text);
                ctx.restore();
            }
        }
        for (const name of Object.getOwnPropertyNames(WorkflowETADisplayMethods.prototype)) {
            if (name === "constructor") continue;
            const original = nodeType.prototype[name];
            const extra = WorkflowETADisplayMethods.prototype[name];
            nodeType.prototype[name] = function (...args) {
                const result = original?.apply(this, args);
                const added = extra.apply(this, args);
                return added === undefined ? result : added;
            };
        }
    },
    setup() {
        api.addEventListener("workflow.eta", event => { connected = true; updateWorkflowETA(event.detail); });
        api.addEventListener("execution_start", event => updateWorkflowETA({
            state: "running", prompt_id: event.detail?.prompt_id, confidence: "lower-bound", lower_bound: true
        }));
        api.addEventListener("reconnecting", () => { connected = false; revision++; for (const n of displays) n.setDirtyCanvas(true, false); });
        api.addEventListener("reconnected", () => {
            connected = true;
            updateWorkflowETA({ state: "idle" });
            restoreWorkflowETA();
        });
        document.addEventListener("visibilitychange", scheduleWorkflowBarAnimation);
        workflowReducedMotion?.addEventListener?.("change", () => {
            for (const node of displays) node.setDirtyCanvas(true, false);
            scheduleWorkflowBarAnimation();
        });
        restoreWorkflowETA();
        console.info("[workflow-eta] display ready · add Workflow ETA from the node menu");
    }
});
