// viral · shell — core: module switcher, api() wrapper, auth, health poll.
// Per-module logic lives in <module>.js (profile.js, processor.js, script.js, ...).

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => root.querySelectorAll(s);

// ----------------------------------------------------------------
// Module config
// ----------------------------------------------------------------
const MODULES = {
  profile: {
    base: "http://localhost:8300",
    health: "/profile/healthz",
    tokenKinds: ["user", "admin"],
    headers: { user: "X-Token", admin: "X-Admin-Token" },
    // order matters: more specific prefixes first
    auth: [
      ["admin", ["/profile/seed"]],
      ["user",  ["/profile"]],
    ],
    parseHealth: (h) => `ok · ${h.status || "ok"}`,
  },
  monitor: {
    base: "http://localhost:8400",
    health: "/monitor/healthz",
    tokenKinds: [],
    headers: {},
    auth: [],
    parseHealth: (h) => `ok · ${h.status || "ok"}`,
  },
  downloader: {
    base: "http://localhost:8500",
    health: "/healthz",
    tokenKinds: [],
    headers: {},
    auth: [],
    parseHealth: (h) => `ok · ${h.status || "ok"}`,
  },
  processor: {
    base: "http://localhost:8100",
    health: "/healthz",
    tokenKinds: ["admin", "worker"],
    headers: { admin: "X-Admin-Token", worker: "X-Worker-Token" },
    auth: [
      ["admin",  ["/admin"]],
      ["worker", ["/jobs"]],
    ],
    parseHealth: (h) => {
      const tr = h.active_keys?.transcription ?? 0;
      const vi = h.active_keys?.vision ?? 0;
      return `ok · ffmpeg=${h.ffmpeg_available} · keys tr=${tr} vi=${vi} · queue=${h.queue_depth}`;
    },
  },
  script: {
    base: "http://localhost:8200",
    health: "/script/healthz",
    tokenKinds: ["admin", "worker"],
    headers: { admin: "X-Admin-Token", worker: "X-Worker-Token" },
    auth: [
      ["admin",  ["/script/admin"]],
      ["worker", ["/script"]],
    ],
    parseHealth: (h) =>
      `ok · provider=${h.default_provider || "?"} · fake=${h.fake_llm}`,
  },
};

const MODULE_ORDER = ["profile", "monitor", "downloader", "processor", "script"];

// ----------------------------------------------------------------
// LocalStorage helpers
// ----------------------------------------------------------------
const LS = {
  getBase(mod) {
    return localStorage.getItem(`vmpv-base-${mod}`) || MODULES[mod].base;
  },
  setBase(mod, v) {
    localStorage.setItem(`vmpv-base-${mod}`, v);
  },
  getTok(mod, kind) {
    return localStorage.getItem(`vmpv-tok-${mod}-${kind}`) || "";
  },
  setTok(mod, kind, v) {
    localStorage.setItem(`vmpv-tok-${mod}-${kind}`, v);
  },
  getActive() {
    return localStorage.getItem("vmpv-active-module") || "profile";
  },
  setActive(m) {
    localStorage.setItem("vmpv-active-module", m);
  },
};

// One-time migration from legacy processor UI keys.
(function migrateLegacy() {
  if (localStorage.getItem("vmpv-migrated-v1") === "1") return;
  const adm = localStorage.getItem("vp-adm");
  const wrk = localStorage.getItem("vp-wrk");
  if (adm) LS.setTok("processor", "admin", adm);
  if (wrk) LS.setTok("processor", "worker", wrk);
  localStorage.setItem("vmpv-migrated-v1", "1");
})();

// ----------------------------------------------------------------
// api(module, method, path, opts)
// ----------------------------------------------------------------
async function api(mod, method, path, opts = {}) {
  const cfg = MODULES[mod];
  if (!cfg) throw new Error(`unknown module: ${mod}`);
  const base = LS.getBase(mod);
  const headers = { ...(opts.headers || {}) };

  for (const [kind, prefixes] of cfg.auth) {
    if (prefixes.some((p) => path.startsWith(p))) {
      const tok = LS.getTok(mod, kind);
      if (tok) headers[cfg.headers[kind]] = tok;
      break; // first matching prefix wins
    }
  }

  let body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (opts.form) body = opts.form;

  const r = await fetch(base + path, { method, headers, body, mode: "cors" });
  if (r.status === 204) return null;
  const txt = await r.text();
  let data;
  try { data = JSON.parse(txt); } catch { data = txt; }
  if (!r.ok) {
    const msg = typeof data === "object" ? JSON.stringify(data) : data;
    throw new Error(`${r.status} ${msg}`);
  }
  return data;
}

// ----------------------------------------------------------------
// Module switcher + subtab switcher
// ----------------------------------------------------------------
let currentModule = null;
let currentTab = {}; // module -> tab name
let healthTimer = null;
const moduleHandlers = {}; // module -> { onShow(tab), onTabShow(tab) }

function registerModule(mod, handlers) {
  moduleHandlers[mod] = handlers || {};
}

function switchModule(mod) {
  if (!MODULES[mod]) return;
  currentModule = mod;
  LS.setActive(mod);

  $$(".module-switcher button").forEach((b) => {
    b.classList.toggle("active", b.dataset.module === mod);
  });
  $$("nav.subtabs").forEach((n) => {
    n.style.display = n.dataset.module === mod ? "" : "none";
  });
  $$("section.module").forEach((s) => {
    const active = s.dataset.module === mod;
    s.classList.toggle("active", active);
    s.style.display = active ? "" : "none";
  });

  renderAuthPanel(mod);
  startHealthPoll(mod);

  const tab = currentTab[mod] || getActiveTabFromDOM(mod);
  if (tab) switchTab(mod, tab);

  const h = moduleHandlers[mod];
  if (h && h.onShow) h.onShow(tab);
}

function getActiveTabFromDOM(mod) {
  const nav = $(`nav.subtabs[data-module="${mod}"]`);
  if (!nav) return null;
  const btn = nav.querySelector("button.active") || nav.querySelector("button");
  return btn ? btn.dataset.tab : null;
}

function switchTab(mod, tab) {
  currentTab[mod] = tab;
  const nav = $(`nav.subtabs[data-module="${mod}"]`);
  if (nav) {
    nav.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
  }
  const section = $(`section.module[data-module="${mod}"]`);
  if (section) {
    section.querySelectorAll(":scope > .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tab);
    });
  }
  const h = moduleHandlers[mod];
  if (h && h.onTabShow) h.onTabShow(tab);
}

// ----------------------------------------------------------------
// Auth panel — per-module rebuild
// ----------------------------------------------------------------
function renderAuthPanel(mod) {
  const cfg = MODULES[mod];
  const kinds = new Set(cfg.tokenKinds);
  $("#base-url").value = LS.getBase(mod);
  ["user", "admin", "worker"].forEach((kind) => {
    const wrap = $(`#tok-${kind}-wrap`);
    const input = $(`#tok-${kind}`);
    if (kinds.has(kind)) {
      wrap.style.display = "";
      input.value = LS.getTok(mod, kind);
      const hdr = cfg.headers[kind];
      input.placeholder = hdr;
    } else {
      wrap.style.display = "none";
      input.value = "";
    }
  });
}

function saveAuthPanel() {
  if (!currentModule) return;
  const cfg = MODULES[currentModule];
  const baseVal = $("#base-url").value.trim();
  if (baseVal) LS.setBase(currentModule, baseVal);
  for (const kind of cfg.tokenKinds) {
    LS.setTok(currentModule, kind, $(`#tok-${kind}`).value.trim());
  }
  pollHealth();
  const h = moduleHandlers[currentModule];
  if (h && h.onAuthSave) h.onAuthSave();
}

// ----------------------------------------------------------------
// Health poll
// ----------------------------------------------------------------
async function pollHealth() {
  if (!currentModule) return;
  const cfg = MODULES[currentModule];
  const el = $("#health-status");
  try {
    const base = LS.getBase(currentModule);
    const r = await fetch(base + cfg.health, { mode: "cors" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const j = await r.json();
    el.textContent = cfg.parseHealth(j);
    el.className = "ok";
  } catch (e) {
    el.textContent = "DOWN";
    el.className = "err";
  }
}
function startHealthPoll(/* mod */) {
  if (healthTimer) clearInterval(healthTimer);
  pollHealth();
  healthTimer = setInterval(pollHealth, 5000);
}

// ----------------------------------------------------------------
// Boot
// ----------------------------------------------------------------
function boot() {
  // Module switcher clicks
  $$(".module-switcher button").forEach((b) => {
    b.onclick = () => switchModule(b.dataset.module);
  });
  // Subtab clicks (all modules)
  $$("nav.subtabs button").forEach((b) => {
    b.onclick = () => switchTab(b.closest("nav").dataset.module, b.dataset.tab);
  });
  // Save button
  $("#auth-save").onclick = saveAuthPanel;

  const initial = LS.getActive();
  switchModule(MODULES[initial] ? initial : "profile");
}

// Expose
window.SHELL = { api, LS, MODULES, boot, registerModule, switchModule, switchTab, $, $$ };
