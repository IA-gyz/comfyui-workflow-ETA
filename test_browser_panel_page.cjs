// Executes the served /workflow/eta/panel page script against a small DOM stub. getElementById is strict: like a real
// browser it throws when called detached from `document` (the page once did that inside boot()'s try/catch, so it
// silently never showed anything). Needs python3 to produce the page; no npm packages.
const vm = require("vm");
const assert = require("assert");
const { execFileSync } = require("child_process");

const html = execFileSync("python3", ["-c", `
import sys
sys.path.insert(0, ${JSON.stringify(__dirname)})
import workflow_eta_tracker as t
sys.stdout.write(t._workflow_eta_panel_page_html())`], { encoding: "utf8" });
const script = html.split("<script>")[1].split("</script>")[0];
const themes = JSON.parse(script.split("const THEMES=")[1].split(";let theme")[0]);

function makePage(status) {
    const elements = new Map(), vars = {}, sockets = [], errors = [], urls = [];
    const document = {
        getElementById(id) {
            if (this !== document) throw new TypeError("Illegal invocation");
            if (!elements.has(id)) elements.set(id, { textContent: "", style: {} });
            return elements.get(id);
        },
        documentElement: { style: { setProperty: (name, value) => { vars[name] = value; } } },
    };
    const sandbox = {
        document, location: { protocol: "http:", host: "127.0.0.1:8188" }, console, Date, Math, Number, JSON, Promise,
        fetch: async (url) => { urls.push(url); if (status instanceof Error) throw status; return { json: async () => status }; },
        WebSocket: class { constructor(url) { this.url = url; sockets.push(this); } },
        setTimeout: () => 0,
    };
    vm.createContext(sandbox);
    vm.runInContext(script, sandbox);
    const tick = () => new Promise((resolve) => setImmediate(resolve));
    const text = (id) => document.getElementById(id).textContent;
    const send = (type, data) => sockets[0].onmessage({ data: JSON.stringify({ type, data }) });
    return { tick, text, vars, sockets, send, element: (id) => document.getElementById(id), errors, urls };
}

(async () => {
    const running = { state: "running", remaining: 120, elapsed: 30, nodes_done: 2, nodes_total: 8, nodes_cached: 1, fraction: 0.25,
                      confidence: "estimated", cold: false, theme: "midnight", schema: 1 };

    // 1) it renders (this fails if getElementById is detached) and takes the server's theme
    let page = makePage(running); await page.tick();
    assert.strictEqual(page.text("state"), "running");
    assert.strictEqual(page.text("main"), "≈ 2m 0s");
    assert.strictEqual(page.text("nodes"), "2 / 8 nodes · 1 cached");
    assert.strictEqual(page.text("label"), "Estimated remaining time");
    assert.strictEqual(page.text("cold"), "Warm start");
    assert.strictEqual(page.text("conf"), "Estimated");
    assert.strictEqual(page.element("fill").style.width, "25%");
    assert.strictEqual(page.sockets[0].url, "ws://127.0.0.1:8188/ws");
    assert.deepStrictEqual(page.urls, ["/workflow/eta/status?compact=1"], "the page never reads the inspector data, so it asks for the compact status");
    for (const key of ["bg", "title", "text", "muted", "strong", "accent", "track"]) assert.strictEqual(page.vars["--" + key], themes.midnight[key], key);

    // 2) a live theme event restyles and repaints, and the new theme is NOT reverted by the older cached status
    page.send("workflow.eta.theme", { theme: "paper" });
    for (const key of ["bg", "title", "strong"]) assert.strictEqual(page.vars["--" + key], themes.paper[key], key);
    assert.strictEqual(page.element("conf").style.color, "#8a6d00", "readable confidence colour on the light theme");
    page.send("workflow.eta", { state: "running", remaining: 75, elapsed: 55, nodes_done: 4, nodes_total: 8, fraction: 0.5, confidence: "estimated" });
    assert.strictEqual(page.text("main"), "≈ 1m 15s");
    assert.strictEqual(page.vars["--bg"], themes.paper.bg, "a pushed status without a theme keeps the current theme");

    // 3) unknown themes are ignored; dark themes use the normal confidence colours
    page.send("workflow.eta.theme", { theme: "nope" }); page.send("workflow.eta.theme", {}); page.send("workflow.eta.theme", { theme: null });
    assert.strictEqual(page.vars["--bg"], themes.paper.bg);
    page.send("workflow.eta.theme", { theme: "ember" });
    assert.strictEqual(page.vars["--bg"], themes.ember.bg);
    assert.strictEqual(page.element("conf").style.color, "#ffd43b");

    // 4) a fresh status carrying a theme takes effect; done and idle states render
    page.send("workflow.eta", { state: "done", elapsed: 95, theme: "graphite", nodes_done: 8, nodes_total: 8, fraction: 1, confidence: "exact", tiers: { exact: 3 } });
    assert.strictEqual(page.vars["--bg"], themes.graphite.bg);
    assert.strictEqual(page.text("label"), "Total duration");
    assert.strictEqual(page.text("main"), "1m 35s");
    page.send("workflow.eta", { state: "idle" });
    assert.strictEqual(page.text("main"), "—");

    // 5) a failing status request does not stop the page from listening for pushes
    page = makePage(new Error("offline")); await page.tick();
    assert.strictEqual(page.sockets.length, 1);
    page.send("workflow.eta", { state: "running", remaining: 10, elapsed: 5, fraction: 0.3, confidence: "estimated", theme: "forest" });
    assert.strictEqual(page.text("main"), "≈ 10s");
    assert.strictEqual(page.vars["--bg"], themes.forest.bg);

    console.log("PASS: browser panel page renders (strict getElementById), follows the shared theme live, ignores unknown themes, survives a failed status request");
})().catch((error) => { console.error(error); process.exit(1); });
