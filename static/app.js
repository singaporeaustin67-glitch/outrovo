let lastQuery = "";

function focusSearch(e) {
  e.preventDefault();
  window.scrollTo({ top: 0, behavior: "smooth" });
  setTimeout(() => $("query").focus(), 500);
}


$("searchBtn").addEventListener("click", runSearch);
$("query").addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
$("refineInput").addEventListener("keydown", e => { if (e.key === "Enter") runRefine(); });

// ── conversational search messages ───────────────────────────
function _msgRow(kind, html) {
  const row = document.createElement("div");
  row.className = "msg " + kind;
  const mark = kind === "user" ? (currentUser?.email?.[0] || "Y").toUpperCase() : "✦";
  row.innerHTML = `<div class="av">${mark}</div><div class="bubble"></div>`;
  const b = row.querySelector(".bubble");
  if (typeof html === "string") { const d = document.createElement("div"); d.textContent = html; b.appendChild(d); }
  else b.appendChild(html);
  $("trace").appendChild(row);
  return row;
}
function chatUser(text) { return _msgRow("user", text); }
function chatAgent(text) { return _msgRow("agent", text); }

function _el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }

// Simple live progress bubble ("Scanning live sources… 12 matches found")
function chatProgress(label) {
  const card = _el(`<div><div class="b-title"><span class="p-label"></span><span class="dots"></span> — <span class="p-found">0 matches found</span></div><div class="src-chips"></div></div>`);
  card.querySelector(".p-label").textContent = label;
  const row = _msgRow("agent", card);
  let found = 0;
  return {
    row,
    setText(t) { card.querySelector(".p-label").textContent = t; },
    addSource(name, count) {
      found += count;
      card.querySelector(".p-found").textContent = `${found.toLocaleString()} matches found`;
      const chip = document.createElement("span");
      chip.className = "src-chip";
      chip.textContent = `✓ ${name} · ${count}`;
      card.querySelector(".src-chips").appendChild(chip);
    },
    finish(text) {
      card.querySelector(".dots")?.remove();
      card.querySelector(".b-title").textContent = text;
    },
  };
}

function _pillRow(items, max = 2) {
  const frag = document.createDocumentFragment();
  items.slice(0, max).forEach((t, i) => {
    const extra = items.length > max && i === max - 1 ? `<span class="n">+${items.length - max + 1}</span>` : "";
    const p = _el(`<span class="pill"><span></span>${extra}</span>`);
    p.querySelector("span").textContent = t;
    frag.appendChild(p);
  });
  return frag;
}

// "I've set these filters based on what you're looking for" card
function chatFilters(plan) {
  const card = _el(`<div>
    <div class="b-title">I've set these <b>filters</b> based on what you're looking for (<span class="mtotal">…</span> matches scanned)</div>
    <div class="pills"></div>
    <div class="dims"></div>
  </div>`);
  const pills = card.querySelector(".pills");
  const kws = (plan.role_keywords || []).filter(Boolean);
  pills.appendChild(_pillRow(kws.length ? kws : [plan.intent_summary || "people"]));
  const loc = plan.location || plan.country;
  if (loc) {
    pills.appendChild(_el(`<span class="pill-join">in the</span>`));
    pills.appendChild(_pillRow([loc]));
  }
  const edit = _el(`<button class="edit-link">Edit filters</button>`);
  edit.addEventListener("click", () => { $("refineBar").style.display = "flex"; $("refineInput").focus(); });
  pills.appendChild(edit);

  const dims = [
    ["Location", !!(plan.location || plan.country)],
    ["Job Title", !!(plan.occupations || []).length],
    ["Years of Experience", false],
    ["Industry", !!kws.length],
    ["Skills", !!(plan.hn_terms || []).length],
  ];
  const dimBox = card.querySelector(".dims");
  dims.forEach(([label, on]) => {
    dimBox.appendChild(_el(`<span class="dim ${on ? "on" : ""}"><span class="ck">${on ? "✓" : "○"}</span>${label}</span>`));
  });
  const row = _msgRow("agent", card);
  return { setTotal: n => card.querySelector(".mtotal").textContent = n.toLocaleString() };
}

// "Added these criteria to rank your matches" card
function chatCriteria(plan) {
  const crit = [...new Set([...(plan.occupations || []), ...(plan.hn_terms || [])])].filter(Boolean);
  if (!crit.length) return null;
  const card = _el(`<div>
    <div class="b-title">Added these <b>✦ Criteria</b> to rank your matches</div>
    <div class="pills"></div>
  </div>`);
  const pills = card.querySelector(".pills");
  pills.appendChild(_pillRow(crit));
  const edit = _el(`<button class="edit-link">Edit criteria</button>`);
  edit.addEventListener("click", () => { $("refineBar").style.display = "flex"; $("refineInput").focus(); });
  pills.appendChild(edit);
  _msgRow("agent", card);
}

function scoreClass(s) { return s >= 70 ? "hi" : s >= 40 ? "mid" : "lo"; }

const SOCIAL_ICONS = {
  linkedin: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.47-.9 1.63-1.85 3.36-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56v11.45z"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.9 1.15h3.68l-8.04 9.19L24 22.85h-7.41l-5.8-7.58-6.64 7.58H.47l8.6-9.83L0 1.15h7.59l5.24 6.93 6.07-6.93zm-1.29 19.5h2.04L6.49 3.24H4.3l13.31 17.41z"/></svg>`,
  instagram: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.64.07-4.85.07s-3.58-.01-4.85-.07c-3.26-.15-4.77-1.7-4.92-4.92C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85C2.38 3.92 3.9 2.38 7.15 2.23 8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.69.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95C23.73 2.7 21.31.27 16.95.07 15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1 0 18.16 12 6.16 6.16 0 0 0 12 5.84zM12 16a4 4 0 1 1 4-4 4 4 0 0 1-4 4zm6.41-11.85a1.44 1.44 0 1 0 1.44 1.44 1.44 1.44 0 0 0-1.44-1.44z"/></svg>`,
  youtube: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.5 3.55 12 3.55 12 3.55s-7.5 0-9.38.5A3.02 3.02 0 0 0 .5 6.19 31.6 31.6 0 0 0 0 12a31.6 31.6 0 0 0 .5 5.81 3.02 3.02 0 0 0 2.12 2.14c1.88.5 9.38.5 9.38.5s7.5 0 9.38-.5a3.02 3.02 0 0 0 2.12-2.14A31.6 31.6 0 0 0 24 12a31.6 31.6 0 0 0-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>`,
  tiktok: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.53.02C13.84 0 15.14.01 16.44 0c.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>`,
  github: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.6-1.4-1.4-1.8-1.4-1.8-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.7-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.2.5-2.3 1.2-3.1-.1-.3-.5-1.6.1-3.2 0 0 1-.3 3.4 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.6 1.6.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.1 0 4.7-2.8 5.7-5.5 6 .4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3z"/></svg>`,
  hackernews: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M0 0v24h24V0H0zm13.1 13.3V19h-2.2v-5.7L6.4 5h2.5l2.4 4.4c.6 1.2 1.1 2.3 1.5 3.1h.1c.4-.9.9-2 1.5-3.1L16.7 5h2.5l-4.6 8.3z"/></svg>`,
  wikipedia: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.09 13.12c-.95 4.56-2.51 8.16-3.1 8.96-.11.15-.26.22-.41.22-.14 0-.29-.07-.4-.22-.67-.95-2.26-4.7-3.28-9.26l2.1-.34c.05-.01.1-.02.13-.07.03-.05.04-.11.02-.17l-.21-.67c-.02-.06-.08-.1-.14-.1l-3.61.58c-.06.01-.12-.02-.15-.07-.03-.05-.03-.11-.01-.17l.36-.68c.03-.06.09-.09.15-.08l3.61-.6c.06-.01.11-.04.13-.09.02-.05.02-.11-.01-.16l-.34-.68c-.03-.06-.09-.09-.15-.08l-3.41.58c-.4-1.26-.6-2.5-.6-3.66C2.6 3.42 4.42 1.6 6.9 1.6c.97 0 1.87.29 2.62.79.14-.03.29-.05.44-.05.15 0 .29.02.43.05.75-.5 1.65-.79 2.62-.79 2.48 0 4.3 1.82 4.3 4.3 0 1.16-.2 2.4-.6 3.66l-3.41-.58c-.06-.01-.12.02-.15.08l-.34.68c-.03.05-.03.11-.01.16.02.05.07.08.13.09l3.61.6c.06.01.12.02.15.08l.36.68c.02.06.02.12-.01.17-.03.05-.09.08-.15.07l-3.61-.58c-.06-.01-.12.04-.14.1l-.21.67c-.02.06-.01.12.02.17.03.05.08.06.13.07l2.1.34c-1.02 4.56-2.61 8.31-3.28 9.26-.11.15-.26.22-.4.22-.15 0-.3-.07-.41-.22-.59-.8-2.15-4.4-3.1-8.96zm8.68-7.21c.42 1.32.63 2.63.63 3.89 0 2.15-.59 4.28-1.71 6.13l-2.02-4.9 3.1-.51c.06-.01.11-.04.13-.09.02-.05.02-.11-.01-.16l-.34-.68c-.03-.06-.09-.09-.15-.08l-3.13.52-.85-2.07c.75-.11 1.55-.17 2.4-.17.7 0 1.35.04 1.95.12zM3.2 5.91c.6-.08 1.25-.12 1.95-.12.85 0 1.65.06 2.4.17l-.85 2.07-3.13-.52c-.06-.01-.12.02-.15.08l-.34.68c-.03.05-.03.11-.01.16.02.05.07.08.13.09l3.1.51-2.02 4.9C3.19 14.08 2.6 11.95 2.6 9.8c0-1.26.21-2.57.6-3.89z"/></svg>`,
  wikidata: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M0 3.6h2.4v16.8H0V3.6zm4.8 0h2.4v16.8H4.8V3.6zm4.8 0h2.4v16.8H9.6V3.6zm4.82 0h2.4v16.8h-2.4V3.6zm4.79 0h2.4v16.8h-2.4V3.6z" opacity=".9"/></svg>`,
  website: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  opencorporates: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6M9 11h.01M15 11h.01M9 15h.01M15 15h.01"/></svg>`,
  websearch: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a15.3 15.3 0 0 1 0 18 15.3 15.3 0 0 1 0-18"/></svg>`,
  mastodon: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23.27 7.34c0-4.48-2.94-5.8-2.94-5.8C18.85.55 15.48.16 12.31.15h-.08c-3.17.01-6.54.4-8.02 1.39 0 0-2.94 1.32-2.94 5.8 0 1.04-.02 2.29.01 3.62.17 6.9 1.31 13.7 7.9 14.11 3.04.19 5.65-.74 5.65-.74l-.12-2.63s-2.17.69-4.61.6c-2.42-.08-4.97-.25-5.36-3.14a5.87 5.87 0 0 1-.05-.43s2.38.58 5.39.72c1.84.08 3.57-.1 5.32-.29 3.36-.37 6.29-2.28 6.65-4.03.58-2.8.53-6.83.53-6.83zm-3.92 8.52h-2.57V8.66c0-1.33-.56-2-1.67-2-1.23 0-1.85.82-1.85 2.43v3.44h-2.55V9.09c0-1.61-.62-2.43-1.85-2.43-1.11 0-1.67.67-1.67 2v7.2H3.61V8.53c0-1.33.34-2.38 1.02-3.16.7-.77 1.62-1.17 2.74-1.17 1.31 0 2.3.5 2.96 1.5l.64 1.08.64-1.08c.66-1 1.65-1.5 2.96-1.5 1.12 0 2.04.4 2.74 1.17.68.78 1.02 1.83 1.02 3.16l.02 7.33z"/></svg>`,
  devto: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7.83 10.67c0-.6-.28-.88-.84-.88h-.61v2.55h.61c.56 0 .84-.28.84-.88v-.79zM20.73 0H3.27A3.27 3.27 0 0 0 0 3.27v17.46A3.27 3.27 0 0 0 3.27 24h17.46A3.27 3.27 0 0 0 24 20.73V3.27A3.27 3.27 0 0 0 20.73 0zM8.7 12.22c0 1.44-.98 2.19-2.4 2.19H4.5V7.63h1.83c1.4 0 2.37.76 2.37 2.19v2.4zm4.63-2.96H10.7v1.4h1.43v1.59H10.7v1.44h2.63v1.6h-3.9c-.34 0-.62-.28-.62-.61V8.37c0-.34.28-.61.62-.61h3.9v1.5zm5.33 5.23c-.81 1.11-2.28 1.2-3.17.2-.41-.46-.65-1.05-1.44-2.95l-1.62-4.11h1.83l1.26 3.18 1.24-3.18h1.83l-1.93 6.86z"/></svg>`,
  producthunt: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.6 8.4h-3.4V4.8h3.4a1.8 1.8 0 0 1 0 3.6zm0 6h-3.4v4.8H6.8V0h6.8a5.2 5.2 0 0 1 0 10.4z"/></svg>`
};

let lastResults = null;

function fmtK(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

function followersOf(c) {
  const s = c.stats || {};
  return s.social_followers ?? s.followers ?? s.karma ?? null;
}

function renderHistory(items) {
  if (!items?.length) return;
  const div = document.createElement("div");
  div.id = "history";
  div.innerHTML = "<h3>Recent searches</h3>" +
    items.map(h =>
      `<button class="history-item" onclick="rerunQuery(this)">${h.query.replace(/</g, "&lt;")}</button>`
    ).join(" ");
  const anchor = document.querySelector(".stats") ||
    document.querySelector("#results");
  anchor.parentElement.insertBefore(div, anchor);
}
async function loadHistory() {
  try {
    const r = await api("/api/history");
    const d = await r.json();
    renderHistory(d.history);
  } catch (_) { /* history is best-effort */ }
}


function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function loadOutreachLog() {
  try {
    const r = await api("/api/outreach/log");
    const panel = $("outreachPanel");
    if (r.status === 401) { panel.style.display = "none"; return; }
    const d = await r.json();
    const msgs = d.messages || [];
    if (!msgs.length) { panel.style.display = "none"; return; }
    $("outreachRows").innerHTML = msgs.map(m => {
      const badge = m.opened_at
        ? `<span class="open-badge yes">Opened ✓${m.opens > 1 ? ` · ${m.opens}×` : ""}</span>`
        : `<span class="open-badge no">Not opened yet</span>`;
      const openedInfo = m.opened_at ? `first opened ${fmtDate(m.opened_at)}` : "";
      return `<div class="ol-row">
        <span class="ol-to">${esc(m.to)}</span>
        <span class="ol-subj">${esc(m.subject)}</span>
        <span class="ol-date">sent ${fmtDate(m.sent_at)}${openedInfo ? " · " + openedInfo : ""}</span>
        ${badge}
      </div>`;
    }).join("");
    panel.style.display = "block";
  } catch (_) { /* outreach log is best-effort */ }
}
function rerunQuery(btn) {
  const q = btn.textContent;
  const input = document.querySelector("input");
  input.value = q;
  input.dispatchEvent(new Event("input"));
  input.focus();
  document.getElementById("searchBtn")?.click();
}
async function refreshQuota() {
  if (!authToken) return;
  try {
    const r = await api("/api/auth/me");
    if (r.ok) { currentUser = (await r.json()).user; renderAccount(); }
  } catch (_) { /* best-effort */ }
}
restoreSession().then(() => { loadHistory(); loadOutreachLog(); });
if (new URLSearchParams(location.search).get("upgraded")) {
  setTimeout(() => { refreshQuota(); alert("Welcome to Outrovo Pro — your limits are lifted."); }, 600);
  history.replaceState(null, "", location.pathname);
}

function exportCsv() {
  if (!lastResults?.results?.length) return;
  const head = ["name","fit_score","role","company","headline","location","source","profile_url","followers"];
  const csv = [head.join(",")]
    .concat(lastResults.results.map(r => head.map(k =>
      `"${String(k === "followers" ? followersOf(r) ?? "" : (r[k] ?? "")).replace(/"/g, '""')}"`).join(",")))
    .join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\uFEFF" + csv], { type: "text/csv" }));
  a.download = `outrovo-${String(lastResults.query || "search").replace(/\W+/g, "-").slice(0, 40)}.csv`;
  a.click();
}

function flagEmoji(cc) {
  if (!cc || cc.length !== 2) return "";
  return String.fromCodePoint(...[...cc.toUpperCase()].map(ch => 0x1F1E6 + ch.charCodeAt(0) - 65));
}

function personRows(c, i) {
  const score = c.fit_score || 0;
  const initials = c.name.split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const socials = Object.entries(c.platforms || {})
    .map(([k, u]) => `<a class="s-${k}" href="${u}" target="_blank" rel="noopener" title="${k}">${SOCIAL_ICONS[k] || SOCIAL_ICONS.website}</a>`)
    .join("");

  const tr = document.createElement("tr");
  tr.className = "person-row";
  tr.style.animationDelay = (i * 0.05) + "s";
  tr.innerHTML = `
    <td><div class="cell-person">
      <div class="avatar">${c.avatar_url ? `<img src="${c.avatar_url}" loading="lazy" onerror="this.remove()">` : initials}</div>
      <div><div class="cell-name"></div><div class="cell-sub"></div></div>
    </div></td>
    <td><span class="match ${scoreClass(score)}"><span class="mdot"></span>${score.toFixed(1)}%</span></td>
    <td><div class="socials">${socials}</div></td>
    <td><div class="cell-company">${c.company_logo ? `<img class="company-logo" src="${c.company_logo}" loading="lazy" onerror="this.remove()">` : ""}<span class="company-name"></span></div></td>
    <td class="cell-role"></td>
    <td><button class="check-btn">✉ Check</button></td>
    <td class="cell-country"></td>
    <td class="num-cell">${fmtK(followersOf(c))}</td>
    <td class="num-cell">${fmtK((c.stats || {}).avg_views)}</td>`;
  tr.querySelector(".cell-name").textContent = c.name;
  tr.querySelector(".cell-sub").textContent = "via " + c.source;
  tr.querySelector(".company-name").textContent = c.company || "—";
  const headline = (c.headline || "").trim();
  const cleanHeadline = /https?:\/\/|@/.test(headline) ? "" : headline.slice(0, 60);
  tr.querySelector(".cell-role").textContent = c.role || cleanHeadline || "—";
  tr.querySelector(".cell-country").innerHTML = c.country_code
    ? `<span class="flag">${flagEmoji(c.country_code)}</span>${c.country_code}`
    : (c.location ? `<span class="flag">◈</span>${c.location.slice(0, 14)}` : "—");

  const detail = document.createElement("tr");
  detail.className = "detail-row";
  detail.innerHTML = `<td colspan="9"><div class="detail-box">
    <div class="fb-row">Good match?
      <button class="fb-btn" data-vote="1">👍</button>
      <button class="fb-btn" data-vote="-1">👎</button>
      <span class="fb-done"></span>
    </div>
    ${c.fit_reason ? `<div class="fit"></div>` : ""}
    ${(c.highlights || []).length ? `<ul class="highlights">${c.highlights.map(() => "<li></li>").join("")}</ul>` : ""}
    <div class="email-box"><div class="eb-title">✉ Public email discovery</div><div class="email-list"></div></div>
    <div class="row-actions">
      <button class="save-btn">＋ Save to list</button>
      <button class="outreach-btn">✦ Draft outreach message</button>
    </div>
    <div class="outreach-text"></div>
    <div class="send-row">
      <input class="send-to" type="email" placeholder="their-email@example.com">
      <input class="send-subj" type="text" placeholder="Subject">
      <button class="send-btn">Send →</button>
      <span class="send-status"></span>
    </div>
  </div></td>`;
  if (c.fit_reason) detail.querySelector(".fit").textContent = c.fit_reason;
  detail.querySelectorAll(".highlights li").forEach((li, j) => li.textContent = c.highlights[j]);

  let emailsLoaded = false;
  async function loadEmails() {
    if (emailsLoaded) return;
    emailsLoaded = true;
    const list = detail.querySelector(".email-list");
    list.innerHTML = `<div class="email-loading">scanning public sources for published emails…</div>`;
    try {
      const r = await api("/api/emails", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate: c })
      });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) openAuth(null, "signup");
        throw new Error(d.detail || "failed");
      }
      if (!d.emails.length) {
        list.innerHTML = `<div class="email-none">No publicly published email found for this person.</div>`;
        return;
      }
      list.innerHTML = "";
      d.emails.forEach(e => {
        const item = document.createElement("div");
        item.className = "email-item";
        const badge = { valid: ['✓ verified', '#37b268'], risky: ['⚠ disposable', '#c2890a'], invalid: ['✕ undeliverable', '#c2452d'] }[e.status] || ['', '#888'];
        item.innerHTML = `<span class="addr"></span><span class="src"></span><span class="email-badge"></span><button class="copy">Copy</button>`;
        item.querySelector(".addr").textContent = e.address;
        item.querySelector(".src").textContent = "via " + e.source;
        const b = item.querySelector(".email-badge");
        b.textContent = badge[0];
        b.style.color = badge[1];
        b.title = e.reason || "";
        item.querySelector(".copy").addEventListener("click", (ev) => {
          ev.stopPropagation();
          navigator.clipboard.writeText(e.address);
          ev.target.textContent = "Copied ✓";
          setTimeout(() => ev.target.textContent = "Copy", 1500);
        });
        list.appendChild(item);
      });
    } catch (e) {
      list.innerHTML = `<div class="email-none">Email lookup failed — try again.</div>`;
      emailsLoaded = false;
    }
  }

  tr.querySelector(".check-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    detail.classList.toggle("open");
    if (detail.classList.contains("open")) loadEmails();
  });
  tr.addEventListener("click", () => {
    detail.classList.toggle("open");
    if (detail.classList.contains("open")) loadEmails();
  });

  const btn = detail.querySelector(".outreach-btn");
  const box = detail.querySelector(".outreach-text");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    btn.disabled = true; btn.textContent = "✦ Drafting…";
    try {
      const r = await api("/api/outreach", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: lastQuery, candidate: c })
      });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) openAuth(null, "signup");
        throw new Error(d.detail || "failed");
      }
      box.textContent = d.message;
      box.classList.add("on");
      btn.textContent = "✦ Regenerate";
    } catch (e) {
      btn.textContent = "✦ Failed — retry";
    } finally { btn.disabled = false; }
  });

  // Feedback loop — votes influence ranking of future similar searches
  detail.querySelectorAll(".fb-btn").forEach(b => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    const r = await api("/api/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: lastQuery, person_id: c.id, vote: Number(b.dataset.vote) })
    });
    if (r.status === 401) { openAuth(null, "signup"); return; }
    detail.querySelector(".fb-done").textContent = "thanks — noted for ranking";
  }));

  // Save this candidate to a named prospect list
  detail.querySelector(".save-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    if (!authToken) { openAuth(null, "signup"); return; }
    openListPicker(c, e.currentTarget);
  });

  // Send the drafted message via the configured SMTP account
  const sendBtn = detail.querySelector(".send-btn");
  sendBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const to = detail.querySelector(".send-to").value.trim();
    const subject = detail.querySelector(".send-subj").value.trim() || "Quick hello";
    const body = box.textContent.trim();
    const status = detail.querySelector(".send-status");
    if (!to || !body) { status.textContent = "draft a message and enter an email first"; return; }
    sendBtn.disabled = true; status.textContent = "sending…";
    try {
      const r = await api("/api/outreach/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate: c, to, subject, body })
      });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) openAuth(null, "signup");
        throw new Error(d.detail || "failed");
      }
      status.textContent = `sent ✓${d.tracked ? " · open tracking on" : ""} · follow-up reminder in ${d.followup_due_in_days}d`;
      loadOutreachLog();
      refreshQuota();
    } catch (err) {
      status.textContent = String(err.message).slice(0, 120);
    } finally { sendBtn.disabled = false; }
  });
  return [tr, detail];
}

function renderResults(d) {
  lastResults = d;
  $("exportBar").style.display = "block";
  $("resultsMeta").textContent =
    `${d.results.length} matches · ${d.total_candidates} scanned · ${d.elapsed_seconds}s`;
  if (!d.results.length) {
    $("empty").textContent = "No matching people found in live sources — try rephrasing your request.";
    $("empty").style.display = "block";
    $("tableWrap").style.display = "none";
    $("refineBar").style.display = "none";
  } else {
    $("refineBar").style.display = "flex";
    renderTable();
    if (lastResults.results.length > lastResults.results.filter(c => (c.fit_score || 0) > 0).length) {
      $("resultsMeta").textContent += ` · ${lastResults.results.length - lastResults.results.filter(c => (c.fit_score || 0) > 0).length} irrelevant hidden`;
    }
  }
}

async function runRefine() {
  if (!lastResults || !lastResults.results.length) return;
  const instruction = $("refineInput").value.trim();
  if (instruction.length < 2) return;
  const btn = $("refineBtn");
  btn.disabled = true;
  chatUser(instruction);
  const live = chatProgress("Refining your matches");
  try {
    const resp = await api("/api/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: lastQuery, instruction, candidates: lastResults.results }),
    });
    if (resp.status === 401) { openAuth(null, "signup"); throw new Error("sign in to refine results"); }
    if (!resp.ok) throw new Error("refine failed");
    const out = await resp.json();
    live.finish(out.reply);
    lastResults.results = out.results;
    if (!out.results.length) {
      $("tableWrap").style.display = "none";
      $("empty").textContent = "Nothing matches that refinement — try a different instruction.";
      $("empty").style.display = "block";
    } else {
      $("empty").style.display = "none";
      renderTable();
    }
    $("resultsMeta").textContent =
      `${out.results.length} matches · refined: "${instruction}"`;
    $("refineInput").value = "";
  } catch (e) {
    live.finish("Refinement failed — try again");
  } finally {
    btn.disabled = false;
    $("refineInput").focus();
  }
}

/* ---- sortable grid state ---- */
let sortState = { key: "fit_score", dir: -1 };

const SORT_GETTERS = {
  name: c => (c.name || "").toLowerCase(),
  fit_score: c => c.fit_score || 0,
  company: c => (c.company || "").toLowerCase(),
  role: c => (c.role || "").toLowerCase(),
  country_code: c => (c.country_code || "").toLowerCase(),
  followers: c => (c.stats || {}).social_followers || (c.stats || {}).followers || 0,
  avg_views: c => (c.stats || {}).avg_views || 0,
};

function renderTable() {
  const d = lastResults;
  $("rows").innerHTML = "";
  $("tableWrap").style.display = "block";
  let shown = d.results.filter(c => (c.fit_score || 0) > 0);
  if (!shown.length) shown = d.results.slice();
  if (sortState.key !== "fit_score") {
    const get = SORT_GETTERS[sortState.key];
    shown = shown.slice().sort((a, b) => {
      const va = get(a), vb = get(b);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * sortState.dir;
      return String(va).localeCompare(String(vb)) * sortState.dir;
    });
  }
  shown.forEach((c, i) => {
    const [tr, detail] = personRows(c, i);
    $("rows").appendChild(tr);
    $("rows").appendChild(detail);
  });
  document.querySelectorAll("table.results thead th[data-sort]").forEach(th => {
    const key = th.dataset.sort;
    const arrow = sortState.key === key ? (sortState.dir === -1 ? " ▼" : " ▲") : "";
    th.textContent = th.textContent.replace(/ [▼▲]$/, "") + arrow;
    th.style.color = sortState.key === key ? "var(--brand)" : "";
  });
}

document.querySelectorAll("table.results thead th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (sortState.key === key) sortState.dir *= -1;
    else sortState = { key, dir: key === "name" || key === "company" || key === "role" || key === "country_code" ? 1 : -1 };
    renderTable();
  });
});

function runSearch() {
  const q = $("query").value.trim();
  if (q.length < 3) return;
  lastQuery = q;
  $("searchBtn").disabled = true;
  $("err").style.display = "none";
  $("results").classList.add("on");
  $("trace").innerHTML = "";
  $("rows").innerHTML = "";
  $("tableWrap").style.display = "none";
  $("empty").style.display = "none";
  $("exportBar").style.display = "none";
  $("refineBar").style.display = "none";
  $("resultsMeta").textContent = "";
  chatUser(q);
  const progress = chatProgress("Planning your search");
  let filterCard = null;
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });

  const src = new EventSource("/api/search/stream?q=" + encodeURIComponent(q));
  src.addEventListener("status", e => {
    const d = JSON.parse(e.data);
    if (d.text && d.text.startsWith("AI reviewing")) progress.setText(`AI reviewing ${d.count ?? ""} profiles`);
    else progress.setText(d.text || "Working");
  });
  src.addEventListener("plan", e => {
    const p = JSON.parse(e.data);
    progress.setText("Scanning 100+ live sources");
    filterCard = chatFilters(p);
    chatCriteria(p);
    // Keep the scanning bubble last so the flow reads: ask → filters → criteria → scanning
    $("trace").appendChild(progress.row);
  });
  src.addEventListener("source", e => {
    const d = JSON.parse(e.data);
    progress.addSource(d.source, d.count);
  });
  src.addEventListener("done", e => {
    src.close();
    const d = JSON.parse(e.data);
    progress.finish(`Scanned ${d.total_candidates.toLocaleString()} real profiles in ${d.elapsed_seconds}s — AI ranked the best matches`);
    filterCard?.setTotal(d.total_candidates);
    renderResults(d);
    $("searchBtn").disabled = false;
  });
  src.onerror = () => {
    src.close();
    progress.finish("Search interrupted — please try again");
    $("err").textContent = "⚠ search stream interrupted — please try again";
    $("err").style.display = "block";
    $("searchBtn").disabled = false;
  };
}

// ── saved prospect lists ─────────────────────────
let _pickerCandidate = null;

async function openListPicker(candidate, btn) {
  _pickerCandidate = candidate;
  $("listPickerErr").textContent = "";
  $("newListName").value = "";
  const r = await api("/api/lists");
  const d = await r.json();
  if (!r.ok) { if (r.status === 401) openAuth(null, "signup"); return; }
  const items = $("listPickerItems");
  items.innerHTML = d.lists.length
    ? d.lists.map(l => `<button class="list-pick" data-id="${l.id}" style="text-align:left;padding:11px 14px;border:1px solid var(--line);border-radius:11px;background:#fff;cursor:pointer;font-size:14px;display:flex;justify-content:space-between"><span></span><span style="color:var(--muted);font-family:'JetBrains Mono';font-size:12px">${l.count} saved</span></button>`).join("")
    : `<div style="color:var(--muted);font-size:13.5px;padding:6px 2px">No lists yet — create your first one below.</div>`;
  items.querySelectorAll(".list-pick").forEach((b, i) => {
    b.querySelector("span").textContent = d.lists[i].name;
    b.addEventListener("click", () => saveToList(d.lists[i].id, btn));
  });
  $("listPicker").classList.add("on");
}
function closeListPicker() { $("listPicker").classList.remove("on"); _pickerCandidate = null; }

async function saveToList(listId, btn) {
  const r = await api(`/api/lists/${listId}/members`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person: _pickerCandidate })
  });
  const d = await r.json();
  if (r.ok && d.added) {
    closeListPicker();
    if (btn) { const t = btn.textContent; btn.textContent = "✓ Saved"; setTimeout(() => btn.textContent = t, 1800); }
  } else if (r.ok) {
    $("listPickerErr").textContent = "Already in that list.";
  } else {
    $("listPickerErr").textContent = d.detail || "Could not save.";
  }
}

async function createAndSave() {
  const name = $("newListName").value.trim();
  if (!name) { $("listPickerErr").textContent = "Name the list first."; return; }
  const r = await api("/api/lists", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  });
  const d = await r.json();
  if (!r.ok) { $("listPickerErr").textContent = d.detail || "Could not create list."; return; }
  await saveToList(d.id, null);
  closeListPicker();
}

async function openListsPanel(e) {
  if (e) e.preventDefault();
  if (!authToken) { openAuth(null, "signup"); return; }
  $("listsPanel").classList.add("on");
  await renderListsPanel();
}
function closeListsPanel() { $("listsPanel").classList.remove("on"); }

async function renderListsPanel() {
  const body = $("listsBody");
  body.innerHTML = `<div style="color:var(--muted);padding:12px">loading…</div>`;
  const r = await api("/api/lists");
  const d = await r.json();
  if (!r.ok) { body.innerHTML = `<div style="color:var(--muted)">Could not load lists.</div>`; return; }
  if (!d.lists.length) {
    body.innerHTML = `<div style="color:var(--muted);padding:14px 4px;font-size:14px">No lists yet. Run a search, open a person, and hit <b>＋ Save to list</b>.</div>`;
    return;
  }
  body.innerHTML = "";
  for (const l of d.lists) {
    const wrap = document.createElement("div");
    wrap.style.cssText = "border:1px solid var(--line);border-radius:13px;margin-bottom:10px;overflow:hidden";
    const head = document.createElement("div");
    head.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding:12px 15px;cursor:pointer;background:#fff";
    head.innerHTML = `<b style="font-size:14.5px"></b><span style="display:flex;gap:10px;align-items:center"><span style="font-family:'JetBrains Mono';font-size:12px;color:var(--muted)">${l.count} saved</span><button class="del-list" style="border:0;background:none;color:#c2452d;cursor:pointer;font-size:13px">Delete</button></span>`;
    head.querySelector("b").textContent = l.name;
    const membersBox = document.createElement("div");
    membersBox.style.cssText = "display:none;border-top:1px solid var(--line);background:var(--bg)";
    head.addEventListener("click", async (ev) => {
      if (ev.target.classList.contains("del-list")) return;
      const open = membersBox.style.display !== "none";
      membersBox.style.display = open ? "none" : "block";
      if (!open && !membersBox.dataset.loaded) {
        membersBox.dataset.loaded = "1";
        const mr = await api(`/api/lists/${l.id}`);
        const md = await mr.json();
        membersBox.innerHTML = (md.members || []).map(m =>
          `<div style="display:flex;justify-content:space-between;align-items:center;padding:9px 15px;border-top:1px solid var(--line)">
            <span style="font-size:13.5px"><b></b> <span style="color:var(--muted)"></span></span>
            <a target="_blank" rel="noopener" style="color:var(--brand);font-size:12.5px;text-decoration:none">view →</a>
          </div>`).join("") || `<div style="padding:12px;color:var(--muted);font-size:13px">Empty list.</div>`;
        membersBox.querySelectorAll(":scope > div").forEach((row, j) => {
          const m = md.members[j]; if (!m) return;
          row.querySelector("b").textContent = m.name || "(unnamed)";
          row.querySelectorAll("span")[1].textContent = m.role || m.company || "";
          const a = row.querySelector("a"); a.href = m.profile_url || "#";
        });
      }
    });
    head.querySelector(".del-list").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Delete list "${l.name}"? This cannot be undone.`)) return;
      await api(`/api/lists/${l.id}`, { method: "DELETE" });
      renderListsPanel();
    });
    wrap.appendChild(head);
    wrap.appendChild(membersBox);
    body.appendChild(wrap);
  }
}

/* ── app boot ─────────────────────────────── */
afterAuth = afterSession = () => { loadHistory(); loadOutreachLog(); };
afterLogout = () => { loadOutreachLog(); };

window.addEventListener("DOMContentLoaded", () => {
  const q = new URLSearchParams(location.search).get("q");
  if (q) {
    $("query").value = q;
    history.replaceState(null, "", "/app");
    runSearch();
  } else {
    setTimeout(() => $("query").focus(), 300);
  }
});
