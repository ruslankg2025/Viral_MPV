// script.js — module A5: Generate / Versions / Tree / Templates / Keys.

(function () {
  const { api, registerModule } = window.SHELL;
  const MOD = "script";

  const root = () => document.querySelector('section.module[data-module="script"]');
  const q = (sel) => root().querySelector(sel);

  let currentVersion = null;

  // ---------------- Generate ----------------
  async function loadTemplateOptions() {
    try {
      const rows = await api(MOD, "GET", "/script/admin/templates");
      const sel = q("#script-template-select");
      sel.innerHTML = "";
      for (const t of rows) {
        const o = document.createElement("option");
        o.value = t.name;
        o.textContent = `${t.name} · ${t.version}${t.is_active ? " (active)" : ""}`;
        sel.appendChild(o);
      }
    } catch (e) {
      // silent — keys tab or templates tab will surface error
    }
  }

  async function doGenerate(e) {
    e.preventDefault();
    const f = e.target;
    const resultEl = q("#script-generate-result");
    let params, profile;
    try {
      params = JSON.parse(f.params.value || "{}");
      profile = JSON.parse(f.profile.value || "{}");
    } catch (err) { alert("params/profile must be valid JSON"); return; }

    const body = {
      template: f.template.value,
      params,
      profile,
    };
    if (f.template_version.value.trim()) body.template_version = f.template_version.value.trim();
    if (f.provider.value.trim()) body.provider = f.provider.value.trim();

    resultEl.textContent = "generating...";
    try {
      const rec = await api(MOD, "POST", "/script/generate", { json: body });
      resultEl.textContent = JSON.stringify(rec, null, 2);
      if (rec.id) {
        q("#script-version-id").value = rec.id;
        q("#script-tree-root").value = rec.root_id || rec.id;
      }
    } catch (err) {
      resultEl.textContent = "ERROR: " + err.message;
    }
  }

  // ---------------- Versions ----------------
  async function loadVersion() {
    const id = q("#script-version-id").value.trim();
    if (!id) return;
    const resultEl = q("#script-version-result");
    try {
      const rec = await api(MOD, "GET", `/script/${encodeURIComponent(id)}`);
      currentVersion = rec;
      resultEl.textContent = JSON.stringify(rec, null, 2);
      q("#script-version-actions").style.display = "flex";
    } catch (err) {
      currentVersion = null;
      resultEl.textContent = "ERROR: " + err.message;
      q("#script-version-actions").style.display = "none";
    }
  }

  async function exportVersion(fmt) {
    if (!currentVersion) return;
    try {
      const data = await api(MOD, "GET", `/script/${encodeURIComponent(currentVersion.id)}/export/${fmt}`);
      const blob = new Blob([typeof data === "string" ? data : JSON.stringify(data, null, 2)], {
        type: fmt === "markdown" ? "text/markdown" : "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `script_${currentVersion.id.slice(0, 8)}.${fmt === "markdown" ? "md" : "json"}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) { alert("export error: " + err.message); }
  }

  async function deleteVersion() {
    if (!currentVersion) return;
    if (!confirm(`Delete version ${currentVersion.id}?`)) return;
    try {
      await api(MOD, "DELETE", `/script/${encodeURIComponent(currentVersion.id)}`);
      q("#script-version-result").textContent = "deleted";
      currentVersion = null;
      q("#script-version-actions").style.display = "none";
    } catch (err) { alert("delete error: " + err.message); }
  }

  function openForkDialog() {
    if (!currentVersion) return;
    q("#script-fork-form [name=override]").value = "{}";
    q("#script-fork-modal").showModal();
  }

  async function doFork(e) {
    e.preventDefault();
    if (!currentVersion) return;
    let override;
    try { override = JSON.parse(e.target.override.value || "{}"); }
    catch { alert("override must be valid JSON"); return; }
    try {
      const rec = await api(MOD, "POST", `/script/${encodeURIComponent(currentVersion.id)}/fork`, { json: { override } });
      q("#script-fork-modal").close();
      q("#script-version-id").value = rec.id;
      loadVersion();
    } catch (err) { alert("fork error: " + err.message); }
  }

  // ---------------- Tree ----------------
  async function loadTree() {
    const rootId = q("#script-tree-root").value.trim();
    if (!rootId) return;
    const container = q("#script-tree");
    container.innerHTML = "";
    try {
      const nodes = await api(MOD, "GET", `/script/tree/${encodeURIComponent(rootId)}`);
      const byParent = new Map();
      for (const n of nodes) {
        const p = n.parent_id || "__root__";
        if (!byParent.has(p)) byParent.set(p, []);
        byParent.get(p).push(n);
      }
      const rootNodes = byParent.get("__root__") || [];
      for (const r of rootNodes) container.appendChild(renderTreeNode(r, byParent));
    } catch (err) {
      container.innerHTML = `<li class="hint" style="color:#f87171">tree error: ${err.message}</li>`;
    }
  }

  function renderTreeNode(node, byParent) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "node";
    const shortId = node.id.slice(0, 8);
    label.textContent = ` ${shortId}… · ${node.template}:${node.template_version} · ${node.status} · $${(node.cost_usd || 0).toFixed(4)}`;
    label.onclick = () => {
      q("#script-version-id").value = node.id;
      loadVersion();
    };
    li.appendChild(label);
    const kids = byParent.get(node.id) || [];
    if (kids.length) {
      const ul = document.createElement("ul");
      for (const k of kids) ul.appendChild(renderTreeNode(k, byParent));
      li.appendChild(ul);
    }
    return li;
  }

  // ---------------- Templates ----------------
  async function loadTemplates() {
    const tbody = q("#script-templates-table tbody");
    try {
      const rows = await api(MOD, "GET", "/script/admin/templates");
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="hint">No templates.</td></tr>';
        return;
      }
      for (const t of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${t.name}</strong></td>
          <td><code>${t.version}</code></td>
          <td>${t.is_active ? "✅" : ""}</td>
          <td>${(t.created_at || "").slice(0, 19)}</td>
          <td>
            <button data-view="${t.name}::${t.version}">view</button>
            ${t.is_active ? "" : `<button data-activate="${t.name}::${t.version}">activate</button>`}
            ${t.is_active ? "" : `<button data-del="${t.name}::${t.version}">del</button>`}
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-view]").forEach((el) =>
        (el.onclick = async () => {
          const [name, version] = el.dataset.view.split("::");
          try {
            const rec = await api(MOD, "GET", `/script/admin/templates/${encodeURIComponent(name)}/${encodeURIComponent(version)}`);
            q("#script-template-modal-title").textContent = `${name} · ${version}`;
            q("#script-template-modal-body").textContent = rec.body || JSON.stringify(rec, null, 2);
            q("#script-template-modal").showModal();
          } catch (err) { alert("view error: " + err.message); }
        }));
      tbody.querySelectorAll("[data-activate]").forEach((el) =>
        (el.onclick = async () => {
          const [name, version] = el.dataset.activate.split("::");
          if (!confirm(`Activate ${name} ${version}?`)) return;
          try {
            await api(MOD, "PATCH", `/script/admin/templates/${encodeURIComponent(name)}/activate/${encodeURIComponent(version)}`);
            loadTemplates();
            loadTemplateOptions();
          } catch (err) { alert("activate error: " + err.message); }
        }));
      tbody.querySelectorAll("[data-del]").forEach((el) =>
        (el.onclick = async () => {
          const [name, version] = el.dataset.del.split("::");
          if (!confirm(`Delete ${name} ${version}?`)) return;
          try {
            await api(MOD, "DELETE", `/script/admin/templates/${encodeURIComponent(name)}/${encodeURIComponent(version)}`);
            loadTemplates();
          } catch (err) { alert("delete error: " + err.message); }
        }));
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" class="hint" style="color:#f87171">templates error: ${e.message}</td></tr>`;
    }
  }

  async function createTemplate(e) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    let metadata;
    try { metadata = JSON.parse(d.metadata || "{}"); }
    catch { alert("metadata must be valid JSON"); return; }
    const body = {
      name: d.name,
      version: d.version,
      body: d.body,
      metadata,
      is_active: d.is_active === "on",
    };
    try {
      await api(MOD, "POST", "/script/admin/templates", { json: body });
      e.target.reset();
      loadTemplates();
      loadTemplateOptions();
    } catch (err) { alert("create error: " + err.message); }
  }

  // ---------------- Keys ----------------
  async function loadKeyProviders() {
    try {
      const p = await api(MOD, "GET", "/script/admin/providers");
      const sel = q("#script-key-provider");
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
    const tbody = q("#script-keys-table tbody");
    try {
      const keys = await api(MOD, "GET", "/script/admin/api-keys");
      tbody.innerHTML = "";
      for (const k of keys) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${k.id}</td>
          <td>${k.provider}</td>
          <td>${k.kind || ""}</td>
          <td>${k.label || ""}</td>
          <td>${k.secret_masked}</td>
          <td><input type="checkbox" ${k.is_active ? "checked" : ""} data-toggle="${k.id}"></td>
          <td><input type="number" value="${k.priority}" data-prio="${k.id}" style="width:60px"></td>
          <td>${(k.usage_30d?.cost_usd ?? 0).toFixed(4)}</td>
          <td>${k.usage_30d?.calls ?? 0}</td>
          <td><button data-del="${k.id}">del</button></td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-toggle]").forEach((el) =>
        (el.onchange = async (e) => {
          await api(MOD, "PATCH", `/script/admin/api-keys/${e.target.dataset.toggle}`, { json: { is_active: e.target.checked } });
        }));
      tbody.querySelectorAll("[data-prio]").forEach((el) =>
        (el.onchange = async (e) => {
          await api(MOD, "PATCH", `/script/admin/api-keys/${e.target.dataset.prio}`, { json: { priority: parseInt(e.target.value) } });
        }));
      tbody.querySelectorAll("[data-del]").forEach((el) =>
        (el.onclick = async (e) => {
          if (!confirm("Delete key?")) return;
          await api(MOD, "DELETE", `/script/admin/api-keys/${e.target.dataset.del}`);
          loadKeys();
        }));
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="10" class="hint" style="color:#f87171">keys error: ${e.message}</td></tr>`;
    }
  }

  async function createKey(e) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    if (d.monthly_limit_usd === "") delete d.monthly_limit_usd;
    else d.monthly_limit_usd = parseFloat(d.monthly_limit_usd);
    d.priority = parseInt(d.priority);
    try {
      await api(MOD, "POST", "/script/admin/api-keys", { json: d });
      e.target.reset();
      loadKeys();
    } catch (err) { alert("create error: " + err.message); }
  }

  // ---------------- Wire up ----------------
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;

    q("#script-generate-form").onsubmit = doGenerate;

    q("#script-version-load").onclick = loadVersion;
    q("#script-export-md").onclick = () => exportVersion("markdown");
    q("#script-export-json").onclick = () => exportVersion("json");
    q("#script-fork-btn").onclick = openForkDialog;
    q("#script-delete-btn").onclick = deleteVersion;
    q("#script-fork-form").onsubmit = doFork;
    q("#script-fork-cancel").onclick = () => q("#script-fork-modal").close();

    q("#script-tree-load").onclick = loadTree;

    q("#script-templates-refresh").onclick = loadTemplates;
    q("#script-template-form").onsubmit = createTemplate;
    q("#script-template-modal-close").onclick = () => q("#script-template-modal").close();

    q("#script-keys-refresh").onclick = loadKeys;
    q("#script-add-key-form").onsubmit = createKey;
  }

  registerModule(MOD, {
    onShow(tab) { wire(); onTabShow(tab); },
    onTabShow(tab) { onTabShow(tab); },
    onAuthSave() {
      loadTemplateOptions();
      loadTemplates();
    },
  });

  function onTabShow(tab) {
    if (tab === "generate") loadTemplateOptions();
    else if (tab === "templates") loadTemplates();
    else if (tab === "keys") { loadKeyProviders(); loadKeys(); }
  }
})();
