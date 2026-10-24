(function () {
  const $ = id => document.getElementById(id);
  let mode = "login", cfg = null;
  const params = new URLSearchParams(location.search);
  const nextUrl = (() => { const n = params.get("next") || "/"; return n.startsWith("/") && !n.startsWith("//") && !n.includes("\\") ? n : "/"; })();

  function show(el, on) { el.hidden = !on; }
  function setError(msg) { const e = $("authError"); e.textContent = msg || ""; show(e, !!msg); }
  function setMode(m) {
    mode = m;
    $("tabLogin").classList.toggle("active", m === "login"); $("tabSignup").classList.toggle("active", m === "signup");
    $("authSubmit").textContent = m === "login" ? "Log in" : "Create account";
    $("password").autocomplete = m === "login" ? "current-password" : "new-password";
    show($("confirmRow"), m === "signup");
    show($("codeRow"), m === "signup" && !!(cfg && cfg.signup && cfg.signup.needs_code));
    $("authHint").textContent = m === "signup" ? "Username: 3–32 letters, numbers, . - _   ·   Password: at least 8 characters. There is no email, so keep your password safe — only the owner can reset it." : "";
    setError("");
    const s = cfg && cfg.signup;
    if (m === "signup" && s && !s.open) setError(s.closed_reason || "Sign-ups are closed.");
  }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "same-origin" });
    let data = {}; try { data = await res.json(); } catch { }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")");
    return data;
  }
  async function init() {
    try {
      const res = await fetch("/api/auth/config", { credentials: "same-origin" });
      cfg = await res.json();
      if (!res.ok) throw new Error(cfg.detail || "Server error");
    } catch (e) {
      const n = $("authNote"); n.textContent = "Cannot reach the account service: " + e.message; n.className = "notice warning"; show(n, true); return;
    }
    if (cfg.user || cfg.auth_disabled) { location.replace(nextUrl); return; }
    const notes = [];
    if (cfg.storage && cfg.storage.warning) notes.push(cfg.storage.warning);
    if (cfg.signup && cfg.signup.first_user && !cfg.signup.needs_code) notes.push("First-time setup: the first account you create becomes the owner. Create it now, before anyone else finds this address.");
    if (notes.length) { const n = $("authNote"); n.textContent = notes.join(" "); n.className = "notice warning"; show(n, true); }
    setMode(cfg.signup && cfg.signup.first_user ? "signup" : "login");
  }
  $("tabLogin").onclick = () => setMode("login");
  $("tabSignup").onclick = () => setMode("signup");
  $("authForm").addEventListener("submit", async ev => {
    ev.preventDefault(); setError("");
    const username = $("username").value.trim(), password = $("password").value;
    if (!username || !password) { setError("Enter a username and password."); return; }
    if (mode === "signup" && password !== $("confirm").value) { setError("The two passwords do not match."); return; }
    const btn = $("authSubmit"); btn.disabled = true; const label = btn.textContent; btn.textContent = "Please wait…";
    try {
      await post(mode === "login" ? "/api/auth/login" : "/api/auth/signup", mode === "login" ? { username, password } : { username, password, code: $("code").value.trim() });
      location.replace(nextUrl);
    } catch (e) { setError(e.message); btn.disabled = false; btn.textContent = label; }
  });
  init();
})();
