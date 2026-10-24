// End-to-end test of static/sync.js against the real account storage code. Run: node tests/sync/run.js
const { spawn } = require("child_process"), vm = require("vm"), fs = require("fs"), path = require("path"), assert = require("assert");
const PORT = 8700 + Math.floor(Math.random() * 200), BASE = "http://127.0.0.1:" + PORT;
const SRC = fs.readFileSync(path.join(__dirname, "../../static/sync.js"), "utf8");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const T = "option-intelligence-paper-trades-v1", CAP = "option-intelligence-paper-capital-v1", MODE = "oi-signal-mode";

function device(user, store) {
  // a fresh "browser": own localStorage, own copy of sync.js
  const mem = new Map(Object.entries(store || {}));
  class Storage { getItem(k) { return mem.has(k) ? mem.get(k) : null; } setItem(k, v) { mem.set(k, String(v)); } removeItem(k) { mem.delete(k); } }
  const localStorage = new Storage();
  const loaded = [], redirects = [], elements = [];
  const doc = { currentScript: { dataset: { next: "/static/app.js" } }, readyState: "complete", body: { appendChild: s => loaded.push(s.src) },
    querySelector: () => null, querySelectorAll: () => [], addEventListener() { }, createElement: () => ({ style: {}, classList: { add() { } }, append() { }, appendChild() { } }), visibilityState: "visible" };
  const fetchWrap = (url, init = {}) => fetch(BASE + url, { ...init, headers: { ...(init.headers || {}), "X-Test-User": user || "" } });
  const win = { fetch: fetchWrap, addEventListener() { }, location: { pathname: "/", search: "", replace: u => redirects.push(u) } };
  const ctx = vm.createContext({ window: win, document: doc, localStorage, Storage, location: win.location, fetch: fetchWrap, setTimeout, clearTimeout, setInterval: () => 0, AbortController, JSON, Object, Map, Date, Math, Promise, Array, String, encodeURIComponent, console, Error, confirm: () => true });
  win.Storage = Storage; win.localStorage = localStorage;
  vm.runInContext(SRC, ctx);
  return { mem, loaded, redirects, ls: localStorage, sync: win.OISync, ready: async () => { for (let i = 0; i < 100 && !loaded.length && !redirects.length; i++) await sleep(20); } };
}
const trade = (id, extra) => ({ id, status: "OPEN", qty: 75, sl: 100, opened_at: "2026-10-23T09:" + id.padStart(2, "0"), ...extra });
const get = async (user) => (await (await fetch(BASE + "/api/userdata", { headers: { "X-Test-User": user } })).json()).data;

(async () => {
  const srv = spawn("python3", [path.join(__dirname, "server.py"), String(PORT)], { stdio: "inherit" });
  try {
    for (let i = 0; i < 50; i++) { try { await fetch(BASE + "/__calls"); break; } catch { await sleep(100); } }

    // 1. first sign-in on a browser that already has local (pre-account) data: it is uploaded, then the page script loads
    let a = device("alice", { [T]: JSON.stringify([trade("1")]), [CAP]: "150000", [MODE]: "scalp" });
    await a.ready(); assert.deepStrictEqual(a.loaded, ["/static/app.js"]);
    await sleep(900);
    let d = await get("alice");
    assert.strictEqual(d["paper-trades"].value.length, 1); assert.strictEqual(d["paper-capital"].value, 150000); assert.strictEqual(d["pref-signal-mode"].value, "scalp");
    console.log("ok 1 existing local data migrates to the account");

    // 2. a second device starts empty and receives everything before app.js loads
    let b = device("alice"); await b.ready();
    assert.strictEqual(JSON.parse(b.ls.getItem(T)).length, 1); assert.strictEqual(b.ls.getItem(CAP), "150000"); assert.strictEqual(b.ls.getItem(MODE), "scalp");
    console.log("ok 2 new device gets the saved data before the app starts");

    // 3. a change on device A is auto-saved (debounced) and arrives on the server without any button
    a.ls.setItem(T, JSON.stringify([trade("1"), trade("2")]));
    assert.ok(a.sync.status().dirty.includes("paper-trades"));
    await sleep(2200);
    assert.deepStrictEqual(a.sync.status().dirty, []);
    assert.strictEqual((await get("alice"))["paper-trades"].value.length, 2);
    console.log("ok 3 changes are saved automatically");

    // 4. same value written again is not a change; quote-only change is throttled (not saved immediately)
    const before = (await (await fetch(BASE + "/__calls")).json()).length;
    a.ls.setItem(T, a.ls.getItem(T)); a.ls.setItem(CAP, "150000");
    await sleep(1800);
    assert.strictEqual((await (await fetch(BASE + "/__calls")).json()).length, before);
    a.ls.setItem(T, JSON.stringify([trade("1", { last_price: 111 }), trade("2", { last_price: 99 })]));   // price-only change
    await sleep(2200);
    assert.ok(a.sync.status().dirty.includes("paper-trades"), "price-only update should wait for the slow timer");
    console.log("ok 4 no-op writes ignored and price-only updates throttled");

    // 5. two devices edit different trades while both are open: nothing is lost (merged by trade id)
    b = device("alice"); await b.ready();
    await a.sync.flush();
    a.ls.setItem(T, JSON.stringify([trade("1", { status: "CLOSED", exit_time: "t", exit_price: 130 }), trade("2")]));
    b.ls.setItem(T, JSON.stringify([trade("1"), trade("2"), trade("3")]));
    await sleep(2200);
    await a.sync.flush(); await b.sync.flush(); await sleep(300);
    const merged = (await get("alice"))["paper-trades"].value;
    assert.deepStrictEqual(merged.map(t => t.id).sort(), ["1", "2", "3"]);
    assert.strictEqual(merged.find(t => t.id === "1").status, "CLOSED", "closed copy must beat the open copy");
    console.log("ok 5 concurrent edits on two devices are merged without loss");

    // 6. server outage: changes stay local, status says so, and they are saved once the server returns
    await fetch(BASE + "/__down", { method: "POST" });
    a.ls.setItem(CAP, "200000");
    await sleep(2200);
    assert.strictEqual(a.sync.status().offline, true); assert.ok(a.sync.status().dirty.includes("paper-capital"));
    await fetch(BASE + "/__up", { method: "POST" });
    for (let i = 0; i < 40 && a.sync.status().dirty.length; i++) { await sleep(500); }      // retry back-off starts at 10 s
    console.log("   (retry pending:", a.sync.status().dirty.join(",") || "none", ")");
    await a.sync.flush(); await sleep(300);
    assert.strictEqual((await get("alice"))["paper-capital"].value, 200000);
    console.log("ok 6 outage: unsaved changes are kept and uploaded after recovery");

    // 7. a different account on the same browser never sees (or uploads) the previous account's data
    const mem = Object.fromEntries(a.mem);
    let c = device("bob", mem); await c.ready();
    assert.strictEqual(c.ls.getItem(T), null, "alice's trades must not be shown to bob"); assert.strictEqual(c.ls.getItem(CAP), null);
    await sleep(500);
    assert.deepStrictEqual(Object.keys(await get("bob")), []);
    console.log("ok 7 accounts are isolated on a shared browser");

    // 8. signed-out session sends the page to the login screen and does not start the app
    let z = device(""); await z.ready();
    assert.deepStrictEqual(z.loaded, []); assert.ok(z.redirects[0].startsWith("/login?next="));
    console.log("ok 8 signed-out visitors are redirected to /login");

    // 9. removing a key locally (e.g. reset) removes it on the server
    a.ls.removeItem(MODE); await sleep(2200);
    assert.ok(!("pref-signal-mode" in await get("alice")));
    console.log("ok 9 deletions sync too");
    console.log("all sync tests passed");
  } catch (e) { console.error(e); process.exitCode = 1; }
  finally { srv.kill(); }
})();
