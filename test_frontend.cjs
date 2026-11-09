const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
let extension, NodeType;
const handlers = {};
const app = { registerExtension(e) { extension = e; } };
const api = {
    addEventListener(n, f) { handlers[n] = f; },
    async fetchApi() { return { ok: true, json: async () => ({ state: "idle" }) }; }
};
class LGraphNode {
    constructor() { this.flags = {}; }
    setDirtyCanvas() {}
}
const motionPreference = {matches:false,addEventListener(){}};
const sandbox = { AbortController, matchMedia:()=>motionPreference, document: {hidden:false,addEventListener(){}}, app, api, LGraphNode, LiteGraph: { registerNodeType(n, C) { NodeType = C; } }, console, Date };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(__dirname + "/js/workflow_eta.js", "utf8").replace(/^import .*;\n/gm, ""), sandbox);
NodeType = class extends LGraphNode {};
extension.beforeRegisterNodeDef(NodeType, {name: "WorkflowETA"});
extension.setup();
const node = new NodeType();
node.onNodeCreated();
assert(node.isVirtualNode);
node.onAdded();
const labels = [];
const transforms = [];
const ctx = { scale(x,y){ transforms.push([x,y]); }, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){}, fillRect(){},
    moveTo(){},lineTo(){},closePath(){},fill(){},createLinearGradient(){return {addColorStop(){}};},
    fillText(t){ labels.push(t); }, measureText(t){ return { width: t.length * 6 }; } };
const emit = (name, detail) => handlers[name]({detail});
emit("execution_start", {prompt_id:"one"});
node.onDrawForeground(ctx);
assert(labels.includes("Estimating…"));
labels.length = 0;
emit("workflow.eta", {state:"running", remaining:65, finish_ts:Date.now()/1000+65,
    elapsed:3, confidence:"estimated", fraction:.05, nodes_done:2, nodes_total:8,
    nodes_cached:4, node:"6", node_class:"KSampler", cold:true});
node.onDrawForeground(ctx);
assert(labels.includes("≈ 1m 5s"));
assert(labels.includes("2 / 8 nodes · 4 cached"));
assert(labels.includes("Current: KSampler (#6)"));
emit("reconnecting");
labels.length=0;
node.onDrawForeground(ctx);
assert(labels.includes("Disconnected"));
emit("workflow.eta", {state:"done",elapsed:12.4,finish_ts:Date.now()/1000,nodes_done:8,nodes_total:8});
labels.length=0;
node.onDrawForeground(ctx);
assert(labels.includes("Completed") && labels.includes("13 s"));
emit("workflow.eta", {state:"aborted"});
labels.length=0;node.onDrawForeground(ctx);
assert(labels.includes("Stopped / failed"));
node.onResize([100,100]);
// Scale presets resize the panel and persist through properties/configuration.
const menu = [];
node.getExtraMenuOptions(null, menu);
const scaling = menu.find(item => item.content.startsWith("Scale UI"));
assert(scaling && scaling.submenu.options.length === 9);
const originalSize = [...node.size];
scaling.submenu.options.find(item => item.content === "200%").callback();
assert(node.properties.ui_scale === 2);
assert(node.size[0] === originalSize[0] * 2 && node.size[1] === originalSize[1] * 2);
node.onDrawForeground(ctx);
assert.deepStrictEqual(transforms.at(-1), [2,2]);
const restored = new NodeType();
restored.onNodeCreated();
restored.properties = JSON.parse(JSON.stringify(node.properties));
restored.size = [...node.size];
restored.onConfigure();
assert(restored.properties.ui_scale === 2 && restored.size[1] === node.size[1]);
restored.onPropertyChanged("ui_scale", 3);
assert(restored.size[1] === node.size[1] * 1.5);
restored.onPropertyChanged("ui_scale", NaN);
assert(restored.properties.ui_scale === 1);
restored.setWorkflowUIScale(99);
assert(restored.properties.ui_scale === 3);
node.onRemoved();
console.log("PASS: Python-defined node decoration, event updates, display rendering, disconnect, completion, interruption, scaling and persistence");

class Element {
    constructor(tag) { this.value=""; this.tagName = tag; this.children = []; this.style = {cssText:""}; this.events = {}; this.attrs = {}; this.isConnected = false; }
    replaceChildren(...items) { this.children=[];this.append(...items); }
    setAttribute(key,value) { this.attrs[key] = value; }
    append(...items) { for (const item of items) { this.children.push(item); item.parent = this; item.isConnected = true; } }
    addEventListener(name,fn) { (this.events[name] ??= []).push(fn); }
    dispatch(name) { for (const fn of this.events[name] ?? []) fn({target:this,stopPropagation(){},clientX:10,clientY:10}); }
    showModal() { this.open = true; }
    focus() { this.focused = true; }
    close() { this.open = false; this.dispatch("close"); }
    remove() { this.isConnected = false; if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    getBoundingClientRect() { return {left:0,right:340,top:0,bottom:300}; }
}
sandbox.document = {hidden:false,addEventListener(){},createElement: tag => new Element(tag), body: new Element("body")};
const findElement = (root,tag) => root.tagName === tag ? root : root.children.map(child => findElement(child,tag)).find(Boolean);
const settingsNode = new NodeType(); settingsNode.onNodeCreated(); settingsNode.setWorkflowUIScale(2);
assert(settingsNode.onMouseDown({button:0},[10,10]) === undefined);
assert(settingsNode.onMouseDown({button:0},[settingsNode.size[0]-60,40]) === true);
const dialog = settingsNode._workflowSettingsDialog;
assert(dialog.open && dialog.attrs["aria-label"] === "Workflow ETA settings");
const dropdown = findElement(dialog,"select");
assert(dropdown.children.length === 7);
assert.deepStrictEqual(dropdown.children.map(item=>item.textContent),["Ocean","Midnight","Graphite","Forest","Ember","Amethyst","Paper"]);
dropdown.value="paper"; dropdown.dispatch("change");
assert(settingsNode.properties.theme === "paper" && settingsNode.bgcolor === "#f4f7f9");
assert(dialog.style.backgroundColor === settingsNode.bgcolor);
const slider = findElement(dialog,"input"); slider.value="1.5"; slider.dispatch("input");
assert(settingsNode.properties.ui_scale === 1.5);
assert(findElement(dialog,"output").textContent === "150%");
const saved = new NodeType(); saved.onNodeCreated(); saved.properties=JSON.parse(JSON.stringify(settingsNode.properties));saved.size=[...settingsNode.size];saved.onConfigure();
assert(saved.properties.theme === "paper" && saved.bgcolor === settingsNode.bgcolor && saved.properties.ui_scale === 1.5);
settingsNode.onRemoved();assert(!dialog.isConnected && settingsNode._workflowSettingsDialog === null);
saved.setWorkflowETATheme("invalid");assert(saved.properties.theme === "ocean");
console.log("PASS: scaled gear hit area, settings slider, seven themes, live palette, workflow persistence, removal cleanup");


const animationNode = new NodeType(); animationNode.onNodeCreated(); animationNode.onAdded();
const frames=[];
sandbox.requestAnimationFrame = callback => { frames.push(callback); return frames.length; };
emit("workflow.eta", {state:"running",remaining:10,fraction:.4,confidence:"estimated",elapsed:2});
assert(frames.length===1);
const rects=[];const oldFill=ctx.fillRect;ctx.fillRect=(...args)=>rects.push(args);
for(const style of ["static","stripes","shimmer","pulse","scanner","comet","wave","rainbow"]){
    animationNode.setWorkflowBarAnimation(style);rects.length=0;animationNode.onDrawForeground(ctx);
    assert(rects.some(rect=>rect[0]===18 && rect[1]===279 && Math.abs(rect[2]-(540-36)*.4)<.0001));
}
ctx.fillRect=oldFill;
animationNode.onMouseDown({button:0},[animationNode.size[0]-30,20]);
const animationSelect=findElement(animationNode._workflowSettingsDialog,"select");
const findAnimation=root=>root.attrs?.["aria-label"]==="Progress bar animation"?root:root.children.map(findAnimation).find(Boolean);
const options=findAnimation(animationNode._workflowSettingsDialog);
assert(options.children.length===8);options.value="pulse";options.dispatch("change");
assert(animationNode.properties.bar_animation==="pulse");
const reloaded=new NodeType();reloaded.onNodeCreated();reloaded.properties=JSON.parse(JSON.stringify(animationNode.properties));reloaded.onConfigure();
assert(reloaded.properties.bar_animation==="pulse");
// Each stop condition prevents a queued frame from rescheduling itself.
let last=frames.at(-1);const count=frames.length;
sandbox.document.hidden=true;last(1000);assert(frames.length===count);
sandbox.document.hidden=false;animationNode.setWorkflowBarAnimation("wave");
last=frames.at(-1);const reducedCount=frames.length;motionPreference.matches=true;last(2000);assert(frames.length===reducedCount);
motionPreference.matches=false;animationNode.setWorkflowBarAnimation("scanner");
last=frames.at(-1);const doneCount=frames.length;emit("workflow.eta",{state:"done",elapsed:10});last(3000);assert(frames.length===doneCount);
animationNode.onRemoved();
console.log("PASS: eight animation styles preserve fill extent, settings/persistence, hidden-tab/reduced-motion/completion scheduling");

const logNode = new NodeType();logNode.onNodeCreated();logNode.onAdded();
const hiddenHeight=logNode.size[1];
assert(!logNode.properties.logs_visible);
assert(logNode.onMouseDown({button:0},[logNode.size[0]-30,logNode.size[1]-20])===true);
assert(logNode.properties.logs_visible && logNode.size[1]===hiddenHeight+480);
const logData=Array.from({length:30},(_,i)=>({id:i+1,ts:1700000000+i,message:"Diagnostic entry "+i+" : "+"details ".repeat(8)}));
emit("workflow.eta",{state:"running",remaining:1,elapsed:1,log_session:"server-a",logs:logData});
labels.length=0;logNode.onDrawForeground(ctx);
assert(labels.some(label=>label.startsWith("Verbose log · 30")));
assert(labels.some(label=>label.includes("Diagnostic entry 29")));
const cachedRows=logNode._workflowLogCache;
logNode.onDrawForeground(ctx);assert(logNode._workflowLogCache===cachedRows);
logNode.onMouseDown({button:0},[logNode.size[0]-95,476]);
assert(logNode._workflowLogScroll>0);
logNode.onMouseDown({button:0},[logNode.size[0]-33,476]);assert(logNode._workflowLogScroll===0);
// Replayed/delta entries are deduplicated.
emit("workflow.eta",{state:"done",elapsed:2,log_session:"server-a",logs:logData.slice(-2)});
labels.length=0;logNode.onDrawForeground(ctx);assert(labels.includes("Verbose log · 30"));
const logRestored=new NodeType();logRestored.onNodeCreated();
logRestored.properties=JSON.parse(JSON.stringify(logNode.properties));logRestored.size=[...logNode.size];logRestored.onConfigure();
assert(logRestored.properties.logs_visible && logRestored.size[1]===logNode.size[1]);
logRestored.setWorkflowUIScale(2);assert(logRestored.size[1]===logNode.size[1]*2);
logRestored.onMouseDown({button:0},[logRestored.size[0]-60,logRestored.size[1]-40]);
assert(!logRestored.properties.logs_visible && logRestored.size[1]===hiddenHeight*2);
emit("workflow.eta",{state:"idle",log_session:"server-b",logs:[{id:1,ts:1700000010,message:"Restarted backend"}]});
labels.length=0;logNode.onDrawForeground(ctx);assert(labels.includes("Verbose log · 1"));
const lots=Array.from({length:300},(_,i)=>({id:i+2,ts:1700000000+i,message:"Entry "+i}));
emit("workflow.eta",{state:"idle",log_session:"server-b",logs:lots});
labels.length=0;logNode.onDrawForeground(ctx);assert(labels.includes("Verbose log · 250"));
logNode.onMouseDown({button:0},[logNode.size[0]-30,logNode.size[1]-20]);
assert(!logNode.properties.logs_visible && logNode.size[1]===hiddenHeight);
logNode.onRemoved();
console.log("PASS: bottom-right log toggle, expanded layout, wrapping/cache, browsing, persistence/scaling, deduplicated bounded logs and backend restart reset");

// Semantic category priority, wrapped rows and live theme changes.
vm.runInContext(`
    for (const [message,kind] of [
        ["startup: wrapped instance send_sync","info"],
        ["run abc: predicted 4s","run"],
        ["node 6 KSampler: 4s","node"],
        ["run done: actual 4s","success"],
        ["  ETA check @ 1s: predicted 3s","timing"],
        ["node 6: insufficient calibration; ETA is a lower bound","warning"],
        ["run discarded, not recorded (interrupted)","warning"],
        ["history save failed; run completed","error"]
    ]) { if(workflowLogKind(message)!==kind) throw Error(message); }
    function luminance(color) {
        const values=color.slice(1).match(/../g).map(v=>parseInt(v,16)/255)
            .map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
        return values[0]*.2126+values[1]*.7152+values[2]*.0722;
    }
    for(const [key,palette] of Object.entries(WORKFLOW_LOG_PALETTES)) {
        const bg=luminance(WORKFLOW_ETA_THEMES[key].bg);
        for(const color of Object.values(palette)) {
            const fg=luminance(color);
            if((Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05)<4.5) throw Error(key+" low contrast: "+color);
        }
    }
    mergeWorkflowLogs({log_session:"palette-test",logs:[{id:1,ts:1700000000,message:"history save failed; "+"x".repeat(100)}]});
    const cacheNode={properties:{theme:"paper"}};
    const rows=workflowLogRows(cacheNode,300);
    if(rows.length<2 || rows.some(row=>row.kind!=="error")) throw Error("wrapped severity lost");
    const painted=[];
    cacheNode.properties.logs_visible=true;
    const fakeCtx={measureText(t){return {width:t.length*6};},fillRect(){},save(){},restore(){},beginPath(){},rect(){},clip(){}};
    drawWorkflowLogs(fakeCtx,cacheNode,WORKFLOW_ETA_THEMES.paper,350,972,(t,x,y,font,color)=>painted.push([t,color]));
    if(!painted.some(([t,c])=>t.includes("[ERROR]") && c===WORKFLOW_LOG_PALETTES.paper.error)) throw Error("paper palette not rendered");
    cacheNode.properties.theme="ocean";painted.length=0;
    drawWorkflowLogs(fakeCtx,cacheNode,WORKFLOW_ETA_THEMES.ocean,350,972,(t,x,y,font,color)=>painted.push([t,color]));
    if(!painted.some(([t,c])=>t.includes("[ERROR]") && c===WORKFLOW_LOG_PALETTES.ocean.error)) throw Error("theme switch stale colors");
`,sandbox);
console.log("PASS: seven dedicated log palettes, semantic severity and labels, wrapping, live theme changes, 4.5:1 minimum text contrast");

const watchedRows=Array.from({length:125},(_,i)=>({id:String(i),node:"6",class:"KSampler",group:"Literal",parameter:i===0?"steps":"value_"+i,value:i===0?"40":"<safe> "+i,previous:i===0?"20":"<safe> "+i,change:i===0?"changed":"same"}));
const watchedPayload={state:"running",prompt_id:"new",watched:{prompt_id:"new",baseline_prompt:"old",rows:watchedRows,nodes:{"6":{class:"KSampler",predicted:20,initial_predicted:18,initial_tier:"node-class",samples:4,low:15,high:25,calibration:{axis:"steps"},actual:null,tier:"step-scaled",cached:false,unresolved:false}},previous_nodes:{"6":{class:"KSampler",predicted:10,actual:11}}}};
const inspectNode=new NodeType();inspectNode.onNodeCreated();inspectNode.onAdded();inspectNode.setWorkflowLogVisible(true);
emit("workflow.eta",watchedPayload);
labels.length=0;inspectNode.onDrawForeground(ctx);
assert(labels.some(t=>t.includes("Watched data · 125")));
const fullInspectHeight=inspectNode.size[1];
inspectNode.onMouseDown({button:0},[30,fullInspectHeight-288]);
assert(inspectNode.properties.watch_collapsed && inspectNode.size[1]===fullInspectHeight-220);
inspectNode.onMouseDown({button:0},[30,inspectNode.size[1]-68]);
assert(!inspectNode.properties.watch_collapsed && inspectNode.size[1]===fullInspectHeight);
inspectNode.onMouseDown({button:0},[30,fullInspectHeight-266]);
assert(inspectNode.properties.watch_changes_only);
labels.length=0;inspectNode.onDrawForeground(ctx);assert(labels.some(t=>t.includes("Watched data · 1")));
inspectNode.onMouseDown({button:0},[inspectNode.size[0]-50,fullInspectHeight-288]);
const inspector=sandbox.document.body.children.at(-1);assert(inspector.open);
const inspectControls=inspector.children.find(e=>e.tagName==="div" && e.children.some(c=>c.tagName==="input"));
const inspectSearch=inspectControls.children.find(e=>e.tagName==="input");
const inspectSelects=inspectControls.children.filter(e=>e.tagName==="select");
inspectSelects[0].value="values";inspectSelects[1].value="all";inspectSelects[2].value="All groups";inspectSearch.value="";
inspectSearch.dispatch("input");
const inspectTable=inspector.children.at(-1).children[0];
assert(inspectTable.children[1].children.length===100);
inspectSelects[1].value="changed";inspectSelects[1].dispatch("input");
assert(inspectTable.children[1].children.length===1);
assert(inspectTable.children[1].children[0].children.some(e=>e.textContent==="steps"));
inspectSelects[0].value="timing";inspectSelects[0].dispatch("input");
assert(inspectTable.children[1].children[0].children.some(e=>e.textContent==="+10.00 s"));
assert(inspectTable.children[1].children[0].children.some(e=>e.textContent==="18.00 s"));
assert(inspectTable.children[1].children[0].children.some(e=>e.textContent==="4"));
assert(inspectTable.children[1].children[0].children.some(e=>e.textContent==="15.00 s – 25.00 s"));
assert(inspectTable.children[1].children[0].title.includes('steps'));
inspectSelects[1].value="all";inspectSelects[0].value="values";inspectSearch.value="<safe>";inspectSearch.dispatch("input");
assert(inspectTable.children[1].children.some(r=>r.children.some(e=>String(e.textContent).includes("<safe>"))));
emit("workflow.eta",{...watchedPayload,watched:{...watchedPayload.watched,rows:watchedRows.slice(0,1)}});
assert(inspectSearch.value==="<safe>");
inspectNode.onRemoved();assert(!inspector.isConnected);
console.log("PASS: watched table, collapse/resize, changed filter, searchable inspector, pagination, safe value rendering, timing deltas, live updates and cleanup");

const explained=new NodeType();explained.onNodeCreated();explained.onAdded();
emit("workflow.eta",{state:"running",remaining:0,elapsed:231,confidence:"lower-bound",lower_bound:true,finish_ts:Date.now()/1000,eta_detail:{known_remaining:0,provisional_remaining:1616,timing_message:"First step done; regular speed still unknown",coverage_message:"1 nodes still have no duration",unpriced_nodes:[{node:"d",class:"VAEDecode"}],provisional_notes:["First-step guess includes startup; may be high"],graph_incomplete:false}});
labels.length=0;explained.onDrawForeground(ctx);
assert(labels.includes("Known ≈ 0 s"));
assert(labels.some(t=>t.includes("Provisional full remaining estimate")));
assert(labels.some(t=>t.includes("1 nodes unpriced")));
assert(labels.some(t=>t.includes("First-step guess includes startup")));
emit("workflow.eta",{state:"running",remaining:60,elapsed:241,confidence:"estimated",eta_detail:{provisional_remaining:60,timing_message:"Sampling: provisional speed (1–2 intervals)",unpriced_nodes:[],provisional_notes:[],graph_incomplete:false}});
labels.length=0;explained.onDrawForeground(ctx);
assert(labels.some(t=>t==="Full remaining estimate"));
assert(!labels.some(t=>t.includes("startup")));
explained.onRemoved();
console.log("PASS: explicit known subtotal, provisional full ETA/finish, unpriced count, startup caveat and regular-step replacement text");

const datesNode=new NodeType();datesNode.onNodeCreated();
emit("workflow.eta",{state:"running",start_ts:1700000000,expected_total:3600,expected_total_incomplete:true,elapsed:60,remaining:100,lower_bound:true});
labels.length=0;datesNode.onDrawForeground(ctx);
assert(labels.some(t=>t.startsWith("Started: ") && t!=="Started: —"));
assert(labels.some(t=>t.startsWith("Expected total")));
emit("workflow.eta",{state:"done",start_ts:1700000000,expected_total:1800,elapsed:1800,finish_ts:1700001800});
labels.length=0;datesNode.onDrawForeground(ctx);assert(labels.includes("Actual total") && labels.includes("30m 0s"));
datesNode.onRemoved();
console.log("PASS: localized start date/time, expected total with incomplete label, actual total on completion");

const prominent=new NodeType();prominent.onNodeCreated();
const drawn=[];const oldText=ctx.fillText;ctx.fillText=function(t,x,y){drawn.push({t,x,y,font:this.font});};
emit("workflow.eta",{state:"running",remaining:600,elapsed:120,start_ts:Date.now()/1000-120,expected_total:720,eta_detail:{provisional_remaining:600,unpriced_nodes:[],provisional_notes:[]}});
prominent.onDrawForeground(ctx);
const totalHeading=drawn.find(r=>r.t==="Expected total"),finishHeading=drawn.find(r=>r.t==="Expected finish time");
assert(totalHeading && finishHeading && totalHeading.y===finishHeading.y && finishHeading.x>totalHeading.x);
assert(drawn.some(r=>r.t==="≈ 10m 0s" && r.font==="bold 38px sans-serif"));
assert(drawn.some(r=>r.t.startsWith("Elapsed:") && r.font==="bold 20px sans-serif"));
assert(drawn.some(r=>r.t==="≈ 12m 0s" && r.font==="bold 23px sans-serif"));
assert(prominent.size[0]===540);prominent.onResize([1,1]);
ctx.fillText=oldText;prominent.onRemoved();
console.log("PASS: prominent remaining/elapsed/total typography and expected finish positioned after total on the same row");

(async()=>{
    const diagnosticNode=new NodeType();diagnosticNode.onNodeCreated();
    diagnosticNode.onMouseDown({button:0},[diagnosticNode.size[0]-30,20]);
    const dlg=diagnosticNode._workflowSettingsDialog;
    const visit=(root,predicate)=>predicate(root)?root:root.children.map(e=>visit(e,predicate)).find(Boolean);
    const button=visit(dlg,e=>e.tagName==='button' && e.textContent==='Export diagnostic log (ZIP)');
    const count=visit(dlg,e=>e.tagName==='input' && e.type==='number');
    const status=visit(dlg,e=>e.attrs?.role==='status');
    assert(button && count.value==='20');
    count.value='0';await button.events.click[0]();assert(status.textContent==='Choose 1 to 100 runs.');
    let downloaded=false,revoked=false,requested='';
    sandbox.URL={createObjectURL(){return 'blob:test';},revokeObjectURL(){revoked=true;}};
    sandbox.setTimeout=callback=>callback();
    Element.prototype.click=function(){if(this.download==='Workflow_ETA_diagnostics.zip')downloaded=true;};
    const previousFetch=api.fetchApi;
    api.fetchApi=async path=>{requested=path;return {ok:true,blob:async()=>({})};};
    count.value='10';await button.events.click[0]();
    assert(requested==='/workflow/eta/diagnostics/export?limit=10' && downloaded && revoked && !button.disabled);
    assert(status.textContent.includes('RUN_SUMMARY.md'));
    api.fetchApi=async()=>({ok:false,status:409,json:async()=>({error:'Diagnostic recording is disabled'})});
    await button.events.click[0]();assert(status.textContent==='Diagnostic recording is disabled' && !button.disabled);
    let pendingSignal;
    api.fetchApi=async(path,options)=>new Promise((resolve,reject)=>{
        pendingSignal=options.signal;
        pendingSignal.addEventListener('abort',()=>reject(Object.assign(new Error('cancelled'),{name:'AbortError'})));
    });
    const pending=button.events.click[0]();
    diagnosticNode._workflowLogCache={rows:new Array(1000)};
    diagnosticNode.onRemoved();await pending;
    assert(pendingSignal.aborted && diagnosticNode._workflowLogCache===null);
    api.fetchApi=previousFetch;
    console.log('PASS: diagnostic export control, run-count validation, ZIP download, URL cleanup and readable server errors');
})().catch(error=>{console.error(error);process.exitCode=1;});

const yellowNode=new NodeType();yellowNode.onNodeCreated();
const yellowPaint=[];const previousPainter=ctx.fillText;ctx.fillText=function(t,x,y){yellowPaint.push({t,y,color:this.fillStyle});};
for(const theme of ["ocean","midnight","graphite","forest","ember","amethyst","paper"]){
 yellowNode.setWorkflowETATheme(theme);
 emit("workflow.eta",{state:"done",elapsed:60,expected_total:60,finish_ts:1700000000});
 yellowPaint.length=0;yellowNode.onDrawForeground(ctx);
 const expected=theme==="paper"?"#806000":"#ffe066";
 for(const y of [76,221,441])assert(yellowPaint.some(r=>r.y===y && r.color===expected),theme+" completed duration "+y);
 emit("workflow.eta",{state:"running",elapsed:10,remaining:50,expected_total:60,eta_detail:{provisional_remaining:50,unpriced_nodes:[],provisional_notes:[]}});
 yellowPaint.length=0;yellowNode.onDrawForeground(ctx);
 assert(!yellowPaint.some(r=>[76,221,441].includes(r.y)&&r.color===expected));
}
ctx.fillText=previousPainter;yellowNode.onRemoved();
console.log("PASS: completed duration values yellow in all seven themes; running values unchanged");

let fitMeasurements=0,largestFitInput=0;
sandbox.fitTestContext={measureText(value){fitMeasurements++;largestFitInput=Math.max(largestFitInput,value.length);return {width:value.length*6};}};
sandbox.fitTestValue='x'.repeat(200000);
const fitted=vm.runInContext('workflowWatchFit(fitTestContext,fitTestValue,100)',sandbox);
assert(fitted.endsWith('…') && fitted.length<=17 && fitMeasurements<20 && largestFitInput<=2048);
console.log('PASS: long watched values use bounded text measurement without quadratic substring churn');
