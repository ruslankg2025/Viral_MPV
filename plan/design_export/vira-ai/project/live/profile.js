// profile.js — module A7: Accounts / Brand Book / Audience / Prompt Profile / Taxonomy.

(function () {
  const { api, registerModule } = window.SHELL;
  const MOD = "profile";

  const root = () => document.querySelector('section.module[data-module="profile"]');
  const q = (sel) => root().querySelector(sel);
  const qa = (sel) => root().querySelectorAll(sel);

  const state = {
    activeAccountId: localStorage.getItem("vmpv-profile-account") || null,
    activeAccountName: localStorage.getItem("vmpv-profile-account-name") || null,
  };

  function setActiveAccount(id, name) {
    state.activeAccountId = id;
    state.activeAccountName = name;
    if (id) {
      localStorage.setItem("vmpv-profile-account", id);
      localStorage.setItem("vmpv-profile-account-name", name || "");
    } else {
      localStorage.removeItem("vmpv-profile-account");
      localStorage.removeItem("vmpv-profile-account-name");
    }
    renderBanner();
  }

  function renderBanner() {
    const banner = q("#profile-banner");
    if (state.activeAccountId) {
      banner.style.display = "";
      q("#profile-active-name").textContent = state.activeAccountName || "—";
      q("#profile-active-id").textContent = ` (${state.activeAccountId.slice(0, 8)}…)`;
    } else {
      banner.style.display = "none";
    }
    qa("#brand-book-hint, #audience-hint, #pp-hint").forEach((el) => {
      el.style.display = state.activeAccountId ? "none" : "";
    });
  }

  function requireAccount() {
    if (!state.activeAccountId) {
      alert("Select an account first (Accounts tab).");
      return false;
    }
    return true;
  }

  // ---------------- Accounts ----------------
  async function loadAccounts() {
    const tbody = q("#profile-accounts-table tbody");
    try {
      const rows = await api(MOD, "GET", "/profile/accounts");
      tbody.innerHTML = "";
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="hint">No accounts yet. Use “Seed example” or “+ New account”.</td></tr>';
        return;
      }
      for (const a of rows) {
        const tr = document.createElement("tr");
        const isActive = a.id === state.activeAccountId;
        tr.innerHTML = `
          <td>${a.id.slice(0, 8)}…</td>
          <td><strong>${a.name}</strong></td>
          <td>${a.niche_slug || "—"}</td>
          <td>${(a.created_at || "").slice(0, 19)}</td>
          <td>
            <button data-select="${a.id}" data-name="${encodeURIComponent(a.name)}">${isActive ? "✓ selected" : "select"}</button>
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-select]").forEach((btn) =>
        (btn.onclick = () => {
          setActiveAccount(btn.dataset.select, decodeURIComponent(btn.dataset.name));
          loadAccounts();
        }));
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" class="hint" style="color:#f87171">accounts error: ${e.message}</td></tr>`;
    }
  }

  async function createAccount(e) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    if (!d.niche_slug) delete d.niche_slug;
    try {
      const rec = await api(MOD, "POST", "/profile/accounts", { json: d });
      e.target.reset();
      setActiveAccount(rec.id, rec.name);
      loadAccounts();
    } catch (err) { alert("create error: " + err.message); }
  }

  async function seedExample() {
    if (!confirm("Seed example account (requires admin token)?")) return;
    try {
      const r = await api(MOD, "POST", "/profile/seed");
      alert("seeded: " + JSON.stringify(r));
      loadAccounts();
    } catch (err) { alert("seed error: " + err.message); }
  }

  // ---------------- Brand Book ----------------
  async function loadBrandBook() {
    if (!state.activeAccountId) return;
    try {
      const bb = await api(MOD, "GET", `/profile/accounts/${state.activeAccountId}/brand-book`);
      const form = q("#profile-brand-form");
      if (!bb) {
        form.reset();
        q("#profile-brand-result").textContent = "(empty — save to create)";
        syncRangeOutputs();
        return;
      }
      const tone = bb.tone_of_voice || {};
      form.formality.value = tone.formality ?? 5;
      form.energy.value = tone.energy ?? 5;
      form.humor.value = tone.humor ?? 5;
      form.expertise.value = tone.expertise ?? 5;
      form.forbidden_words.value = (bb.forbidden_words || []).join("\n");
      form.cta.value = (bb.cta || []).join("\n");
      form.extra.value = JSON.stringify(bb.extra || {}, null, 2);
      syncRangeOutputs();
      q("#profile-brand-result").textContent = `updated_at: ${bb.updated_at}`;
    } catch (e) {
      q("#profile-brand-result").textContent = "load error: " + e.message;
    }
  }

  function syncRangeOutputs() {
    const form = q("#profile-brand-form");
    ["formality", "energy", "humor", "expertise"].forEach((k) => {
      const span = form.querySelector(`[data-out="${k}"]`);
      if (span) span.textContent = form[k].value;
    });
  }

  async function saveBrandBook(e) {
    e.preventDefault();
    if (!requireAccount()) return;
    const f = e.target;
    let extra = {};
    try { extra = JSON.parse(f.extra.value || "{}"); }
    catch { alert("extra must be valid JSON"); return; }
    const body = {
      tone: {
        formality: parseInt(f.formality.value),
        energy: parseInt(f.energy.value),
        humor: parseInt(f.humor.value),
        expertise: parseInt(f.expertise.value),
      },
      forbidden_words: f.forbidden_words.value.split("\n").map((s) => s.trim()).filter(Boolean),
      cta: f.cta.value.split("\n").map((s) => s.trim()).filter(Boolean),
      extra,
    };
    try {
      const bb = await api(MOD, "PUT", `/profile/accounts/${state.activeAccountId}/brand-book`, { json: body });
      q("#profile-brand-result").textContent = "saved · updated_at: " + bb.updated_at;
    } catch (err) {
      q("#profile-brand-result").textContent = "save error: " + err.message;
    }
  }

  // ---------------- Audience ----------------
  async function loadAudience() {
    if (!state.activeAccountId) return;
    try {
      const aud = await api(MOD, "GET", `/profile/accounts/${state.activeAccountId}/audience`);
      const form = q("#profile-audience-form");
      if (!aud) {
        form.reset();
        form.extra.value = "{}";
        q("#profile-audience-result").textContent = "(empty — save to create)";
        return;
      }
      form.age_range.value = aud.age_range || "";
      form.gender.value = aud.gender || "";
      form.expertise_level.value = aud.expertise_level || "";
      form.geography.value = aud.geography || "";
      form.pain_points.value = (aud.pain_points || []).join("\n");
      form.desires.value = (aud.desires || []).join("\n");
      form.extra.value = JSON.stringify(aud.extra || {}, null, 2);
      q("#profile-audience-result").textContent = "updated_at: " + aud.updated_at;
    } catch (e) {
      q("#profile-audience-result").textContent = "load error: " + e.message;
    }
  }

  async function saveAudience(e) {
    e.preventDefault();
    if (!requireAccount()) return;
    const f = e.target;
    let extra = {};
    try { extra = JSON.parse(f.extra.value || "{}"); }
    catch { alert("extra must be valid JSON"); return; }
    const body = {
      age_range: f.age_range.value.trim() || null,
      gender: f.gender.value || null,
      expertise_level: f.expertise_level.value || null,
      geography: f.geography.value.trim() || null,
      pain_points: f.pain_points.value.split("\n").map((s) => s.trim()).filter(Boolean),
      desires: f.desires.value.split("\n").map((s) => s.trim()).filter(Boolean),
      extra,
    };
    try {
      const aud = await api(MOD, "PUT", `/profile/accounts/${state.activeAccountId}/audience`, { json: body });
      q("#profile-audience-result").textContent = "saved · updated_at: " + aud.updated_at;
    } catch (err) {
      q("#profile-audience-result").textContent = "save error: " + err.message;
    }
  }

  // ---------------- Prompt Profile ----------------
  async function loadPromptProfile() {
    if (!state.activeAccountId) return;
    const tbody = q("#pp-table tbody");
    try {
      const versions = await api(MOD, "GET", `/profile/accounts/${state.activeAccountId}/prompt-profile/versions`);
      tbody.innerHTML = "";
      if (!versions.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="hint">No versions yet.</td></tr>';
        return;
      }
      for (const v of versions) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><code>${v.version}</code></td>
          <td>${v.is_active ? "✅" : ""}</td>
          <td>${(v.created_at || "").slice(0, 19)}</td>
          <td>
            <button data-view="${v.version}">view</button>
            ${v.is_active ? "" : `<button data-rollback="${v.version}">rollback</button>`}
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-view]").forEach((el) =>
        (el.onclick = () => {
          const ver = el.dataset.view;
          const rec = versions.find((v) => v.version === ver);
          q("#pp-modal-title").textContent = `version ${ver}${rec.is_active ? " (active)" : ""}`;
          q("#pp-modal-body").textContent = JSON.stringify(rec, null, 2);
          q("#pp-modal").showModal();
        }));
      tbody.querySelectorAll("[data-rollback]").forEach((el) =>
        (el.onclick = async () => {
          const ver = el.dataset.rollback;
          if (!confirm(`Rollback to ${ver}?`)) return;
          try {
            await api(MOD, "POST", `/profile/accounts/${state.activeAccountId}/prompt-profile/rollback/${encodeURIComponent(ver)}`);
            loadPromptProfile();
          } catch (err) { alert("rollback error: " + err.message); }
        }));
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="4" class="hint" style="color:#f87171">error: ${e.message}</td></tr>`;
    }
  }

  async function createPromptVersion(e) {
    e.preventDefault();
    if (!requireAccount()) return;
    const d = Object.fromEntries(new FormData(e.target));
    let modifiers, hc, sc;
    try {
      modifiers = JSON.parse(d.modifiers || "{}");
      hc = JSON.parse(d.hard_constraints || "{}");
      sc = JSON.parse(d.soft_constraints || "{}");
    } catch {
      alert("modifiers / constraints must be valid JSON");
      return;
    }
    const body = {
      version: d.version,
      system_prompt: d.system_prompt,
      modifiers,
      hard_constraints: hc,
      soft_constraints: sc,
    };
    try {
      await api(MOD, "POST", `/profile/accounts/${state.activeAccountId}/prompt-profile`, { json: body });
      e.target.reset();
      loadPromptProfile();
    } catch (err) { alert("create error: " + err.message); }
  }

  // ---------------- Taxonomy ----------------
  async function loadTaxonomy() {
    const root = q("#tax-tree");
    root.innerHTML = "";
    try {
      const nodes = await api(MOD, "GET", "/profile/taxonomy");
      const top = nodes.filter((n) => !n.parent_slug);
      for (const n of top) {
        root.appendChild(renderTaxNode(n));
      }
    } catch (e) {
      root.innerHTML = `<li class="hint" style="color:#f87171">taxonomy error: ${e.message}</li>`;
    }
  }

  function renderTaxNode(node) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "node expand";
    label.textContent = ` ${node.label_ru} (${node.slug})`;
    const sub = document.createElement("ul");
    sub.style.display = "none";
    label.onclick = async () => {
      if (sub.style.display === "none") {
        if (!sub.dataset.loaded) {
          try {
            const kids = await api(MOD, "GET", `/profile/taxonomy?parent_slug=${encodeURIComponent(node.slug)}`);
            for (const k of kids) sub.appendChild(renderTaxNode(k));
            if (!kids.length) {
              const empty = document.createElement("li");
              empty.className = "hint";
              empty.textContent = "(no children)";
              sub.appendChild(empty);
            }
            sub.dataset.loaded = "1";
          } catch (e) {
            sub.innerHTML = `<li class="hint" style="color:#f87171">${e.message}</li>`;
          }
        }
        sub.style.display = "";
        label.classList.add("expanded");
        label.classList.remove("expand");
      } else {
        sub.style.display = "none";
        label.classList.remove("expanded");
        label.classList.add("expand");
      }
    };
    li.appendChild(label);
    li.appendChild(sub);
    return li;
  }

  // ---------------- Wire up ----------------
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;

    q("#profile-accounts-refresh").onclick = loadAccounts;
    q("#profile-seed-btn").onclick = seedExample;
    q("#profile-new-account-form").onsubmit = createAccount;

    const brandForm = q("#profile-brand-form");
    brandForm.onsubmit = saveBrandBook;
    brandForm.addEventListener("input", syncRangeOutputs);

    q("#profile-audience-form").onsubmit = saveAudience;

    q("#pp-refresh").onclick = loadPromptProfile;
    q("#pp-new-form").onsubmit = createPromptVersion;
    q("#pp-modal-close").onclick = () => q("#pp-modal").close();

    q("#tax-refresh").onclick = loadTaxonomy;
  }

  registerModule(MOD, {
    onShow(tab) {
      wire();
      renderBanner();
      onTabShow(tab);
    },
    onTabShow(tab) { onTabShow(tab); },
    onAuthSave() {
      loadAccounts();
    },
  });

  function onTabShow(tab) {
    if (tab === "accounts") loadAccounts();
    else if (tab === "brand-book") loadBrandBook();
    else if (tab === "audience") loadAudience();
    else if (tab === "prompt-profile") loadPromptProfile();
    else if (tab === "taxonomy") loadTaxonomy();
  }
})();
