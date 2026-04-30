// processor.js — ported from Modules/processor/ui/static/app.js.
// All calls go through SHELL.api("processor", ...).

(function () {
  const { api, $, $$, LS, registerModule } = window.SHELL;
  const MOD = "processor";

  const root = () => document.querySelector('section.module[data-module="processor"]');
  const q = (sel) => root().querySelector(sel);
  const qa = (sel) => root().querySelectorAll(sel);

  const hasAdmin = () => !!LS.getTok(MOD, "admin");
  const hasWorker = () => !!LS.getTok(MOD, "worker");

  // --- Job polling helper ---
  async function submitJob(path, body, resultEl, onDone) {
    resultEl.textContent = "submitting...";
    try {
      const r = await api(MOD, "POST", path, { json: body });
      const jobId = r.job_id;
      resultEl.textContent = `job ${jobId} queued...`;
      for (let i = 0; i < 600; i++) {
        await new Promise((r) => setTimeout(r, 500));
        const j = await api(MOD, "GET", `/jobs/${jobId}`);
        if (j.status === "done") {
          onDone ? onDone(j) : (resultEl.textContent = JSON.stringify(j.result, null, 2));
          return j;
        }
        if (j.status === "failed") {
          resultEl.textContent = "FAILED: " + j.error;
          return j;
        }
        resultEl.textContent = `job ${jobId} ${j.status}...`;
      }
    } catch (e) {
      resultEl.textContent = "ERROR: " + e.message;
    }
  }

  // --- Files ---
  async function loadFiles() {
    const tbody = q("#files-table tbody");
    if (!hasAdmin()) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="hint">Введите admin + worker токены → Save</td></tr>';
      return;
    }
    try {
      const d = await api(MOD, "GET", "/admin/files");
      tbody.innerHTML = "";
      for (const f of d.items) {
        const tr = document.createElement("tr");
        tr.className = "clickable";
        tr.innerHTML = `<td>${f.name}</td><td>${(f.size_bytes / 1024).toFixed(1)} KB</td><td>${new Date(f.modified_at * 1000).toLocaleString()}</td><td><button data-del="${f.name}">x</button></td>`;
        tr.onclick = (e) => {
          if (e.target.tagName === "BUTTON") return;
          qa("form input[name=file_path]").forEach((i) => (i.value = f.file_path));
        };
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("button[data-del]").forEach((btn) => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          if (!confirm(`Delete ${btn.dataset.del}?`)) return;
          await api(MOD, "DELETE", `/admin/files/${encodeURIComponent(btn.dataset.del)}`);
          loadFiles();
        };
      });
    } catch (e) {
      tbody.innerHTML =
        `<tr><td colspan="4" class="hint" style="color:#f87171">files error: ${e.message}</td></tr>`;
    }
  }

  // --- Keys ---
  async function loadProviders() {
    try {
      const p = await api(MOD, "GET", "/admin/providers");
      const sel = q("#add-key-provider");
      sel.innerHTML = "";
      Object.keys(p).forEach((name) => {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = `${name} [${p[name].kind}]`;
        sel.appendChild(o);
      });
    } catch (e) {}
  }

  async function loadKeys() {
    const tbody = q("#keys-table tbody");
    if (!hasAdmin()) {
      tbody.innerHTML =
        '<tr><td colspan="11" class="hint">Введите admin token → Save</td></tr>';
      return;
    }
    try {
      const keys = await api(MOD, "GET", "/admin/api-keys");
      tbody.innerHTML = "";
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
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-toggle]").forEach((el) =>
        (el.onchange = async (e) => {
          await api(MOD, "PATCH", `/admin/api-keys/${e.target.dataset.toggle}`, {
            json: { is_active: e.target.checked },
          });
        }));
      tbody.querySelectorAll("[data-prio]").forEach((el) =>
        (el.onchange = async (e) => {
          await api(MOD, "PATCH", `/admin/api-keys/${e.target.dataset.prio}`, {
            json: { priority: parseInt(e.target.value) },
          });
        }));
      tbody.querySelectorAll("[data-del]").forEach((el) =>
        (el.onclick = async (e) => {
          if (!confirm("Delete key?")) return;
          await api(MOD, "DELETE", `/admin/api-keys/${e.target.dataset.del}`);
          loadKeys();
        }));
      tbody.querySelectorAll("[data-test]").forEach((el) =>
        (el.onclick = async (e) => {
          try {
            const r = await api(MOD, "POST", `/admin/api-keys/${e.target.dataset.test}/test`);
            alert(JSON.stringify(r));
          } catch (err) { alert("error: " + err.message); }
        }));
    } catch (e) {
      tbody.innerHTML =
        `<tr><td colspan="11" class="hint" style="color:#f87171">keys error: ${e.message}</td></tr>`;
    }
  }

  // --- Usage ---
  async function loadUsage() {
    if (!hasAdmin()) {
      q("#usage-total").textContent = "enter admin token → Save";
      return;
    }
    try {
      const u = await api(MOD, "GET", "/admin/usage");
      q("#usage-total").textContent =
        `calls=${u.total.calls} · cost=$${u.total.cost_usd.toFixed(4)} · errors=${u.total.errors}`;
      const pt = q("#usage-providers tbody");
      pt.innerHTML = "";
      for (const p of u.by_provider) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${p.provider}</td><td>${p.calls}</td><td>${p.cost_usd.toFixed(4)}</td><td>${p.errors}</td><td>${p.avg_latency_ms}</td>`;
        pt.appendChild(tr);
      }
      const dt = q("#usage-days tbody");
      dt.innerHTML = "";
      for (const d of u.by_day) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${d.day}</td><td>${d.calls}</td><td>${d.cost_usd.toFixed(4)}</td>`;
        dt.appendChild(tr);
      }
    } catch (e) {
      q("#usage-total").textContent = "usage error: " + e.message;
    }
  }

  // --- Jobs ---
  let _currentJob = null;
  async function loadJobs() {
    const tbody = q("#jobs-table tbody");
    if (!hasWorker()) {
      tbody.innerHTML =
        '<tr><td colspan="6" class="hint">Введите worker token → Save</td></tr>';
      return;
    }
    try {
      const jobs = await api(MOD, "GET", "/jobs");
      tbody.innerHTML = "";
      for (const j of jobs) {
        const tr = document.createElement("tr");
        tr.className = "clickable";
        tr.innerHTML = `<td>${j.id.slice(0, 8)}…</td><td>${j.kind}</td><td>${j.status}</td><td>${j.created_at.slice(0, 19)}</td><td>${j.finished_at ? j.finished_at.slice(0, 19) : ""}</td><td><button data-view="${j.id}">view</button></td>`;
        tr.onclick = (e) => {
          if (e.target.dataset.view) showJob(j);
        };
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-view]").forEach((el) =>
        (el.onclick = (e) => showJob(jobs.find((x) => x.id === e.target.dataset.view))));
    } catch (e) {
      tbody.innerHTML =
        `<tr><td colspan="6" class="hint" style="color:#f87171">jobs error: ${e.message}</td></tr>`;
    }
  }
  function showJob(j) {
    _currentJob = j;
    q("#job-modal-body").textContent = JSON.stringify(j, null, 2);
    const canReanalyze = j.status === "done" && (j.kind === "full_analysis" || j.kind === "vision_analyze");
    q("#job-reanalyze-btn").style.display = canReanalyze ? "" : "none";
    q("#job-modal").showModal();
  }

  // --- Prompts ---
  async function loadPrompts() {
    const tbody = q("#prompts-table tbody");
    if (!hasAdmin()) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="hint">Введите admin token → Save</td></tr>';
      return;
    }
    try {
      const rows = await api(MOD, "GET", "/admin/prompts");
      tbody.innerHTML = "";
      for (const p of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${p.name}</strong></td>
          <td><code>${p.version}</code></td>
          <td>${p.is_active ? "✅" : ""}</td>
          <td>${(p.created_at || "").slice(0, 19)}</td>
          <td>
            <button data-view="${p.name}::${p.version}">view</button>
            ${p.is_active ? "" : `<button data-activate="${p.name}::${p.version}">activate</button>`}
            ${p.is_active ? "" : `<button data-del="${p.name}::${p.version}">del</button>`}
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-view]").forEach((el) =>
        (el.onclick = async (e) => {
          const [name, version] = e.target.dataset.view.split("::");
          try {
            const rec = await api(MOD, "GET", `/admin/prompts/${encodeURIComponent(name)}/${encodeURIComponent(version)}`);
            q("#prompt-modal-title").textContent = `${name} · ${version}${rec.is_active ? " (active)" : ""}`;
            q("#prompt-modal-body").textContent = rec.body;
            q("#prompt-modal").showModal();
          } catch (err) { alert("view error: " + err.message); }
        }));
      tbody.querySelectorAll("[data-activate]").forEach((el) =>
        (el.onclick = async (e) => {
          const [name, version] = e.target.dataset.activate.split("::");
          if (!confirm(`Activate ${name} ${version}?`)) return;
          try {
            await api(MOD, "PATCH", `/admin/prompts/${encodeURIComponent(name)}/activate/${encodeURIComponent(version)}`);
            loadPrompts();
          } catch (err) { alert("activate error: " + err.message); }
        }));
      tbody.querySelectorAll("[data-del]").forEach((el) =>
        (el.onclick = async (e) => {
          const [name, version] = e.target.dataset.del.split("::");
          if (!confirm(`Delete ${name} ${version}?`)) return;
          try {
            await api(MOD, "DELETE", `/admin/prompts/${encodeURIComponent(name)}/${encodeURIComponent(version)}`);
            loadPrompts();
          } catch (err) { alert("delete error: " + err.message); }
        }));
    } catch (e) {
      tbody.innerHTML =
        `<tr><td colspan="5" class="hint" style="color:#f87171">prompts error: ${e.message}</td></tr>`;
    }
  }

  // --- Diff chart ---
  function drawDiffChart(frames) {
    const canvas = q("#diff-chart");
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

  // --- Wire up (idempotent) ---
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;

    q("#files-refresh").onclick = loadFiles;
    q("#upload-btn").onclick = async () => {
      const f = q("#upload-input").files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      await api(MOD, "POST", "/admin/files/upload", { form: fd });
      q("#upload-input").value = "";
      loadFiles();
    };

    q("#transcribe-form").onsubmit = async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(e.target));
      Object.keys(data).forEach((k) => data[k] === "" && delete data[k]);
      await submitJob("/jobs/transcribe", data, q("#transcribe-result"));
    };

    q("#frames-form").onsubmit = async (e) => {
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
      q("#frames-gallery").innerHTML = "";
      q("#frames-stats").textContent = "submitting...";
      await submitJob("/jobs/extract-frames", body, q("#frames-stats"), (j) => {
        const r = j.result.frames;
        q("#frames-stats").textContent =
          `raw=${r.stats.raw_count} kept=${r.stats.kept_count} dropped=${r.stats.dropped_count} duration=${r.stats.duration_sec}s`;
        const g = q("#frames-gallery");
        g.innerHTML = "";
        const base = LS.getBase(MOD);
        for (const fr of r.extracted) {
          const parts = fr.file_path.replace(/\\/g, "/").split("/");
          const name = parts[parts.length - 1];
          const jobId = parts[parts.length - 2];
          const fig = document.createElement("figure");
          fig.innerHTML = `<img src="${base}/frames/${jobId}/${name}" loading="lazy"><figcaption>#${fr.index} · ${fr.timestamp_sec}s · Δ${fr.diff_ratio}</figcaption>`;
          g.appendChild(fig);
        }
        drawDiffChart(r.extracted);
      });
    };

    q("#vision-form").onsubmit = async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(e.target));
      Object.keys(data).forEach((k) => data[k] === "" && delete data[k]);
      await submitJob("/jobs/vision-analyze", data, q("#vision-result"));
    };

    q("#full-form").onsubmit = async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(e.target));
      Object.keys(data).forEach((k) => data[k] === "" && delete data[k]);
      await submitJob("/jobs/full-analysis", data, q("#full-result"));
    };

    q("#keys-refresh").onclick = loadKeys;
    q("#add-key-form").onsubmit = async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(e.target));
      if (d.monthly_limit_usd === "") delete d.monthly_limit_usd;
      else d.monthly_limit_usd = parseFloat(d.monthly_limit_usd);
      d.priority = parseInt(d.priority);
      await api(MOD, "POST", "/admin/api-keys", { json: d });
      e.target.reset();
      loadKeys();
    };

    q("#usage-refresh").onclick = loadUsage;
    q("#jobs-refresh").onclick = loadJobs;
    q("#job-modal-close").onclick = () => q("#job-modal").close();

    q("#job-reanalyze-btn").onclick = () => {
      if (!_currentJob) return;
      q("#job-modal").close();
      const form = q("#reanalyze-form");
      form.reset();
      form.querySelector("[name=base_job_id]").value = _currentJob.id;
      q("#reanalyze-result").textContent = "";
      q("#reanalyze-modal").showModal();
    };
    q("#reanalyze-cancel").onclick = () => q("#reanalyze-modal").close();
    q("#reanalyze-form").onsubmit = async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(e.target));
      const base_job_id = d.base_job_id;
      delete d.base_job_id;
      Object.keys(d).forEach((k) => d[k] === "" && delete d[k]);
      const body = { base_job_id, override: d };
      const resultEl = q("#reanalyze-result");
      resultEl.textContent = "submitting...";
      try {
        const r = await api(MOD, "POST", "/jobs/reanalyze", { json: body });
        resultEl.textContent = `new job ${r.job_id} queued (reanalysis_of=${r.reanalysis_of})`;
        for (let i = 0; i < 600; i++) {
          await new Promise((r) => setTimeout(r, 500));
          const j = await api(MOD, "GET", `/jobs/${r.job_id}`);
          if (j.status === "done" || j.status === "failed") {
            resultEl.textContent = `new job ${r.job_id} → ${j.status}\n\n` +
              (j.status === "done" ? JSON.stringify(j.result, null, 2) : j.error);
            loadJobs();
            return;
          }
          resultEl.textContent = `new job ${r.job_id} ${j.status}...`;
        }
      } catch (e) {
        resultEl.textContent = "ERROR: " + e.message;
      }
    };

    q("#prompts-refresh").onclick = loadPrompts;
    q("#prompts-new").onclick = () => {
      q("#prompts-editor").open = true;
      q("#prompts-editor").scrollIntoView({ behavior: "smooth" });
    };
    q("#prompts-form").onsubmit = async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(e.target));
      const body = {
        name: d.name,
        version: d.version,
        body: d.body,
        is_active: d.is_active === "on",
      };
      try {
        await api(MOD, "POST", "/admin/prompts", { json: body });
        e.target.reset();
        q("#prompts-editor").open = false;
        loadPrompts();
      } catch (err) { alert("create error: " + err.message); }
    };
    q("#prompt-modal-close").onclick = () => q("#prompt-modal").close();
  }

  // --- Module lifecycle hooks ---
  registerModule(MOD, {
    onShow(tab) {
      wire();
      if (tab === "files") loadFiles();
      else if (tab === "keys") { loadProviders(); loadKeys(); }
      else if (tab === "usage") loadUsage();
      else if (tab === "jobs") loadJobs();
      else if (tab === "prompts") loadPrompts();
    },
    onTabShow(tab) {
      if (tab === "files") loadFiles();
      else if (tab === "keys") { loadProviders(); loadKeys(); }
      else if (tab === "usage") loadUsage();
      else if (tab === "jobs") loadJobs();
      else if (tab === "prompts") loadPrompts();
    },
    onAuthSave() {
      if (hasAdmin()) loadFiles();
    },
  });
})();
