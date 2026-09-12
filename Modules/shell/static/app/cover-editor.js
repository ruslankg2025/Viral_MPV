/* Редактор обложек рилсов — вкладка «Обложки» (AI-студия).
 * Рендер на КЛИЕНТЕ: один <canvas> в целевом разрешении (1080×1920 и т.д.),
 * CSS-масштабирование для показа → toBlob даёт точный размер без DPR-магии.
 * Все ассеты (фото/кадры/шрифты) грузятся same-origin БЕЗ crossOrigin — иначе
 * canvas затейнтится и toBlob упадёт. Живёт в глобальном scope index.html:
 * использует _heroToast/escapeHtml/setStudioTab; определяет _renderStudioCover,
 * openCoverEditor (зовётся с карточек разбора/ролика).
 */
(function () {
  'use strict';

  const API = '/api/orchestrator/covers';
  const FORMATS = { '9x16': [1080, 1920], '4x5': [1080, 1350], '1x1': [1080, 1080], '16x9': [1920, 1080] };
  const FORMAT_LABELS = { '9x16': '9:16 · Reels/Stories', '4x5': '4:5 · Карусель', '1x1': '1:1 · Лента', '16x9': '16:9 · YouTube' };
  const FONTS = [
    { key: 'serif',  label: 'Serif (заголовок)', family: 'CoverSerif', weight: 700, url: 'fonts/DejaVuSerif-Bold.ttf' },
    { key: 'bold',   label: 'Inter Bold',        family: 'CoverInter', weight: 700, url: 'fonts/Inter-Bold.ttf' },
    { key: 'semi',   label: 'Inter SemiBold',    family: 'CoverInter', weight: 600, url: 'fonts/Inter-SemiBold.ttf' },
    { key: 'reg',    label: 'Inter Regular',     family: 'CoverInter', weight: 400, url: 'fonts/Inter-Regular.ttf' },
  ];
  const TEAL = '#2DD4BF';

  const state = {
    view: 'gallery',      // 'gallery' | 'editor'
    covers: [],
    photos: [],
    cover: null,          // {id, spec, run_id, ...} редактируемая
    activeLayer: 0,
    runFrames: [],        // [{url, text_on_screen}]
    suggest: [],          // строки-подсказки
    srcTab: 'photos',     // 'photos' | 'frames' | 'upload'
    baseImg: null,        // загруженный Image базового слоя
    dragging: null,
  };

  // ── init: шрифты + CSS ──────────────────────────────────────────────────
  let _fontsReady = null;
  function loadFonts() {
    if (_fontsReady) return _fontsReady;
    _fontsReady = Promise.all(FONTS.map(f => {
      try {
        const ff = new FontFace(f.family, `url(${f.url})`, { weight: String(f.weight) });
        return ff.load().then(loaded => { document.fonts.add(loaded); }).catch(() => {});
      } catch (_) { return Promise.resolve(); }
    })).then(() => document.fonts.ready).catch(() => {});
    return _fontsReady;
  }

  function injectCSS() {
    if (document.getElementById('cvr-css')) return;
    const css = `
    .cvr-gtop{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
    .cvr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
    .cvr-tile{position:relative;cursor:pointer;background:#141417;border:1px solid #232329;border-radius:12px;overflow:hidden}
    .cvr-tile:hover{border-color:#555}
    .cvr-tile img{width:100%;aspect-ratio:9/16;object-fit:cover;background:#000;display:block}
    .cvr-tile-meta{padding:8px 10px;font-size:12px;opacity:.8;display:flex;justify-content:space-between}
    .cvr-del{position:absolute;top:6px;right:6px;width:26px;height:26px;border:0;border-radius:7px;background:rgba(20,20,23,.75);color:#ffb4b4;cursor:pointer;opacity:0;transition:.12s}
    .cvr-tile:hover .cvr-del{opacity:1}
    .cvr-btn{cursor:pointer;background:var(--teal,#168FAA);color:#fff;border:0;border-radius:12px;padding:11px 18px;font:inherit;font-size:14px;font-weight:600}
    .cvr-btn.ghost{background:transparent;color:#ddd;border:1px solid #333;font-weight:500}
    .cvr-btn:disabled{opacity:.5;cursor:default}
    .cvr-editor{display:flex;flex-direction:column;gap:12px}
    .cvr-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .cvr-fmt{display:flex;gap:6px}
    .cvr-chip{cursor:pointer;font-size:12px;padding:7px 11px;border-radius:9px;border:1px solid #333;background:#1d1d22;color:#ddd}
    .cvr-chip.on{background:#28323a;border-color:var(--teal,#168FAA);color:#fff}
    .cvr-body{display:grid;grid-template-columns:230px 1fr 300px;gap:16px;align-items:start}
    @media(max-width:1100px){.cvr-body{grid-template-columns:1fr}}
    .cvr-pane{background:#101013;border:1px solid #232329;border-radius:14px;padding:12px}
    .cvr-srctabs{display:flex;gap:6px;margin-bottom:10px}
    .cvr-srcgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;max-height:520px;overflow:auto}
    .cvr-srcgrid img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px;background:#000;cursor:pointer;border:2px solid transparent}
    .cvr-srcgrid img:hover{border-color:#555}
    .cvr-srcgrid img.sel{border-color:var(--teal,#168FAA)}
    .cvr-up{display:block;text-align:center;cursor:pointer;font-size:13px;padding:16px;border:1px dashed #3a3a42;border-radius:10px;opacity:.85;margin-bottom:10px}
    .cvr-up:hover{opacity:1;border-color:#555}
    .cvr-stage{display:flex;justify-content:center;align-items:flex-start}
    .cvr-phone{position:relative;background:#000;border-radius:26px;padding:0;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.5);border:1px solid #23232b}
    .cvr-canvas-wrap{position:relative;line-height:0}
    #cvr-canvas{display:block;background:#000;touch-action:none;cursor:move;max-height:70vh;width:auto}
    .cvr-ig-top{position:absolute;top:0;left:0;right:0;display:flex;justify-content:space-between;align-items:center;padding:12px 14px;color:#fff;font-size:13px;z-index:3;pointer-events:none;text-shadow:0 1px 3px rgba(0,0,0,.6)}
    .cvr-ig-side{position:absolute;right:10px;bottom:96px;display:flex;flex-direction:column;gap:18px;z-index:3;color:#fff;font-size:22px;pointer-events:none;text-shadow:0 1px 4px rgba(0,0,0,.7)}
    .cvr-ig-bot{position:absolute;left:0;right:0;bottom:0;padding:12px 14px 16px;color:#fff;z-index:3;pointer-events:none;background:linear-gradient(transparent,rgba(0,0,0,.5));text-shadow:0 1px 3px rgba(0,0,0,.6)}
    .cvr-ig-bot .u{font-weight:600;font-size:13px}
    .cvr-ig-bot .c{font-size:12px;opacity:.9;margin-top:3px}
    .cvr-ctrl{display:flex;flex-direction:column;gap:10px;max-height:78vh;overflow:auto}
    .cvr-layers{display:flex;flex-direction:column;gap:6px}
    .cvr-lyr{display:flex;align-items:center;gap:6px;padding:7px 9px;border-radius:8px;border:1px solid #26262c;background:#141417;cursor:pointer;font-size:13px}
    .cvr-lyr.on{border-color:var(--teal,#168FAA);background:#18242a}
    .cvr-lyr .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .cvr-lyr button{background:none;border:0;color:#ffb4b4;cursor:pointer;font-size:13px}
    .cvr-field{display:flex;flex-direction:column;gap:4px;font-size:12px;opacity:.9}
    .cvr-field label{opacity:.6}
    .cvr-field input[type=text],.cvr-field textarea,.cvr-field select{background:#1b1b20;border:1px solid #2a2a30;color:#eee;border-radius:8px;padding:8px 10px;font:inherit;font-size:13px;box-sizing:border-box;width:100%}
    .cvr-field textarea{resize:vertical;min-height:52px}
    .cvr-row{display:flex;gap:8px}.cvr-row>*{flex:1}
    .cvr-range{display:flex;align-items:center;gap:8px}.cvr-range input[type=range]{flex:1}
    .cvr-sug{display:flex;flex-wrap:wrap;gap:6px}
    .cvr-sug .s{cursor:pointer;font-size:12px;padding:5px 9px;border-radius:8px;border:1px solid #333;background:#1d1d22;color:#ddd;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .cvr-sug .s:hover{border-color:var(--teal,#168FAA)}
    .cvr-sec{font-size:11px;letter-spacing:.06em;text-transform:uppercase;opacity:.5;margin:6px 0 2px}
    .cvr-empty{opacity:.55;font-size:14px;padding:26px 4px;text-align:center}
    `;
    const s = document.createElement('style'); s.id = 'cvr-css'; s.textContent = css;
    document.head.appendChild(s);
  }

  // ── API ──────────────────────────────────────────────────────────────────
  async function api(path, opts) {
    const r = await fetch(API + path, opts);
    if (!r.ok) { const t = await r.text().catch(() => ''); throw new Error('HTTP ' + r.status + ' ' + t.slice(0, 120)); }
    return r.status === 204 ? null : r.json();
  }

  // ── дефолтный spec ─────────────────────────────────────────────────────────
  function defaultSpec(fmt) {
    return {
      format: fmt || '9x16',
      base: { kind: null, photoId: null, url: null, scrim: 0.45 },
      layers: [
        { text: 'ЗАГОЛОВОК', fontKey: 'serif', sizeN: 0.11, lineHeight: 1.0, trackingN: 0, color: '#ffffff',
          align: 'left', xN: 0.06, yN: 0.60, uppercase: true,
          shadow: { on: true, blur: 18, dx: 0, dy: 4, color: 'rgba(0,0,0,.55)' }, stroke: { on: false, width: 6, color: '#000' } },
        { text: 'подзаголовок', fontKey: 'bold', sizeN: 0.045, lineHeight: 1.1, trackingN: 0, color: TEAL,
          align: 'left', xN: 0.06, yN: 0.73, uppercase: true,
          shadow: { on: true, blur: 12, dx: 0, dy: 3, color: 'rgba(0,0,0,.5)' }, stroke: { on: false, width: 4, color: '#000' } },
      ],
      watermark: { on: true, text: 'ROXBER.INVEST', color: TEAL },
    };
  }
  function fontByKey(k) { return FONTS.find(f => f.key === k) || FONTS[1]; }

  // ── рендер вкладки ─────────────────────────────────────────────────────────
  window._renderStudioCover = function () {
    injectCSS(); loadFonts();
    const host = document.getElementById('studio-cover-host');
    if (!host) return;
    if (state.view === 'editor' && state.cover) return renderEditor(host);
    renderGallery(host);
  };

  window.openCoverEditor = async function (seed) {
    injectCSS(); await loadFonts();
    // seed: {runId, reelId, baseUrl, title}
    const spec = defaultSpec('9x16');
    if (seed && seed.baseUrl) { spec.base = { kind: 'frame', photoId: null, url: seed.baseUrl, scrim: 0.45 }; }
    let cover;
    try {
      cover = await api('', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: (seed && seed.title) || '', run_id: (seed && seed.runId) || null, reel_id: (seed && seed.reelId) || null, format: '9x16', spec }) });
    } catch (e) { (window._heroToast || alert)('err', 'Не удалось создать обложку: ' + e.message); return; }
    state.cover = cover; state.activeLayer = 0; state.baseImg = null; state.runFrames = []; state.suggest = [];
    state.srcTab = seed && seed.runId ? 'frames' : 'photos';
    if (seed && seed.runId) loadRun(seed.runId);
    state.view = 'editor';
    if (typeof setStudioTab === 'function') setStudioTab('cover'); else window._renderStudioCover();
  };

  async function renderGallery(host) {
    host.innerHTML = `<div class="cvr-gtop"><div style="opacity:.7;font-size:14px">Обложки рилсов — для всех соцсетей</div>
      <button class="cvr-btn" onclick="_coverNew()">＋ Создать обложку</button></div>
      <div class="cvr-grid" id="cvr-gal">— загрузка… —</div>`;
    let covers = [];
    try { covers = await api('') || []; } catch (_) {}
    state.covers = covers;
    const cnt = document.getElementById('cnt-cover'); if (cnt) cnt.textContent = covers.length;
    const gal = document.getElementById('cvr-gal'); if (!gal) return;
    if (!covers.length) { gal.innerHTML = `<div class="cvr-empty" style="grid-column:1/-1">Пока нет обложек. Нажми «Создать обложку» или «🖼 Обложка» на карточке разбора/ролика.</div>`; return; }
    gal.innerHTML = covers.map(c => {
      const prev = (c.renders && (c.renders['9x16'] || Object.values(c.renders)[0])) || '';
      const prevU = prev ? prev + (prev.includes('?') ? '&' : '?') + 't=' + (Date.parse(c.updated_at) || '') : '';
      const img = prev ? `<img src="${escapeHtml(prevU)}" loading="lazy">` : `<div style="aspect-ratio:9/16;display:flex;align-items:center;justify-content:center;background:#000;color:#555;font-size:12px">нет превью</div>`;
      return `<div class="cvr-tile" onclick="_coverEdit('${c.id}')">
        <button class="cvr-del" onclick="event.stopPropagation();_coverDelete('${c.id}')">✕</button>
        ${img}<div class="cvr-tile-meta"><span>${escapeHtml(c.title || 'Обложка')}</span><span>${escapeHtml(c.format || '')}</span></div></div>`;
    }).join('');
  }

  window._coverNew = function () { window.openCoverEditor({}); };
  window._coverEdit = async function (id) {
    try { state.cover = await api('/' + id); } catch (e) { (window._heroToast || alert)('err', e.message); return; }
    state.activeLayer = 0; state.baseImg = null; state.view = 'editor';
    if (state.cover.run_id) loadRun(state.cover.run_id);
    window._renderStudioCover();
  };
  window._coverDelete = async function (id) {
    if (!confirm('Удалить обложку?')) return;
    try { await api('/' + id, { method: 'DELETE' }); } catch (_) {}
    window._renderStudioCover();
  };
  window._coverBack = function () { state.view = 'gallery'; state.cover = null; window._renderStudioCover(); };

  async function loadRun(runId) {
    try {
      const r = await fetch('/api/orchestrator/runs/' + encodeURIComponent(runId));
      if (!r.ok) return;
      const run = await r.json();
      const steps = run.steps || {};
      const frames = ((steps.vision || {}).frames) || [];
      state.runFrames = frames.filter(f => f.thumb_url).map(f => ({ url: f.thumb_url, text: (f.text_on_screen || '').trim() }));
      // подсказки текста: слова на экране + hook + заголовки стратегии
      const sug = new Set();
      frames.forEach(f => { const t = (f.text_on_screen || '').trim(); if (t) sug.add(t); });
      const va = (steps.vision || {}).analysis || {};
      if (va.hook) sug.add(String(va.hook).slice(0, 80));
      (((steps.strategy || {}).sections) || []).forEach(s => { if (s.heading) sug.add(String(s.heading)); });
      // из сгенерированного сценария (если открыт с карточки со сценарием) — client-side
      state.suggest = Array.from(sug).slice(0, 14);
      if (state.view === 'editor') window._renderStudioCover();
    } catch (_) {}
  }

  // ── редактор ────────────────────────────────────────────────────────────
  function renderEditor(host) {
    const c = state.cover, spec = c.spec;
    const fmt = spec.format || '9x16';
    const fmtChips = Object.keys(FORMATS).map(k =>
      `<button class="cvr-chip ${k === fmt ? 'on' : ''}" onclick="_coverSetFmt('${k}')" title="${FORMAT_LABELS[k]}">${k}</button>`).join('');
    host.innerHTML = `
    <div class="cvr-editor">
      <div class="cvr-top">
        <button class="cvr-chip" onclick="_coverBack()">← Обложки</button>
        <input class="cvr-chip" style="background:#141417;min-width:180px" id="cvr-title" placeholder="Название обложки" value="${escapeHtml(c.title || '')}" oninput="_coverTitle(this.value)">
        <span style="opacity:.4">|</span><div class="cvr-fmt">${fmtChips}</div>
        <span style="flex:1"></span>
        <button class="cvr-btn ghost" onclick="_coverSave()">Сохранить</button>
        <button class="cvr-btn" onclick="_coverExportAll()">Экспорт всех</button>
      </div>
      <div class="cvr-body">
        <div class="cvr-pane">${srcPaneHTML()}</div>
        <div class="cvr-stage"><div class="cvr-phone" id="cvr-phone">
          <div class="cvr-canvas-wrap"><canvas id="cvr-canvas"></canvas></div>
          <div class="cvr-ig-top"><span>← Reels</span><span>◎</span></div>
          <div class="cvr-ig-side"><span>♥</span><span>💬</span><span>↗</span><span>🔖</span></div>
          <div class="cvr-ig-bot"><div class="u">@roxber.invest</div><div class="c">${escapeHtml((c.title || 'превью подписи').slice(0, 60))}</div></div>
        </div></div>
        <div class="cvr-pane cvr-ctrl">${ctrlPaneHTML()}</div>
      </div>
    </div>`;
    setupCanvas();
    wireSrcGrid();
    redraw();
  }

  function srcPaneHTML() {
    const tab = state.srcTab;
    const tabs = `<div class="cvr-srctabs">
      <button class="cvr-chip ${tab==='photos'?'on':''}" onclick="_coverSrcTab('photos')">Мои фото</button>
      <button class="cvr-chip ${tab==='frames'?'on':''}" onclick="_coverSrcTab('frames')">Кадры</button>
      <button class="cvr-chip ${tab==='upload'?'on':''}" onclick="_coverSrcTab('upload')">Загрузить</button></div>`;
    let body = '';
    if (tab === 'upload' || tab === 'photos') {
      body += `<label class="cvr-up">＋ Загрузить фото<input type="file" accept="image/*" style="display:none" onchange="_coverUpload(this)"></label>`;
    }
    if (tab === 'frames') {
      body += state.runFrames.length
        ? `<div class="cvr-srcgrid" id="cvr-srcgrid">${state.runFrames.map((f,i)=>`<img src="${escapeHtml(f.url)}" data-url="${escapeHtml(f.url)}" data-txt="${escapeHtml(f.text||'')}" loading="lazy">`).join('')}</div>`
        : `<div class="cvr-empty">Открой обложку с карточки разбора — сюда подтянутся кадры ролика.</div>`;
    } else {
      body += `<div class="cvr-srcgrid" id="cvr-srcgrid">— загрузка… —</div>`;
    }
    return tabs + body;
  }

  function ctrlPaneHTML() {
    const spec = state.cover.spec;
    const L = spec.layers[state.activeLayer] || spec.layers[0];
    const fontOpts = FONTS.map(f => `<option value="${f.key}" ${L.fontKey===f.key?'selected':''}>${f.label}</option>`).join('');
    const layers = spec.layers.map((l, i) =>
      `<div class="cvr-lyr ${i===state.activeLayer?'on':''}" onclick="_coverPickLayer(${i})"><span class="t">${escapeHtml(l.text||'(пусто)')}</span><button onclick="event.stopPropagation();_coverDelLayer(${i})">✕</button></div>`).join('');
    const sug = state.suggest.length
      ? `<div class="cvr-sec">Предложить текст</div><div class="cvr-sug">${state.suggest.map((s,i)=>`<span class="s" title="${escapeHtml(s)}" onclick="_coverUseSuggest(${i})">${escapeHtml(s.slice(0,28))}</span>`).join('')}</div>` : '';
    return `
      <div class="cvr-sec">Слои текста</div>
      <div class="cvr-layers">${layers}</div>
      <button class="cvr-chip" style="align-self:flex-start" onclick="_coverAddLayer()">＋ слой</button>
      ${sug}
      <div class="cvr-sec">Текст слоя</div>
      <div class="cvr-field"><textarea oninput="_coverLyr('text',this.value)">${escapeHtml(L.text||'')}</textarea></div>
      <div class="cvr-row">
        <div class="cvr-field"><label>Шрифт</label><select onchange="_coverLyr('fontKey',this.value)">${fontOpts}</select></div>
        <div class="cvr-field"><label>Цвет</label><input type="color" value="${rgbToHex(L.color)}" onchange="_coverLyr('color',this.value)" style="height:34px;padding:2px"></div>
      </div>
      <div class="cvr-field"><label>Размер ${Math.round(L.sizeN*1000)/10}%</label><div class="cvr-range"><input type="range" min="2" max="22" step="0.2" value="${L.sizeN*100}" oninput="_coverLyr('sizeN',this.value/100)"></div></div>
      <div class="cvr-field"><label>Высота строки ${L.lineHeight}</label><div class="cvr-range"><input type="range" min="0.85" max="1.6" step="0.01" value="${L.lineHeight}" oninput="_coverLyr('lineHeight',+this.value)"></div></div>
      <div class="cvr-field"><label>Трекинг (сжатие) ${Math.round(L.trackingN*100)}%</label><div class="cvr-range"><input type="range" min="-8" max="30" step="1" value="${L.trackingN*100}" oninput="_coverLyr('trackingN',this.value/100)"></div></div>
      <div class="cvr-row">
        <div class="cvr-field"><label>Выравнивание</label><select onchange="_coverLyr('align',this.value)"><option value="left" ${L.align==='left'?'selected':''}>слева</option><option value="center" ${L.align==='center'?'selected':''}>центр</option><option value="right" ${L.align==='right'?'selected':''}>справа</option></select></div>
        <div class="cvr-field"><label>CAPS</label><select onchange="_coverLyr('uppercase',this.value==='1')"><option value="1" ${L.uppercase?'selected':''}>ДА</option><option value="0" ${!L.uppercase?'selected':''}>нет</option></select></div>
      </div>
      <div class="cvr-row">
        <div class="cvr-field"><label>Тень</label><select onchange="_coverLyrObj('shadow','on',this.value==='1')"><option value="1" ${L.shadow.on?'selected':''}>вкл</option><option value="0" ${!L.shadow.on?'selected':''}>выкл</option></select></div>
        <div class="cvr-field"><label>Обводка</label><select onchange="_coverLyrObj('stroke','on',this.value==='1')"><option value="1" ${L.stroke.on?'selected':''}>вкл</option><option value="0" ${!L.stroke.on?'selected':''}>выкл</option></select></div>
      </div>
      <div class="cvr-sec">Фон / бренд</div>
      <div class="cvr-field"><label>Затемнение фона ${Math.round(spec.base.scrim*100)}%</label><div class="cvr-range"><input type="range" min="0" max="90" step="1" value="${spec.base.scrim*100}" oninput="_coverScrim(this.value/100)"></div></div>
      <div class="cvr-field"><label>Водяной знак</label><select onchange="_coverWm(this.value==='1')"><option value="1" ${spec.watermark.on?'selected':''}>ROXBER.INVEST</option><option value="0" ${!spec.watermark.on?'selected':''}>выкл</option></select></div>
      <button class="cvr-btn" style="margin-top:8px" onclick="_coverExport()">Экспорт ${state.cover.spec.format}</button>
    `;
  }

  // ── canvas ────────────────────────────────────────────────────────────────
  let canvas, ctx;
  function setupCanvas() {
    canvas = document.getElementById('cvr-canvas');
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    const [W, H] = FORMATS[state.cover.spec.format] || FORMATS['9x16'];
    canvas.width = W; canvas.height = H;
    // фазировать высоту телефона под формат (для 9:16 — узкий)
    canvas.onpointerdown = onDown; canvas.onpointermove = onMove;
    canvas.onpointerup = canvas.onpointerleave = () => { state.dragging = null; };
  }

  function loadBaseImg() {
    const b = state.cover.spec.base;
    const url = b.url || (b.photoId ? `${API}/photos/${b.photoId}` : null);
    if (!url) { state.baseImg = null; redrawNow(); return; }
    const img = new Image();               // БЕЗ crossOrigin (same-origin!)
    img.onload = () => { state.baseImg = img; redrawNow(); };
    img.onerror = () => { state.baseImg = null; redrawNow(); };
    img.src = url;
  }

  let _redrawT = null;
  function redraw() { loadFonts().then(() => { if (needBaseReload()) loadBaseImg(); else redrawNow(); }); }
  let _lastBaseUrl = '__';
  function needBaseReload() {
    const b = state.cover.spec.base;
    const url = b.url || (b.photoId ? `${API}/photos/${b.photoId}` : '');
    if (url !== _lastBaseUrl) { _lastBaseUrl = url; return true; }
    return false;
  }
  function redrawNow() {
    if (!ctx) return;
    const spec = state.cover.spec; const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    // база
    if (state.baseImg) drawCover(state.baseImg, W, H);
    else { ctx.fillStyle = '#0d0f12'; ctx.fillRect(0, 0, W, H); }
    // скрим снизу для читаемости
    if (spec.base.scrim > 0) {
      const g = ctx.createLinearGradient(0, H * 0.35, 0, H);
      g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, `rgba(0,0,0,${spec.base.scrim})`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }
    // текст-слои
    spec.layers.forEach(l => drawLayer(l, W, H));
    // водяной знак
    if (spec.watermark.on) drawWatermark(spec.watermark, W, H);
  }

  function drawCover(img, W, H) {
    const ir = img.width / img.height, cr = W / H;
    let dw, dh, dx, dy;
    if (ir > cr) { dh = H; dw = H * ir; dx = (W - dw) / 2; dy = 0; }
    else { dw = W; dh = W / ir; dx = 0; dy = (H - dh) / 2; }
    ctx.drawImage(img, dx, dy, dw, dh);
  }

  function drawLayer(l, W, H) {
    let text = l.text || ''; if (l.uppercase) text = text.toUpperCase();
    if (!text.trim()) return;
    const f = fontByKey(l.fontKey);
    const px = Math.max(8, l.sizeN * H);
    ctx.font = `${f.weight} ${px}px "${f.family}", sans-serif`;
    ctx.textBaseline = 'top';
    ctx.textAlign = l.align || 'left';
    if ('letterSpacing' in ctx) { try { ctx.letterSpacing = `${l.trackingN * px}px`; } catch (_) {} }
    const maxW = W * 0.9;
    const lines = wrap(text, maxW);
    const lh = px * (l.lineHeight || 1.05);
    const x = l.xN * W;  // якорь всегда по xN; textAlign определяет сторону (drag работает для всех)
    let y = l.yN * H;
    ctx.shadowColor = l.shadow.on ? (l.shadow.color || 'rgba(0,0,0,.55)') : 'transparent';
    ctx.shadowBlur = l.shadow.on ? l.shadow.blur : 0;
    ctx.shadowOffsetX = l.shadow.on ? l.shadow.dx : 0;
    ctx.shadowOffsetY = l.shadow.on ? l.shadow.dy : 0;
    lines.forEach(line => {
      if (l.stroke.on) { ctx.lineJoin = 'round'; ctx.lineWidth = l.stroke.width || 6; ctx.strokeStyle = l.stroke.color || '#000';
        ctx.shadowColor = 'transparent'; ctx.strokeText(line, x, y);
        ctx.shadowColor = l.shadow.on ? (l.shadow.color) : 'transparent'; ctx.shadowBlur = l.shadow.on ? l.shadow.blur : 0; }
      ctx.fillStyle = l.color || '#fff'; ctx.fillText(line, x, y);
      y += lh;
    });
    ctx.shadowColor = 'transparent'; if ('letterSpacing' in ctx) { try { ctx.letterSpacing = '0px'; } catch (_) {} }
  }

  function wrap(text, maxW) {
    const out = [];
    text.split('\n').forEach(para => {
      const words = para.split(/\s+/); let line = '';
      words.forEach(w => {
        const test = line ? line + ' ' + w : w;
        if (ctx.measureText(test).width > maxW && line) { out.push(line); line = w; }
        else line = test;
      });
      out.push(line);
    });
    return out;
  }

  function drawWatermark(wm, W, H) {
    const px = Math.max(10, 0.026 * H);
    const f = fontByKey('bold');
    ctx.font = `${f.weight} ${px}px "${f.family}", sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    if ('letterSpacing' in ctx) { try { ctx.letterSpacing = `${0.12 * px}px`; } catch (_) {} }
    ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 8; ctx.shadowOffsetY = 2;
    ctx.fillStyle = wm.color || TEAL;
    ctx.fillText((wm.text || 'ROXBER.INVEST').toUpperCase(), W / 2, H * 0.045);
    ctx.shadowColor = 'transparent'; if ('letterSpacing' in ctx) { try { ctx.letterSpacing = '0px'; } catch (_) {} }
  }

  // ── drag активного слоя ─────────────────────────────────────────────────
  function canvasXY(e) {
    const r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  }
  function onDown(e) { const p = canvasXY(e); state.dragging = p; state.dragBase = { ...(state.cover.spec.layers[state.activeLayer]) }; canvas.setPointerCapture(e.pointerId); }
  function onMove(e) {
    if (!state.dragging) return;
    const p = canvasXY(e); const L = state.cover.spec.layers[state.activeLayer]; if (!L) return;
    L.xN = Math.max(0, Math.min(1, (state.dragBase.xN || 0) + (p.x - state.dragging.x)));
    L.yN = Math.max(0, Math.min(1, (state.dragBase.yN || 0) + (p.y - state.dragging.y)));
    redrawNow();
  }

  // ── контролы (глобальные хендлеры) ──────────────────────────────────────
  function L() { return state.cover.spec.layers[state.activeLayer]; }
  window._coverLyr = function (k, v) { const l = L(); if (!l) return; l[k] = v; redrawNow(); if (k === 'text') refreshLayersList(); };
  window._coverLyrObj = function (o, k, v) { const l = L(); if (!l) return; l[o] = l[o] || {}; l[o][k] = v; redrawNow(); };
  window._coverScrim = function (v) { state.cover.spec.base.scrim = v; redrawNow(); };
  window._coverWm = function (on) { state.cover.spec.watermark.on = on; redrawNow(); };
  window._coverPickLayer = function (i) { state.activeLayer = i; renderCtrl(); };
  window._coverAddLayer = function () { state.cover.spec.layers.push(Object.assign(defaultSpec().layers[1], { text: 'НОВЫЙ ТЕКСТ', yN: 0.5 })); state.activeLayer = state.cover.spec.layers.length - 1; renderCtrl(); redrawNow(); };
  window._coverDelLayer = function (i) { state.cover.spec.layers.splice(i, 1); if (!state.cover.spec.layers.length) state.cover.spec.layers.push(defaultSpec().layers[0]); state.activeLayer = 0; renderCtrl(); redrawNow(); };
  window._coverUseSuggest = function (i) { const l = L(); if (!l) return; l.text = state.suggest[i] || l.text; renderCtrl(); redrawNow(); };
  window._coverTitle = function (v) { state.cover.title = v; };
  window._coverSrcTab = function (t) { state.srcTab = t; const host = document.getElementById('studio-cover-host'); const pane = host && host.querySelector('.cvr-pane'); if (pane) { pane.innerHTML = srcPaneHTML(); wireSrcGrid(); } if (t === 'photos') loadPhotos(); };
  window._coverSetFmt = function (k) { state.cover.spec.format = k; setupCanvas(); renderEditor(document.getElementById('studio-cover-host')); };

  function renderCtrl() { const host = document.getElementById('studio-cover-host'); const panes = host && host.querySelectorAll('.cvr-pane'); if (panes && panes[1]) { panes[1].innerHTML = ctrlPaneHTML(); } }
  function refreshLayersList() { const host = document.getElementById('studio-cover-host'); const box = host && host.querySelector('.cvr-layers'); if (box) box.innerHTML = state.cover.spec.layers.map((l, i) => `<div class="cvr-lyr ${i===state.activeLayer?'on':''}" onclick="_coverPickLayer(${i})"><span class="t">${escapeHtml(l.text||'(пусто)')}</span><button onclick="event.stopPropagation();_coverDelLayer(${i})">✕</button></div>`).join(''); }

  function wireSrcGrid() {
    const grid = document.getElementById('cvr-srcgrid'); if (!grid) return;
    grid.querySelectorAll('img').forEach(im => im.onclick = () => {
      const url = im.getAttribute('data-url'); const pid = im.getAttribute('data-pid');
      state.cover.spec.base = { kind: pid ? 'photo' : 'frame', photoId: pid || null, url: pid ? null : url, scrim: state.cover.spec.base.scrim };
      grid.querySelectorAll('img').forEach(x => x.classList.remove('sel')); im.classList.add('sel');
      redraw();
    });
    if (state.srcTab === 'photos') loadPhotos();
  }

  async function loadPhotos() {
    const grid = document.getElementById('cvr-srcgrid'); if (!grid) return;
    let photos = [];
    try { photos = await api('/photos') || []; } catch (_) {}
    state.photos = photos;
    if (!photos.length) { grid.innerHTML = `<div class="cvr-empty" style="grid-column:1/-1">Нет фото. Загрузите выше.</div>`; return; }
    grid.innerHTML = photos.map(p => `<img src="${escapeHtml(p.url)}" data-pid="${p.id}" data-url="${escapeHtml(p.url)}" loading="lazy">`).join('');
    grid.querySelectorAll('img').forEach(im => im.onclick = () => {
      state.cover.spec.base = { kind: 'photo', photoId: im.getAttribute('data-pid'), url: null, scrim: state.cover.spec.base.scrim };
      grid.querySelectorAll('img').forEach(x => x.classList.remove('sel')); im.classList.add('sel'); redraw();
    });
  }

  window._coverUpload = async function (input) {
    const file = input.files && input.files[0]; input.value = '';
    if (!file) return;
    (window._heroToast || function(){})('ok', '📥 Загружаю фото…');
    try {
      // ресайз на клиенте до ≤1600px по большей стороне → экономия + валидное изображение
      const blob = await resizeImage(file, 1600);
      const fd = new FormData(); fd.append('photo', blob, 'photo.jpg');
      const rec = await (await fetch(API + '/photos', { method: 'POST', body: fd })).json();
      state.cover.spec.base = { kind: 'photo', photoId: rec.id, url: null, scrim: state.cover.spec.base.scrim };
      state.srcTab = 'photos'; const host = document.getElementById('studio-cover-host'); const pane = host.querySelector('.cvr-pane'); if (pane) { pane.innerHTML = srcPaneHTML(); }
      await loadPhotos(); redraw();
      (window._heroToast || function(){})('ok', '✓ Фото добавлено');
    } catch (e) { (window._heroToast || alert)('err', 'Не удалось загрузить: ' + (e.message || e)); }
  };

  function resizeImage(file, maxSide) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => {
        let { width: w, height: h } = img;
        const sc = Math.min(1, maxSide / Math.max(w, h));
        w = Math.round(w * sc); h = Math.round(h * sc);
        const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
        cv.getContext('2d').drawImage(img, 0, 0, w, h);
        cv.toBlob(b => b ? res(b) : rej(new Error('encode')), 'image/jpeg', 0.9);
      };
      img.onerror = () => rej(new Error('not an image'));
      img.src = URL.createObjectURL(file);
    });
  }

  // ── сохранение / экспорт ──────────────────────────────────────────────────
  window._coverSave = async function () {
    try {
      await api('/' + state.cover.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: state.cover.title || '', format: state.cover.spec.format, spec: state.cover.spec }) });
      (window._heroToast || function(){})('ok', '✓ Сохранено');
    } catch (e) { (window._heroToast || alert)('err', 'Не сохранилось: ' + e.message); }
  };

  async function exportFormat(fmt) {
    const prevFmt = state.cover.spec.format;
    // рисуем в целевом формате (canvas backing = точный размер)
    state.cover.spec.format = fmt; setupCanvas();
    await new Promise(r => { redraw(); setTimeout(r, 120); }); // дать базе/шрифтам дорисоваться
    const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
    if (!blob) throw new Error('toBlob failed');
    const fd = new FormData(); fd.append('image', blob, fmt + '.png'); fd.append('format', fmt);
    await fetch(API + '/' + state.cover.id + '/render', { method: 'POST', body: fd });
    // вернуть исходный формат превью
    state.cover.spec.format = prevFmt; setupCanvas(); redraw();
  }

  window._coverExport = async function () {
    await _coverSave();
    (window._heroToast || function(){})('ok', 'Экспортирую ' + state.cover.spec.format + '…');
    try { await exportFormat(state.cover.spec.format); (window._heroToast || function(){})('ok', '✓ Готово: ' + state.cover.spec.format); }
    catch (e) { (window._heroToast || alert)('err', 'Экспорт не удался: ' + e.message); }
  };
  window._coverExportAll = async function () {
    await _coverSave();
    (window._heroToast || function(){})('ok', 'Экспортирую все форматы…');
    try { for (const k of Object.keys(FORMATS)) { await exportFormat(k); } (window._heroToast || function(){})('ok', '✓ Все форматы готовы'); }
    catch (e) { (window._heroToast || alert)('err', 'Экспорт не удался: ' + e.message); }
  };

  // ── утилиты ────────────────────────────────────────────────────────────
  function rgbToHex(c) { if (!c) return '#ffffff'; if (c[0] === '#') return c.length === 7 ? c : '#ffffff'; return '#ffffff'; }

})();
