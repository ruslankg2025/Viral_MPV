// monitor.js — module A2: Sources / Videos / Trending / Crawl log / Settings.

(function () {
  const { api, registerModule } = window.SHELL;
  const MOD = "monitor";

  const root = () => document.querySelector('section.module[data-module="monitor"]');
  const q = (sel) => root().querySelector(sel);
  const qa = (sel) => root().querySelectorAll(sel);

  const state = {
    accountId: localStorage.getItem("vmpv-monitor-account") || "",
    accountName: localStorage.getItem("vmpv-monitor-account-name") || "",
  };

  function setAccount(id, name) {
    state.accountId = id || "";
    state.accountName = name || "";
    if (id) {
      localStorage.setItem("vmpv-monitor-account", id);
      localStorage.setItem("vmpv-monitor-account-name", name || "");
    } else {
      localStorage.removeItem("vmpv-monitor-account");
      localStorage.removeItem("vmpv-monitor-account-name");
    }
    renderBanner();
  }

  function renderBanner() {
    const banner = q("#monitor-banner");
    if (state.accountId) {
      banner.style.display = "";
      q("#monitor-active-name").textContent = state.accountName || "(no name)";
      q("#monitor-active-id").textContent = ` (${state.accountId.slice(0, 8)}…)`;
    } else {
      banner.style.display = "none";
    }
    const hintShow = !state.accountId;
    const sh = q("#monitor-sources-hint");
    const th = q("#monitor-trending-hint");
    if (sh) sh.style.display = hintShow ? "" : "none";
    if (th) th.style.display = hintShow ? "" : "none";
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ").slice(0, 19);
  }

  function showError(el, e) {
    el.textContent = "ERROR: " + (e && e.message ? e.message : String(e));
    el.className = "result err";
  }

  // ---------------- Sources ----------------
  async function loadSources() {
    const tbody = q("#monitor-sources-table tbody");
    const out = q("#monitor-sources-result");
    out.textContent = "";
    out.className = "result";
    if (!state.accountId) {
      tbody.innerHTML = '<tr><td colspan="6" class="hint">Введите account_id выше и нажмите «Use this account».</td></tr>';
      return;
    }
    try {
      const rows = await api(MOD, "GET", `/monitor/sources?account_id=${encodeURIComponent(state.accountId)}`);
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="hint">No sources yet. Use «+ New source».</td></tr>';
        return;
      }
      for (const s of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${s.channel_name || s.external_id || "—"}</strong><br><span class="muted">${s.channel_url || ""}</span></td>
          <td>${s.platform}</td>
          <td>${s.interval_min} min</td>
          <td>${s.is_active ? "✓" : "✗"}</td>
          <td>${fmtDate(s.last_crawled_at)}</td>
          <td>
            <button data-crawl="${s.id}" class="ghost">Crawl now</button>
            <button data-videos="${s.id}" class="ghost">Videos</button>
            <button data-toggle="${s.id}" data-active="${s.is_active ? 1 : 0}" class="ghost">${s.is_active ? "Pause" : "Resume"}</button>
            <button data-delete="${s.id}" class="ghost">Delete</button>
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-crawl]").forEach((btn) => (btn.onclick = () => crawlSource(btn.dataset.crawl)));
      tbody.querySelectorAll("[data-videos]").forEach((btn) => (btn.onclick = () => {
        q("#monitor-videos-source-id").value = btn.dataset.videos;
        window.SHELL.switchTab(MOD, "videos");
        loadVideos();
      }));
      tbody.querySelectorAll("[data-toggle]").forEach((btn) => (btn.onclick = async () => {
        const active = btn.dataset.active === "1";
        try {
          await api(MOD, "PATCH", `/monitor/sources/${btn.dataset.toggle}`, { json: { is_active: !active } });
          loadSources();
        } catch (e) { showError(out, e); }
      }));
      tbody.querySelectorAll("[data-delete]").forEach((btn) => (btn.onclick = async () => {
        if (!confirm("Delete source?")) return;
        try {
          await api(MOD, "DELETE", `/monitor/sources/${btn.dataset.delete}`);
          loadSources();
        } catch (e) { showError(out, e); }
      }));
    } catch (e) {
      showError(out, e);
    }
  }

  async function crawlSource(sourceId) {
    const out = q("#monitor-sources-result");
    out.textContent = `Crawling ${sourceId}...`;
    out.className = "result";
    try {
      const r = await api(MOD, "POST", `/monitor/sources/${sourceId}/crawl`);
      await loadSources();
      out.textContent = JSON.stringify(r, null, 2);
      out.className = "result";
    } catch (e) { showError(out, e); }
  }

  async function createSource(ev) {
    ev.preventDefault();
    if (!state.accountId) { alert("Set account_id first."); return; }
    const fd = new FormData(ev.target);
    const tagsRaw = (fd.get("tags") || "").toString().trim();
    const body = {
      account_id: state.accountId,
      platform: fd.get("platform"),
      channel_url: fd.get("channel_url"),
      niche_slug: (fd.get("niche_slug") || "").toString().trim() || null,
      priority: Number(fd.get("priority") || 5),
      interval_min: Number(fd.get("interval_min") || 60),
      tags: tagsRaw ? tagsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [],
    };
    const out = q("#monitor-sources-result");
    out.textContent = "Creating...";
    out.className = "result";
    try {
      const r = await api(MOD, "POST", "/monitor/sources", { json: body });
      ev.target.reset();
      await loadSources();
      out.textContent = "Created: " + r.id;
      out.className = "result";
    } catch (e) { showError(out, e); }
  }

  // ---------------- Videos ----------------
  async function loadVideos() {
    const tbody = q("#monitor-videos-table tbody");
    const sourceId = q("#monitor-videos-source-id").value.trim();
    if (!sourceId) {
      tbody.innerHTML = '<tr><td colspan="5" class="hint">Введите source_id (можно нажать «Videos» рядом с источником).</td></tr>';
      return;
    }
    const limit = Number(q("#monitor-videos-limit").value || 50);
    tbody.innerHTML = '<tr><td colspan="5" class="hint">Loading...</td></tr>';
    try {
      const rows = await api(MOD, "GET", `/monitor/videos?source_id=${encodeURIComponent(sourceId)}&limit=${limit}`);
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="hint">No videos yet. Run a crawl first.</td></tr>';
        return;
      }
      for (const v of rows) {
        const tr = document.createElement("tr");
        const shortBadge = v.is_short ? ' <span class="tag" title="≤60s">SHORT</span>' : "";
        tr.innerHTML = `
          <td><strong>${v.title || "(no title)"}</strong>${shortBadge}<br><a href="${v.url}" target="_blank" class="muted">${v.url || ""}</a></td>
          <td>${fmtDate(v.published_at)}</td>
          <td>${v.duration_sec ?? "—"}s</td>
          <td>${v.external_id}</td>
          <td>
            <button data-detail="${v.id}" class="ghost">Detail</button>
            <button data-metrics="${v.id}" class="ghost">Metrics</button>
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-detail]").forEach((btn) => (btn.onclick = () => showVideoDetail(btn.dataset.detail)));
      tbody.querySelectorAll("[data-metrics]").forEach((btn) => (btn.onclick = () => showVideoMetrics(btn.dataset.metrics)));
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" class="err">ERROR: ${e.message}</td></tr>`;
    }
  }

  async function showVideoDetail(videoId) {
    const body = q("#monitor-video-modal-body");
    const title = q("#monitor-video-modal-title");
    const modal = q("#monitor-video-modal");
    title.textContent = `Video ${videoId.slice(0, 8)}…`;
    body.textContent = "Loading...";
    modal.showModal();
    try {
      const r = await api(MOD, "GET", `/monitor/videos/${videoId}`);
      body.textContent = JSON.stringify(r, null, 2);
    } catch (e) { body.textContent = "ERROR: " + e.message; }
  }

  async function showVideoMetrics(videoId) {
    const body = q("#monitor-video-modal-body");
    const title = q("#monitor-video-modal-title");
    const modal = q("#monitor-video-modal");
    title.textContent = `Metrics ${videoId.slice(0, 8)}…`;
    body.textContent = "Loading...";
    modal.showModal();
    try {
      const r = await api(MOD, "GET", `/monitor/videos/${videoId}/metrics`);
      body.textContent = JSON.stringify(r, null, 2);
    } catch (e) { body.textContent = "ERROR: " + e.message; }
  }

  // ---------------- Trending ----------------
  async function loadTrending() {
    const tbody = q("#monitor-trending-table tbody");
    if (!state.accountId) {
      tbody.innerHTML = '<tr><td colspan="7" class="hint">Введите account_id на вкладке Sources.</td></tr>';
      return;
    }
    const limit = Number(q("#monitor-trending-limit").value || 20);
    tbody.innerHTML = '<tr><td colspan="7" class="hint">Loading...</td></tr>';
    try {
      const rows = await api(MOD, "GET", `/monitor/trending?account_id=${encodeURIComponent(state.accountId)}&limit=${limit}`);
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="hint">No trending videos (run a crawl to populate).</td></tr>';
        return;
      }
      for (const t of rows) {
        const tr = document.createElement("tr");
        const zs = t.zscore_24h == null ? "—" : t.zscore_24h.toFixed(2);
        const gr = t.growth_rate_24h == null ? "—" : t.growth_rate_24h.toFixed(2);
        tr.innerHTML = `
          <td><strong>${t.title || "(no title)"}</strong><br><a href="${t.url}" target="_blank" class="muted">${t.url || ""}</a></td>
          <td>${t.channel_name || "—"}</td>
          <td>${t.current_views ?? 0}</td>
          <td>${zs}</td>
          <td>${gr}</td>
          <td>${t.is_trending ? "🔥" : "—"}</td>
          <td>${fmtDate(t.computed_at)}</td>
        `;
        tbody.appendChild(tr);
      }
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="7" class="err">ERROR: ${e.message}</td></tr>`;
    }
  }

  // ---------------- Crawl log ----------------
  async function loadCrawlLog() {
    const tbody = q("#monitor-log-table tbody");
    const sourceId = q("#monitor-log-source-id").value.trim();
    const limit = Number(q("#monitor-log-limit").value || 50);
    const qs = new URLSearchParams({ limit: String(limit) });
    if (sourceId) qs.set("source_id", sourceId);
    tbody.innerHTML = '<tr><td colspan="6" class="hint">Loading...</td></tr>';
    try {
      const rows = await api(MOD, "GET", `/monitor/crawl-log?${qs.toString()}`);
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="hint">No crawl log entries.</td></tr>';
        return;
      }
      for (const r of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${fmtDate(r.started_at)}</td>
          <td>${fmtDate(r.finished_at)}</td>
          <td>${r.status}</td>
          <td>${r.videos_new}</td>
          <td>${r.videos_updated}</td>
          <td class="muted">${r.error || ""}</td>
        `;
        tbody.appendChild(tr);
      }
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" class="err">ERROR: ${e.message}</td></tr>`;
    }
  }

  // ---------------- Settings / Admin ----------------
  async function loadPlan() {
    const out = q("#monitor-plan-result");
    out.textContent = "Loading...";
    out.className = "result";
    try {
      const r = await api(MOD, "GET", "/monitor/admin/plan");
      out.textContent = JSON.stringify(r, null, 2);
      // Подставить текущие значения в форму
      const form = q("#monitor-plan-form");
      form.plan_name.value = r.plan_name;
      form.max_sources_total.value = r.max_sources_total;
      form.min_interval_min.value = r.min_interval_min;
      form.max_results_limit.value = r.max_results_limit;
      form.crawl_anchor_utc.value = r.crawl_anchor_utc;
    } catch (e) { showError(out, e); }
  }

  async function savePlan(ev) {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const body = {
      plan_name: fd.get("plan_name"),
      max_sources_total: Number(fd.get("max_sources_total")),
      min_interval_min: Number(fd.get("min_interval_min")),
      max_results_limit: Number(fd.get("max_results_limit")),
      crawl_anchor_utc: fd.get("crawl_anchor_utc"),
    };
    const out = q("#monitor-plan-result");
    out.textContent = "Saving...";
    out.className = "result";
    try {
      const r = await api(MOD, "PUT", "/monitor/admin/plan", { json: body });
      out.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(out, e); }
  }

  async function loadAdmin() {
    const pl = q("#monitor-platforms-result");
    const qt = q("#monitor-quota-result");
    const ap = q("#monitor-apify-result");
    const sc = q("#monitor-scheduler-result");
    pl.textContent = "Loading..."; qt.textContent = "Loading...";
    ap.textContent = "Loading..."; sc.textContent = "Loading...";
    pl.className = qt.className = ap.className = sc.className = "result";
    await loadPlan();
    try {
      const r = await api(MOD, "GET", "/monitor/admin/platforms");
      pl.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(pl, e); }
    try {
      const r = await api(MOD, "GET", "/monitor/admin/platforms/youtube/quota");
      qt.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(qt, e); }
    try {
      const r = await api(MOD, "GET", "/monitor/admin/platforms/apify/usage");
      ap.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(ap, e); }
    try {
      const r = await api(MOD, "GET", "/monitor/admin/scheduler");
      sc.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(sc, e); }
  }

  async function testYoutube() {
    const pl = q("#monitor-platforms-result");
    pl.textContent = "Testing...";
    pl.className = "result";
    try {
      const r = await api(MOD, "POST", "/monitor/admin/platforms/youtube/test");
      pl.textContent = JSON.stringify(r, null, 2);
    } catch (e) { showError(pl, e); }
  }

  async function reloadScheduler() {
    const sc = q("#monitor-scheduler-result");
    sc.textContent = "Reloading...";
    sc.className = "result";
    try {
      const r = await api(MOD, "POST", "/monitor/admin/scheduler/reload");
      sc.textContent = JSON.stringify(r, null, 2);
      loadAdmin();
    } catch (e) { showError(sc, e); }
  }

  // ---------------- Wire ----------------
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;

    const accInput = q("#monitor-account-id");
    accInput.value = state.accountId;
    q("#monitor-account-save").onclick = () => {
      const v = accInput.value.trim();
      setAccount(v, "");
      loadSources();
    };
    q("#monitor-sources-refresh").onclick = loadSources;
    q("#monitor-new-source-form").onsubmit = createSource;

    q("#monitor-videos-refresh").onclick = loadVideos;
    q("#monitor-video-modal-close").onclick = () => q("#monitor-video-modal").close();

    q("#monitor-trending-refresh").onclick = loadTrending;

    q("#monitor-log-refresh").onclick = loadCrawlLog;

    q("#monitor-admin-refresh").onclick = loadAdmin;
    q("#monitor-yt-test").onclick = testYoutube;
    q("#monitor-scheduler-reload").onclick = reloadScheduler;
    q("#monitor-plan-form").onsubmit = savePlan;

    renderBanner();
  }

  function onTabShow(tab) {
    if (tab === "sources") loadSources();
    else if (tab === "videos") loadVideos();
    else if (tab === "trending") loadTrending();
    else if (tab === "crawl-log") loadCrawlLog();
    else if (tab === "settings") loadAdmin();
  }

  registerModule(MOD, {
    onShow(tab) {
      wire();
      if (tab) onTabShow(tab);
    },
    onTabShow,
    onAuthSave() {
      if (root().querySelector(".tab.active")?.dataset.tab === "sources") loadSources();
    },
  });
})();
