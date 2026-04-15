// downloader.js — stub for module A1 (YouTube / Instagram / TikTok / VK ingestion).

(function () {
  const { LS, MODULES, registerModule } = window.SHELL;
  const MOD = "downloader";

  function root() { return document.querySelector('section.module[data-module="downloader"]'); }
  function q(sel) { return root().querySelector(sel); }

  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    q("#downloader-ping").onclick = async () => {
      const base = LS.getBase(MOD);
      const path = MODULES[MOD].health;
      const out = q("#downloader-result");
      out.textContent = `GET ${base}${path} ...`;
      try {
        const r = await fetch(base + path, { mode: "cors" });
        const txt = await r.text();
        out.textContent = `HTTP ${r.status}\n\n${txt}`;
      } catch (e) {
        out.textContent = "ERROR: " + e.message + "\n\n(backend not implemented yet)";
      }
    };
  }

  registerModule(MOD, { onShow() { wire(); } });
})();
