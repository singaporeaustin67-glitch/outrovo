// Shared: auth, account, billing — used by landing (index.html) and app (app.html).
const $ = (id) => document.getElementById(id);

/* ── auth state ─────────────────────────────── */
let authToken = localStorage.getItem("outrovo_token") || "";
let currentUser = null;
let authMode = "login";
var afterAuth = null;    // page hook: explicit login/signup only
var afterSession = null; // page hook: restored session (page load)
var afterLogout = null;   // page hook: runs after logout

function authHeaders() {
  return authToken ? { "Authorization": "Bearer " + authToken } : {};
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}), ...authHeaders() };
  return fetch(path, { ...opts, headers });
}

function openAuth(e, mode) {
  if (e) e.preventDefault();
  authMode = mode || "login";
  renderAuthMode();
  $("authErr").textContent = "";
  $("authBackdrop").classList.add("on");
  setTimeout(() => $("authEmail").focus(), 50);
}

function closeAuth() { $("authBackdrop").classList.remove("on"); }

function renderAuthMode() {
  const login = authMode === "login";
  $("authTitle").textContent = login ? "Welcome back" : "Create your account";
  $("authSub").textContent = login
    ? "Log in to save your searches and run outreach."
    : "Free plan: 5 searches/day · 3 outreach emails/day. No card required.";
  $("authGo").textContent = login ? "Log in" : "Sign up free";
  $("authPass").autocomplete = login ? "current-password" : "new-password";
  $("authSwitch").innerHTML = login
    ? `No account yet? <a onclick="openAuth(null,'signup')">Sign up free</a>`
    : `Already have an account? <a onclick="openAuth(null,'login')">Log in</a>`;
}

async function submitAuth() {
  const email = $("authEmail").value.trim();
  const password = $("authPass").value;
  const err = $("authErr");
  err.textContent = "";
  if (!email || !password) { err.textContent = "Email and password required."; return; }
  $("authGo").disabled = true;
  try {
    const r = await fetch("/api/auth/" + authMode, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "failed");
    authToken = d.token;
    localStorage.setItem("outrovo_token", authToken);
    currentUser = d.user;
    closeAuth();
    renderAccount();
    afterAuth && afterAuth();
  } catch (e) {
    err.textContent = String(e.message).slice(0, 140);
  } finally { $("authGo").disabled = false; }
}

async function doLogout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  authToken = ""; currentUser = null;
  localStorage.removeItem("outrovo_token");
  renderAccount();
  afterLogout && afterLogout();
}

async function upgradeToPro() {
  try {
    const r = await api("/api/billing/checkout", { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "unavailable");
    window.location.href = d.checkout_url;
  } catch (e) {
    alert("Upgrade isn't available yet: " + e.message);
  }
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderAccount() {
  const area = $("acctArea");
  const listsLink = $("listsLink");
  const appLink = $("appLink");
  if (appLink) appLink.style.display = currentUser ? "" : "none";
  if (!currentUser) {
    area.innerHTML = `<a class="btn btn-primary" href="#" onclick="openAuth(event)">Sign in</a>`;
    if (listsLink) listsLink.style.display = "none";
    return;
  }
  if (listsLink) listsLink.style.display = "";
  const q = currentUser.quota.searches;
  const upgrade = currentUser.tier === "free"
    ? `<button class="btn-upgrade" onclick="upgradeToPro()">Upgrade</button>` : "";
  area.innerHTML = `<span class="acct">
    <span class="acct-email" title="${esc(currentUser.email)}">${esc(currentUser.email)}</span>
    <span class="tier-badge ${currentUser.tier}">${currentUser.tier}</span>
    <span class="quota-note">${q.used}/${q.limit} searches today</span>
    ${upgrade}
    <button onclick="doLogout()">Log out</button>
  </span>`;
}

async function restoreSession() {
  if (!authToken) { renderAccount(); return; }
  try {
    const r = await api("/api/auth/me");
    if (!r.ok) throw new Error();
    currentUser = (await r.json()).user;
  } catch (_) {
    authToken = ""; localStorage.removeItem("outrovo_token");
  }
  renderAccount();
  afterSession && currentUser && afterSession();
}
restoreSession();
