// video-processor test UI — ванильный JS, без фреймворков
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const LS = {
  get adm() { return localStorage.getItem("vp-adm") || ""; },
  set adm(v) { localStorage.setItem("vp-adm", v); },
  get wrk() { return localStorage.getItem("vp-wrk") || ""; },
  set wrk(v) { localStorage.setItem("vp-wrk", v); },
};

function admHeaders() { return { "X-Admin-Token": LS.adm }; }
function wrkHeaders() { return { "X-Worker-Token": LS.wrk }; }

async function api(method, path, opts = {}) {
  const headers = {};
  if (path.startsWith("/admin")) Object.assign(headers, admHeaders());
  if (path.startsWith("/jobs")) Object.assign(headers, wrkHeaders());
  let body = undefined;
  if (opts.json) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.form) body = opts.form;
  const r = await fetch(path, { method, headers, body });
  if (r.status === 204) return null;
  const txt = await r.text();
  let data;
  try { data = JSON.parse(txt); } catch { data = txt; }
  if (!r.ok) throw new Error(`${r.status} ${typeof data === "object" ? JSON.stringify(data) : data}`);
  return data;
}

// --- Tabs ---
$$("nav#tabs button").forEach(btn => {
  btn.onclick = () => {
    $$("nav#tabs button").forEach(b => b.classList.remove("active"));
    $$(".tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "files") loadFiles();
    if (btn.dataset.tab === "keys") { loadProviders(); loadKeys(); }
    if (btn.dataset.tab === "usage") loadUsage();
    if (btn.dataset.tab === "jobs") loadJobs();
  };
});

// --- Auth ---
$("#adm-token").value = LS.adm;
$("#wrk-token").value = LS.wrk;
$("#auth-save").onclick = () => {
  LS.adm = $("#adm-token").value.trim();
  LS.wrk = $("#wrk-token").value.trim();
  pollHealth();
};

async function pollHealth() {
  try {
    const h = await (await fetch("/healthz")).json();
    const el = $("#health-status");
    el.textContent = `ok · ffmpeg=${h.ffmpeg_available} · keys tr=${h.active_keys.transcription} vi=${h.active_keys.vision} · queue=${h.queue_depth}`;
    el.className = "ok";
  } catch (e) {
    $("#health-status").textContent = "DOWN";
    $("#health-status").className = "err";
  }
}
pollHealth();
setInterval(pollHealth, 5000);

// --- Files ---
async function loadFiles() {
  try {
    const d = await api("GET", "/admin/files");
    const tb = $("#files-table tbody");
    tb.innerHTML = "";
    for (const f of d.items) {
      const tr = document.createElement("tr");
      tr.className = "clickable";
      tr.innerHTML = `<td>${f.name}</td><td>${(f.size_bytes/1024).toFixed(1)} KB</td><td>${new Date(f.modified_at*1000).toLocaleString()}</td><td><button data-del="${f.name}">x</button></td>`;
      tr.onclick = (e) => {
        if (e.target.tagName === "BUTTON") return;
        $$("form input[name=file_path]").forEach(i => i.value = f.file_path);
      };
      tb.appendChild(tr);
    }
    tb.querySelectorAll("button[data-del]").forEach(btn => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete ${btn.dataset.del}?`)) return;
        await api("DELETE", `/admin/files/${encodeURIComponent(btn.dataset.del)}`);
        loadFiles();
      };
    });
  } catch (e) { alert("files error: " + e.message); }
}
$("#files-refresh").onclick = loadFiles;
$("#upload-btn").onclick = async () => {
  const f = $("#upload-input").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  await api("POST", "/admin/files/upload", { form: fd });
  $("#upload-input").value = "";
  loadFiles();
};
loadFiles();

// --- Job polling helper ---
async function submitJob(path, body, resultEl, onDone) {
  resultEl.textContent = "submitting...";
  try {
    const r = await api("POST", path, { json: body });
    const jobId = r.job_id;
    resultEl.textContent = `job ${jobId} queued...`;
    for (let i = 0; i < 600; i++) {
      await new Promise(r => setTimeout(r, 500));
      const j = await api("GET", `/jobs/${jobId}`);
      if (j.status === "done") { onDone ? onDone(j) : (resultEl.textContent = JSON.stringify(j.result, null, 2)); return j; }
      if (j.status === "failed") { resultEl.textContent = "FAILED: " + j.error; return j; }
      resultEl.textContent = `job ${jobId} ${j.status}...`;
    }
  } catch (e) {
    resultEl.textContent = "ERROR: " + e.message;
  }
}

// --- Transcribe ---
$("#transcribe-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  Object.keys(data).forEach(k => data[k] === "" && delete data[k]);
  await submitJob("/jobs/transcribe", data, $("#transcribe-result"));
};

// --- Frames ---
$("#frames-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const body = {
    file_path: f.file_path,
    sampling: {
      fps: parseFloat(f.fps),
      diff_threshold: parseFloat(f.diff_threshold),
      min_frames: parseInt(f.min_frames),
      max_frames: parseInt(f.max_frames),
    },
  };
  $("#frames-gallery").innerHTML = "";
  $("#frames-stats").textContent = "submitting...";
  await submitJob("/jobs/extract-frames", body, $("#frames-stats"), (j) => {
    const r = j.result.frames;
    $("#frames-stats").textContent =
      `raw=${r.stats.raw_count} kept=${r.stats.kept_count} dropped=${r.stats.dropped_count} duration=${r.stats.duration_sec}s`;
    const g = $("#frames-gallery");
    g.innerHTML = "";
    // извлечь job_id из пути `media/frames/{jobid}/frame_xxx.jpg`
    for (const fr of r.extracted) {
      const parts = fr.file_path.replace(/\\/g, "/").split("/");
      const name = parts[parts.length - 1];
      const jobId = parts[parts.length - 2];
      const fig = document.createElement("figure");
      fig.innerHTML = `<img src="/admin/frames/${jobId}/${name}" loading="lazy"><figcaption>#${fr.index} · ${fr.timestamp_sec}s · Δ${fr.diff_ratio}</figcaption>`;
      g.appendChild(fig);
    }
    drawDiffChart(r.extracted);
  });
};

function drawDiffChart(frames) {
  const canvas = $("#diff-chart");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!frames.length) return;
  ctx.strokeStyle = "#2a2f3a";
  ctx.beginPath();
  const threshold = 0.10 * H;
  ctx.moveTo(0, H - threshold); ctx.lineTo(W, H - threshold);
  ctx.stroke();
  ctx.fillStyle = "#6ee7b7";
  const step = W / frames.length;
  frames.forEach((f, i) => {
    const h = Math.min(H, f.diff_ratio * H);
    ctx.fillRect(i * step, H - h, step * 0.8, h);
  });
  ctx.fillStyle = "#8a94a6";
  ctx.font = "10px sans-serif";
  ctx.fillText("diff_ratio per kept frame (threshold line 0.10)", 4, 12);
}

// --- Vision ---
$("#vision-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  Object.keys(data).forEach(k => data[k] === "" && delete data[k]);
  await submitJob("/jobs/vision-analyze", data, $("#vision-result"));
};

// --- Full ---
$("#full-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  Object.keys(data).forEach(k => data[k] === "" && delete data[k]);
  await submitJob("/jobs/full-analysis", data, $("#full-result"));
};

// --- Keys ---
async function loadProviders() {
  try {
    const p = await api("GET", "/admin/providers");
    const sel = $("#add-key-provider");
    sel.innerHTML = "";
    Object.keys(p).forEach(name => {
      const o = document.createElement("option");
      o.value = name; o.textContent = `${name} [${p[name].kind}]`;
      sel.appendChild(o);
    });
  } catch (e) {}
}

async function loadKeys() {
  try {
    const keys = await api("GET", "/admin/api-keys");
    const tb = $("#keys-table tbody");
    tb.innerHTML = "";
    for (const k of keys) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${k.id}</td>
        <td>${k.provider}</td>
        <td>${k.kind}</td>
        <td>${k.label || ""}</td>
        <td>${k.secret_masked}</td>
        <td><input type="checkbox" ${k.is_active ? "checked" : ""} data-toggle="${k.id}"></td>
        <td><input type="number" value="${k.priority}" data-prio="${k.id}" style="width:60px"></td>
        <td>${k.monthly_limit_usd ?? ""}</td>
        <td>${k.usage_30d.cost_usd.toFixed(4)}</td>
        <td>${k.usage_30d.calls}</td>
        <td><button data-test="${k.id}">test</button> <button data-del="${k.id}">del</button></td>
      `;
      tb.appendChild(tr);
    }
    tb.querySelectorAll("[data-toggle]").forEach(el => el.onchange = async (e) => {
      await api("PATCH", `/admin/api-keys/${e.target.dataset.toggle}`, { json: { is_active: e.target.checked } });
    });
    tb.querySelectorAll("[data-prio]").forEach(el => el.onchange = async (e) => {
      await api("PATCH", `/admin/api-keys/${e.target.dataset.prio}`, { json: { priority: parseInt(e.target.value) } });
    });
    tb.querySelectorAll("[data-del]").forEach(el => el.onclick = async (e) => {
      if (!confirm("Delete key?")) return;
      await api("DELETE", `/admin/api-keys/${e.target.dataset.del}`);
      loadKeys();
    });
    tb.querySelectorAll("[data-test]").forEach(el => el.onclick = async (e) => {
      try {
        const r = await api("POST", `/admin/api-keys/${e.target.dataset.test}/test`);
        alert(JSON.stringify(r));
      } catch (err) { alert("error: " + err.message); }
    });
  } catch (e) { alert("keys error: " + e.message); }
}

$("#keys-refresh").onclick = loadKeys;
$("#add-key-form").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  if (d.monthly_limit_usd === "") delete d.monthly_limit_usd;
  else d.monthly_limit_usd = parseFloat(d.monthly_limit_usd);
  d.priority = parseInt(d.priority);
  await api("POST", "/admin/api-keys", { json: d });
  e.target.reset();
  loadKeys();
};

// --- Usage ---
async function loadUsage() {
  try {
    const u = await api("GET", "/admin/usage");
    $("#usage-total").textContent =
      `calls=${u.total.calls} · cost=$${u.total.cost_usd.toFixed(4)} · errors=${u.total.errors}`;
    const pt = $("#usage-providers tbody");
    pt.innerHTML = "";
    for (const p of u.by_provider) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${p.provider}</td><td>${p.calls}</td><td>${p.cost_usd.toFixed(4)}</td><td>${p.errors}</td><td>${p.avg_latency_ms}</td>`;
      pt.appendChild(tr);
    }
    const dt = $("#usage-days tbody");
    dt.innerHTML = "";
    for (const d of u.by_day) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${d.day}</td><td>${d.calls}</td><td>${d.cost_usd.toFixed(4)}</td>`;
      dt.appendChild(tr);
    }
  } catch (e) { alert("usage error: " + e.message); }
}
$("#usage-refresh").onclick = loadUsage;

// --- Jobs ---
async function loadJobs() {
  try {
    const jobs = await api("GET", "/jobs");
    const tb = $("#jobs-table tbody");
    tb.innerHTML = "";
    for (const j of jobs) {
      const tr = document.createElement("tr");
      tr.className = "clickable";
      tr.innerHTML = `<td>${j.id.slice(0, 8)}…</td><td>${j.kind}</td><td>${j.status}</td><td>${j.created_at.slice(0, 19)}</td><td>${j.finished_at ? j.finished_at.slice(0, 19) : ""}</td><td><button data-view="${j.id}">view</button></td>`;
      tr.onclick = (e) => {
        if (e.target.dataset.view) showJob(j);
      };
      tb.appendChild(tr);
    }
    tb.querySelectorAll("[data-view]").forEach(el => el.onclick = (e) => showJob(jobs.find(x => x.id === e.target.dataset.view)));
  } catch (e) { alert("jobs error: " + e.message); }
}
function showJob(j) {
  $("#job-modal-body").textContent = JSON.stringify(j, null, 2);
  $("#job-modal").showModal();
}
$("#job-modal-close").onclick = () => $("#job-modal").close();
$("#jobs-refresh").onclick = loadJobs;
