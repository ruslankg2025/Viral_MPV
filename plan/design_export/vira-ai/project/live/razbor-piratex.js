// VIRA · Razbor Piratex prototype — pipeline state machine + render
// 7-step pipeline (no script gen) → strategy + frames + transcript
// + bickford-fuse spark animation between steps
// + AI-Studio Разбор tab (card grid → click → done-state)
// + В работу button → second pipeline in В обработке tab

(function(){
  'use strict';

  const STEPS = [
    { id:'download',   label:'Скачиваем видео',     duration: 1300, startProgress: 0,  endProgress: 10 },
    { id:'frames',     label:'Извлекаем кадры',     duration: 1600, startProgress: 10, endProgress: 25 },
    { id:'vision',     label:'Анализ кадров',       duration: 8500, startProgress: 25, endProgress: 55 },
    { id:'audio',      label:'Извлекаем аудио',     duration: 1200, startProgress: 55, endProgress: 65 },
    { id:'transcribe', label:'Транскрибируем',      duration: 2200, startProgress: 65, endProgress: 80 },
    { id:'virality',   label:'Анализ виральности',  duration: 3500, startProgress: 80, endProgress: 98 },
    { id:'done',       label:'Разбор готов',        duration: 600,  startProgress: 98, endProgress:100 },
  ];

  const SECOND_STEPS = [
    { id:'hook',       label:'Подбираем зацепку',           duration: 1800, startProgress: 0,  endProgress: 14 },
    { id:'niche',      label:'Адаптируем под нишу',           duration: 2200, startProgress: 14, endProgress: 30 },
    { id:'script',     label:'Пишем сценарий',                duration: 3400, startProgress: 30, endProgress: 56 },
    { id:'desc',       label:'Описание + хэштеги',           duration: 1600, startProgress: 56, endProgress: 68 },
    { id:'storyboard', label:'Раскадровка для съёмки',         duration: 2600, startProgress: 68, endProgress: 84 },
    { id:'brief',      label:'Инструкция монтажёру',         duration: 2400, startProgress: 84, endProgress: 96 },
    { id:'ready',      label:'Готово к продакшену',           duration: 600,  startProgress: 96, endProgress:100 },
  ];

  const SCENE_COLORS = {
    talking_head:  { bg:'rgba(31,168,197,.16)', fg:'#7dd3e8', label:'Говорящая голова' },
    animation:     { bg:'rgba(139,92,246,.18)', fg:'#c4b5fd', label:'Анимация' },
    split_screen:  { bg:'rgba(251,146,60,.16)', fg:'#fdba74', label:'Разделённый экран' },
    cutaway:       { bg:'rgba(148,163,184,.16)',fg:'#cbd5e1', label:'Перебивка' },
    text_overlay:  { bg:'rgba(74,222,128,.14)', fg:'#86efac', label:'Текст' },
    b_roll:        { bg:'rgba(120,113,108,.18)',fg:'#d6d3d1', label:'B-roll' },
    demo:          { bg:'rgba(234,179,8,.14)',  fg:'#fde047', label:'Демо' },
    reaction:      { bg:'rgba(244,114,182,.14)',fg:'#f9a8d4', label:'Реакция' },
  };

  const PLATFORM_COLORS = {
    INSTAGRAM: { bg:'rgba(225,48,108,.12)',  fg:'#f472b6' },
    YOUTUBE:   { bg:'rgba(239,68,68,.14)',   fg:'#fca5a5' },
    TIKTOK:    { bg:'rgba(20,184,166,.14)',  fg:'#5eead4' },
  };

  const state = {
    phase: 'hero',
    stepIdx: -1,
    stepStartTs: 0,
    rafId: null,
    timers: [],
    framesEmitted: 0,
    framesAnalyzed: 0,
    libraryFilter: 'recent',
    libraryQuery: '',
  };

  // ─── icon helpers ────────────────────────────────────────────────
  const ICON_PATHS = {
    download:   '<path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14" stroke-linecap="round" stroke-linejoin="round"/>',
    frames:     '<rect x="4" y="4" width="7" height="7" rx="1.2"/><rect x="13" y="4" width="7" height="7" rx="1.2"/><rect x="4" y="13" width="7" height="7" rx="1.2"/><rect x="13" y="13" width="7" height="7" rx="1.2"/>',
    vision:     '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/>',
    audio:      '<path d="M9 18V8l9-3v10" stroke-linecap="round" stroke-linejoin="round"/><circle cx="6" cy="18" r="3"/><circle cx="15" cy="15" r="3"/>',
    transcribe: '<path d="M5 5h14M12 5v14M8 19h8" stroke-linecap="round" stroke-linejoin="round"/>',
    virality:   '<path d="M4 19l5-7 4 4 7-10" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 6h6v6" stroke-linecap="round" stroke-linejoin="round"/>',
    done:       '<path d="M5 12.5l4 4 10-10" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4"/>',
    check:      '<path d="M5 12.5l4 4 10-10" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4"/>',
    copy:       '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" stroke-linecap="round"/>',
    refresh:    '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 4v3h-3M6 20v-3h3" stroke-linecap="round" stroke-linejoin="round"/>',
    arrow:      '<path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/>',
    download2:  '<path d="M12 4v11m0 0l-4-4m4 4l4-4M4 19h16" stroke-linecap="round" stroke-linejoin="round"/>',
    bolt:       '<path d="M13 3L4 14h7l-1 7 9-11h-7l1-7z" stroke-linejoin="round"/>',
    search:     '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4" stroke-linecap="round"/>',
    play:       '<path d="M7 5v14l12-7z" stroke-linejoin="round"/>',
    plus:       '<path d="M12 5v14M5 12h14" stroke-linecap="round"/>',
    hook:       '<path d="M12 4v8a4 4 0 1 1-4 4" stroke-linecap="round" stroke-linejoin="round"/>',
    niche:      '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/>',
    storyboard: '<rect x="3" y="6" width="7" height="6" rx="1"/><rect x="14" y="6" width="7" height="6" rx="1"/><rect x="3" y="14" width="7" height="6" rx="1"/><rect x="14" y="14" width="7" height="6" rx="1"/>',
    brief:      '<path d="M5 5h14v14H5z"/><path d="M8 9h8M8 13h8M8 17h5" stroke-linecap="round"/>',
    desc:       '<path d="M5 6h14M5 10h14M5 14h10M5 18h7" stroke-linecap="round"/>',
    script:     '<path d="M6 4h9l4 4v12H6z"/><path d="M14 4v5h5" stroke-linecap="round" stroke-linejoin="round"/>',
    ready:      '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/>',
  };
  function svgIcon(name, size=14, weight=1.6){
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${weight}">${ICON_PATHS[name]||''}</svg>`;
  }

  function frameThumb(frame, idx){
    const h = frame.hue;
    const grad = `<defs><linearGradient id="g${idx}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="oklch(38% .14 ${h})"/><stop offset="1" stop-color="oklch(18% .08 ${h})"/></linearGradient></defs>`;
    const ring = frame.type === 'split_screen'
      ? `<line x1="0" y1="120" x2="180" y2="120" stroke="rgba(255,255,255,.45)" stroke-width="1.2"/>`
      : '';
    const txt = frame.text_orig
      ? `<text x="90" y="180" text-anchor="middle" fill="rgba(255,255,255,.94)" font-family="Inter,sans-serif" font-size="14" font-weight="700" letter-spacing=".06em">${escapeHtml(frame.text_orig.slice(0,16))}</text>`
      : '';
    return `<svg viewBox="0 0 180 240" preserveAspectRatio="xMidYMid slice" width="100%" height="100%">${grad}<rect width="180" height="240" fill="url(#g${idx})"/>${ring}${txt}</svg>`;
  }

  function thumb16x9(hue, label){
    return `<svg viewBox="0 0 180 320" preserveAspectRatio="xMidYMid slice" width="100%" height="100%">
      <defs><linearGradient id="lt${label}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="oklch(40% .15 ${hue})"/><stop offset="1" stop-color="oklch(16% .08 ${hue})"/></linearGradient></defs>
      <rect width="180" height="320" fill="url(#lt${label})"/>
    </svg>`;
  }

  function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function fmtK(n){ return n>=1000 ? (n/1000).toFixed(n>=10000?0:1)+'K' : String(n); }
  function fmtTime(sec){ const m=Math.floor(sec/60), s=sec%60; return `${m}:${String(s).padStart(2,'0')}`; }

  // ─── PIPELINE SHELL ──────────────────────────────────────────────
  function renderPipelineShell(){
    const meta = window.RZP_VIDEO_META;
    const stepsHtml = STEPS.map((st,i)=>`
      <div class="rzp-step" data-step="${st.id}" data-idx="${i}">
        <div class="rzp-step-circle">
          <span class="rzp-step-icon">${svgIcon(st.id)}</span>
          <span class="rzp-step-check">${svgIcon('check', 14, 2.4)}</span>
          <span class="rzp-spark-burst"></span>
        </div>
        <div class="rzp-step-label">${st.label}</div>
      </div>
      ${i < STEPS.length-1 ? `<div class="rzp-step-line" data-line="${i}"><span class="rzp-fuse-spark"></span></div>` : ''}
    `).join('');

    const platCol = PLATFORM_COLORS[meta.platform] || PLATFORM_COLORS.INSTAGRAM;
    return `
      <div class="rzp-status-row">
        <div class="rzp-status-text">
          <span class="rzp-pulse"></span>
          <span id="rzp-status-label">Запускаем разбор…</span>
        </div>
        <div class="rzp-status-pct" id="rzp-pct">0%</div>
      </div>
      <div class="rzp-stepper">${stepsHtml}</div>
      <div class="rzp-meta-strip">
        <span class="rzp-platform" style="background:${platCol.bg};color:${platCol.fg}">${meta.platform}</span>
        <span class="rzp-author">${meta.author}</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtTime(meta.duration_sec)}</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtK(meta.views)} просм.</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtK(meta.likes)} лайков</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${meta.comments} комм.</span>
      </div>
      <div class="rzp-stage" id="rzp-stage"></div>
    `;
  }

  function applyStepStates(){
    STEPS.forEach((st, i) => {
      const el = document.querySelector(`.rzp-step[data-idx="${i}"]`);
      if (!el) return;
      el.classList.remove('pending','running','done');
      if (i < state.stepIdx) el.classList.add('done');
      else if (i === state.stepIdx) el.classList.add('running');
      else el.classList.add('pending');
    });
    for (let i=0; i<STEPS.length-1; i++){
      const ln = document.querySelector(`.rzp-step-line[data-line="${i}"]`);
      if (!ln) continue;
      ln.classList.remove('full','active','pending');
      if (i < state.stepIdx) ln.classList.add('full');
      else if (i === state.stepIdx) ln.classList.add('active');
      else ln.classList.add('pending');
    }
  }

  function setStatus(label, pct){
    const lbl = document.getElementById('rzp-status-label');
    const pctEl = document.getElementById('rzp-pct');
    if (lbl) lbl.textContent = label;
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
  }

  // ─── SPARK / BICKFORD FUSE ANIMATION ─────────────────────────────
  function fireStepBurst(idx){
    const step = document.querySelector(`.rzp-step[data-idx="${idx}"]`);
    if (!step) return;
    const burst = step.querySelector('.rzp-spark-burst');
    if (burst){
      burst.classList.remove('go');
      // restart animation
      void burst.offsetWidth;
      burst.classList.add('go');
    }
  }
  function igniteFuse(idx){
    const ln = document.querySelector(`.rzp-step-line[data-line="${idx}"]`);
    if (!ln) return;
    const sp = ln.querySelector('.rzp-fuse-spark');
    if (sp){
      sp.classList.remove('go');
      void sp.offsetWidth;
      sp.classList.add('go');
    }
  }

  // ─── STAGE BLOCKS ─────────────────────────────────────────────────
  function ensureStageBlock(id, html){
    const stage = document.getElementById('rzp-stage');
    if (!stage) return;
    let el = stage.querySelector(`[data-block="${id}"]`);
    if (!el){
      el = document.createElement('div');
      el.dataset.block = id;
      el.className = 'rzp-block rzp-block-enter';
      el.innerHTML = html;
      stage.appendChild(el);
      requestAnimationFrame(()=>el.classList.add('rzp-block-shown'));
    }
    return el;
  }

  function renderTranscriptBlock(running){
    const items = window.RZP_TRANSCRIPT.slice(0, running ? 5 : window.RZP_TRANSCRIPT.length);
    const lines = items.map(([t,line]) => `<div class="rzp-tr-line"><span class="rzp-tr-t">${t}</span><span class="rzp-tr-x">${escapeHtml(line)}</span></div>`).join('');
    const skel = running ? `<div class="rzp-tr-line rzp-tr-skel"><span class="rzp-tr-t">…</span><span class="rzp-skel-bar"></span></div>`.repeat(2) : '';
    return `
      <div class="rzp-card rzp-tr">
        <div class="rzp-card-h">
          <span class="rzp-card-eye">Транскрипт</span>
          <span class="rzp-card-cnt">${items.length}${running?'/'+window.RZP_TRANSCRIPT.length:''}</span>
        </div>
        <div class="rzp-tr-list">${lines}${skel}</div>
      </div>
    `;
  }

  function renderFrameGridBlock(){
    return `
      <div class="rzp-section-eye"><span>Покадровый разбор</span><span class="rzp-section-cnt" id="rzp-fg-cnt">0 / ${window.RZP_FRAMES.length}</span></div>
      <div class="rzp-frames" id="rzp-frames"></div>
    `;
  }

  function appendFrameCard(idx, opts){
    const frames = document.getElementById('rzp-frames');
    if (!frames) return;
    const f = window.RZP_FRAMES[idx];
    const sc = SCENE_COLORS[f.type] || SCENE_COLORS.cutaway;
    const card = document.createElement('div');
    card.className = 'rzp-fc rzp-fc-enter';
    card.dataset.idx = String(idx);
    const showAnalyzing = opts && opts.analyzing;
    card.innerHTML = `
      <div class="rzp-fc-head">
        <span class="rzp-fc-ts">${f.t}</span>
        <span class="rzp-fc-num">#${idx+1}</span>
      </div>
      <div class="rzp-fc-img">${frameThumb(f, idx)}
        <div class="rzp-fc-scan"></div>
        ${showAnalyzing ? `<div class="rzp-fc-analyzing"><span class="rzp-fc-spin"></span>АНАЛИЗ…</div>` : ''}
      </div>
      <div class="rzp-fc-body">
        <div class="rzp-fc-row">
          <div class="rzp-fc-l">Тип сцены</div>
          <div class="rzp-fc-skel rzp-fc-skel-text"><span class="rzp-skel-bar w70"></span></div>
        </div>
        <div class="rzp-fc-row">
          <div class="rzp-fc-l">Текст на экране</div>
          <div class="rzp-fc-skel rzp-fc-skel-text"><span class="rzp-skel-bar"></span></div>
        </div>
        <div class="rzp-fc-row">
          <div class="rzp-fc-l">Визуал</div>
          <div class="rzp-fc-skel rzp-fc-skel-visual"><span class="rzp-skel-bar"></span><span class="rzp-skel-bar"></span><span class="rzp-skel-bar w70"></span></div>
        </div>
      </div>
    `;
    frames.appendChild(card);
    requestAnimationFrame(()=>card.classList.add('rzp-fc-shown'));
    state.framesEmitted = idx+1;
    const cnt = document.getElementById('rzp-fg-cnt');
    if (cnt) cnt.textContent = `${state.framesEmitted} / ${window.RZP_FRAMES.length}`;
  }

  function startAnalyzingFrame(idx){
    const card = document.querySelector(`.rzp-fc[data-idx="${idx}"]`);
    if (!card) return;
    const img = card.querySelector('.rzp-fc-img');
    if (img && !img.querySelector('.rzp-fc-analyzing')){
      const an = document.createElement('div');
      an.className = 'rzp-fc-analyzing';
      an.innerHTML = `<span class="rzp-fc-spin"></span>АНАЛИЗ…`;
      img.appendChild(an);
    }
    card.classList.add('rzp-fc-scanning');
  }

  function fillFrameCard(idx){
    const card = document.querySelector(`.rzp-fc[data-idx="${idx}"]`);
    if (!card) return;
    const f = window.RZP_FRAMES[idx];
    const sc = SCENE_COLORS[f.type] || SCENE_COLORS.cutaway;
    card.classList.add('rzp-fc-filled');
    card.classList.remove('rzp-fc-scanning');
    const body = card.querySelector('.rzp-fc-body');
    const txtBlock = f.text_orig
      ? `<div class="rzp-fc-text-orig">${escapeHtml(f.text_orig)}</div><div class="rzp-fc-text-ru">${escapeHtml(f.text_ru || '—')}</div>`
      : `<div class="rzp-fc-text-empty">—</div>`;
    body.innerHTML = `
      <div class="rzp-fc-row">
        <div class="rzp-fc-l">Тип сцены</div>
        <div class="rzp-fc-v"><span class="rzp-fc-chip" style="background:${sc.bg};color:${sc.fg}">${sc.label}</span></div>
      </div>
      <div class="rzp-fc-row">
        <div class="rzp-fc-l">Текст на экране</div>
        <div class="rzp-fc-v">${txtBlock}</div>
      </div>
      <div class="rzp-fc-row">
        <div class="rzp-fc-l">Визуал</div>
        <div class="rzp-fc-v rzp-fc-visual">${escapeHtml(f.visual)}</div>
      </div>
    `;
    state.framesAnalyzed++;
  }

  function renderViralityCogs(){
    return `
      <div class="rzp-section-eye"><span>Анализ виральности</span><span class="rzp-section-cnt">5 направлений</span></div>
      <div class="rzp-virality">
        ${['Зацепка','Целевая аудитория','Эмоциональные триггеры','Окна публикации','Структура']
          .map(l=>`<div class="rzp-vir-row"><span class="rzp-vir-cog"></span><span class="rzp-vir-l">${l}</span><span class="rzp-skel-bar rzp-vir-bar"></span></div>`).join('')}
      </div>
    `;
  }

  // ─── PIPELINE RUN ─────────────────────────────────────────────────
  function startPipeline(){
    state.phase = 'pipeline';
    state.stepIdx = -1;
    state.framesEmitted = 0;
    state.framesAnalyzed = 0;
    state.timers.forEach(clearTimeout); state.timers = [];

    const heroEl = document.getElementById('rzp-hero');
    const pipeEl = document.getElementById('rzp-pipe');
    if (heroEl) heroEl.style.display = 'none';
    if (pipeEl){ pipeEl.style.display = 'block'; pipeEl.innerHTML = renderPipelineShell(); }

    runStep(0);
    tickProgress();
  }

  function runStep(i){
    if (i >= STEPS.length){ finishPipeline(); return; }
    state.stepIdx = i;
    state.stepStartTs = performance.now();
    applyStepStates();

    const st = STEPS[i];
    setStatus(st.label + '…', st.startProgress);

    // step-specific stage content
    if (st.id === 'frames'){
      ensureStageBlock('frame-grid', renderFrameGridBlock());
      // emit all 20 frame placeholders rapidly during this step
      const total = window.RZP_FRAMES.length;
      const stride = Math.max(40, st.duration / (total + 2));
      for (let k=0; k<total; k++){
        state.timers.push(setTimeout(()=>appendFrameCard(k, {analyzing:false}), 100 + k*stride));
      }
    }
    if (st.id === 'vision'){
      // analyze frames in waves
      const total = window.RZP_FRAMES.length;
      const startAt = 100;
      const waveStride = Math.max(180, (st.duration - 800) / total);
      for (let k=0; k<total; k++){
        state.timers.push(setTimeout(()=>startAnalyzingFrame(k), startAt + k*waveStride));
        state.timers.push(setTimeout(()=>fillFrameCard(k), startAt + k*waveStride + 700));
      }
    }
    if (st.id === 'transcribe'){
      ensureStageBlock('transcribe-running', renderTranscriptBlock(true));
    }

    state.timers.push(setTimeout(()=>{
      // burst on this step's circle, then ignite the fuse to next
      fireStepBurst(i);
      if (i < STEPS.length - 1) igniteFuse(i);
      // give the fuse a moment to travel
      const delay = (i < STEPS.length - 1) ? 380 : 0;
      state.timers.push(setTimeout(()=>{
        state.stepIdx = i + 1;
        applyStepStates();
        runStep(i + 1);
      }, delay));
    }, st.duration));
  }

  function tickProgress(){
    if (state.phase !== 'pipeline'){ state.rafId = null; return; }
    const i = state.stepIdx;
    if (i >= 0 && i < STEPS.length){
      const st = STEPS[i];
      const elapsed = performance.now() - state.stepStartTs;
      const ratio = Math.min(1, elapsed / st.duration);
      const pct = st.startProgress + (st.endProgress - st.startProgress) * ratio;
      setStatus(st.label + (st.id==='done' ? '' : '…'), pct);
    }
    state.rafId = requestAnimationFrame(tickProgress);
  }

  function finishPipeline(){
    state.phase = 'done';
    cancelAnimationFrame(state.rafId);
    state.rafId = null;
    state.timers.forEach(clearTimeout); state.timers = [];
    setStatus('Готово', 100);
    applyStepStates();

    // рисуем done-state ПРЯМО в Разбор — без перехода в студию
    setTimeout(()=>{
      const pipe = document.getElementById('rzp-pipe');
      if (pipe){
        pipe.innerHTML = renderDoneState('cur', { context:'home' });
      }
      window.scrollTo({top:0, behavior:'smooth'});
    }, 700);
  }

  // ─── DONE STATE — sectioned strategy + frames + transcript ───────
  function renderDoneState(itemId, opts){
    opts = opts || {};
    const m = window.RZP_VIDEO_META;
    const platCol = PLATFORM_COLORS[m.platform] || PLATFORM_COLORS.INSTAGRAM;

    const sections = window.RZP_STRATEGY_SECTIONS.map((s,i)=>`
      <div class="rzp-strat-section">
        <div class="rzp-strat-num">${String(i+1).padStart(2,'0')}</div>
        <div class="rzp-strat-content">
          <div class="rzp-strat-h">${escapeHtml(s.title)}</div>
          <div class="rzp-strat-text">${escapeHtml(s.body)}</div>
        </div>
      </div>
    `).join('');

    const frames = window.RZP_FRAMES.map((f,i)=>renderDoneFrameCard(f,i)).join('');

    const trItems = window.RZP_TRANSCRIPT.map(([t,line])=>`<div class="rzp-tr-line"><span class="rzp-tr-t">${t}</span><span class="rzp-tr-x">${escapeHtml(line)}</span></div>`).join('');

    const homeCtx = opts.context === 'home';
    const navBtn = homeCtx
      ? `<button class="rzp-act-sec rzp-newone" onclick="window.RZP.startNewRazbor()">${svgIcon('plus',13,1.8)} Разобрать новый</button>`
      : `<button class="rzp-back" onclick="window.RZP.backToLibrary()" title="Назад">← К списку</button>`;

    return `
      <div class="rzp-meta-strip rzp-meta-strip-done">
        ${homeCtx ? '' : navBtn}
        <span class="rzp-platform" style="background:${platCol.bg};color:${platCol.fg}">${m.platform}</span>
        <span class="rzp-author">${m.author}</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtTime(m.duration_sec)}</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtK(m.views)} просм.</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${fmtK(m.likes)} лайков</span>
        <span class="rzp-meta-dot">·</span>
        <span class="rzp-meta-v">${m.comments} комм.</span>
        ${homeCtx ? `<span class="rzp-meta-actions">${navBtn}<button class="rzp-act-pri rzp-vrabotu" onclick="window.RZP.startSecondPipeline()">${svgIcon('bolt',13,1.8)} В работу</button></span>` : `<button class="rzp-act-pri rzp-vrabotu" onclick="window.RZP.startSecondPipeline()">${svgIcon('bolt',13,1.8)} В работу</button>`}
      </div>

      <div class="rzp-section-eye"><span>Стратегия продвижения</span>
        <span class="rzp-section-tools">
          <button class="rzp-icon-btn" onclick="window.RZP.downloadText('strategy')">${svgIcon('download2',13,1.7)}<span>скачать</span></button>
          <button class="rzp-icon-btn" onclick="window.RZP.copyText('strategy')">${svgIcon('copy',13,1.7)}<span>копировать</span></button>
        </span>
      </div>
      <div class="rzp-card rzp-card-strategy">
        <div class="rzp-strat-list">${sections}</div>
      </div>

      <div class="rzp-section-eye"><span>Покадровый разбор</span><span class="rzp-section-cnt">${window.RZP_FRAMES.length}</span></div>
      <div class="rzp-frames">${frames}</div>

      <div class="rzp-section-eye"><span>Оригинальный транскрипт</span>
        <span class="rzp-section-tools">
          <button class="rzp-icon-btn" onclick="window.RZP.downloadText('transcript')">${svgIcon('download2',13,1.7)}<span>скачать</span></button>
          <button class="rzp-icon-btn" onclick="window.RZP.copyText('transcript')">${svgIcon('copy',13,1.7)}<span>копировать</span></button>
        </span>
      </div>
      <div class="rzp-card rzp-card-transcript">
        <div class="rzp-tr-list rzp-tr-list-done">${trItems}</div>
      </div>
    `;
  }

  function renderDoneFrameCard(f, idx){
    const sc = SCENE_COLORS[f.type] || SCENE_COLORS.cutaway;
    const txt = f.text_orig
      ? `<div class="rzp-fc-text-orig">${escapeHtml(f.text_orig)}</div><div class="rzp-fc-text-ru">${escapeHtml(f.text_ru || '—')}</div>`
      : `<div class="rzp-fc-text-empty">—</div>`;
    return `
      <div class="rzp-fc rzp-fc-shown rzp-fc-filled" data-idx="${idx}">
        <div class="rzp-fc-head">
          <span class="rzp-fc-ts">${f.t}</span>
          <span class="rzp-fc-num">#${idx+1}</span>
        </div>
        <div class="rzp-fc-img">${frameThumb(f, 'd'+idx)}</div>
        <div class="rzp-fc-body">
          <div class="rzp-fc-row"><div class="rzp-fc-l">Тип сцены</div><div class="rzp-fc-v"><span class="rzp-fc-chip" style="background:${sc.bg};color:${sc.fg}">${sc.label}</span></div></div>
          <div class="rzp-fc-row"><div class="rzp-fc-l">Текст на экране</div><div class="rzp-fc-v">${txt}</div></div>
          <div class="rzp-fc-row"><div class="rzp-fc-l">Визуал</div><div class="rzp-fc-v rzp-fc-visual">${escapeHtml(f.visual)}</div></div>
        </div>
      </div>
    `;
  }

  // ─── AI-STUDIO · РАЗБОР TAB · LIBRARY GRID ───────────────────────
  function renderRazborLibrary(){
    const filterChips = [
      { id:'recent', label:'Свежие' },
      { id:'platform_ig', label:'Instagram' },
      { id:'platform_yt', label:'YouTube' },
      { id:'platform_tt', label:'TikTok' },
      { id:'high_er', label:'ER > 8%' },
    ];
    const chips = filterChips.map(c=>`<button class="rzl-chip${state.libraryFilter===c.id?' on':''}" data-f="${c.id}" onclick="window.RZP.setLibraryFilter('${c.id}')">${escapeHtml(c.label)}</button>`).join('');

    const lib = window.RZP_LIBRARY.filter(item => {
      if (state.libraryQuery){
        const q = state.libraryQuery.toLowerCase();
        if (!(item.author.toLowerCase().includes(q) || item.title.toLowerCase().includes(q))) return false;
      }
      if (state.libraryFilter==='platform_ig') return item.platform==='INSTAGRAM';
      if (state.libraryFilter==='platform_yt') return item.platform==='YOUTUBE';
      if (state.libraryFilter==='platform_tt') return item.platform==='TIKTOK';
      if (state.libraryFilter==='high_er') return item.er > 8;
      return true; // recent
    });

    const cards = lib.map(it => renderLibraryCard(it)).join('');

    return `
      <div class="rzl-toolbar">
        <div class="rzl-search">
          ${svgIcon('search',14,1.7)}
          <input class="rzl-search-i" placeholder="Поиск по автору или названию…" value="${escapeHtml(state.libraryQuery)}" oninput="window.RZP.setLibraryQuery(this.value)">
        </div>
        <div class="rzl-chips">${chips}</div>
        <div class="rzl-count">${lib.length} из ${window.RZP_LIBRARY.length}</div>
      </div>
      <div class="rzl-grid">${cards || '<div class="studio-empty">— ничего не найдено —</div>'}</div>
    `;
  }

  function renderLibraryCard(it){
    const platCol = PLATFORM_COLORS[it.platform] || PLATFORM_COLORS.INSTAGRAM;
    const m = it.mult >= 1 ? 'rzl-mult-up' : 'rzl-mult-dn';
    return `
      <div class="rzl-card${it.current?' rzl-card-current':''}" data-id="${it.id}" onclick="window.RZP.openInStudio('${it.id}')">
        <div class="rzl-thumb">${thumb16x9(it.hue, it.id)}
          <span class="rzl-plat" style="background:${platCol.bg};color:${platCol.fg}">${it.platform.slice(0,2)}</span>
          <span class="rzl-dur">${fmtTime(it.dur)}</span>
          ${it.current?`<span class="rzl-current-badge">только что</span>`:''}
        </div>
        <div class="rzl-meta">
          <div class="rzl-views">
            <span class="rzl-v">${fmtK(it.views)}</span>
            <span class="rzl-when">${escapeHtml(it.when)}</span>
          </div>
          <div class="rzl-title">${escapeHtml(it.title)}</div>
          <div class="rzl-author">${escapeHtml(it.author)}</div>
          <div class="rzl-stats">
            <span>♥ ${fmtK(it.likes)}</span>
            <span>💬 ${it.comments}</span>
            <span>${it.er.toFixed(1)}% ER</span>
            <span class="rzl-mult ${m}">${it.mult.toFixed(1)}x</span>
          </div>
        </div>
      </div>
    `;
  }

  // ─── В ОБРАБОТКЕ TAB ─────────────────────────────────────────────
  function renderProcessingTab(){
    const items = window.RZP_PROCESSING.map(p => renderProcessingCard(p)).join('');
    return `<div class="rzp-proc-list">${items}</div>`;
  }

  function renderProcessingCard(p){
    if (p.current){
      // АКТИВНАЯ карточка — рисуем ПОЛНЫЙ stepper, как в Разборе
      const platCol = PLATFORM_COLORS.INSTAGRAM;
      const stepsHtml = SECOND_STEPS.map((st, i) => `
        <div class="rzp-step" data-step="${st.id}" data-idx="${i}">
          <div class="rzp-step-circle">
            <span class="rzp-step-icon">${svgIcon(st.id)}</span>
            <span class="rzp-step-check">${svgIcon('check', 14, 2.4)}</span>
            <span class="rzp-spark-burst"></span>
          </div>
          <div class="rzp-step-label">${st.label}</div>
        </div>
        ${i < SECOND_STEPS.length - 1 ? `<div class="rzp-step-line" data-line="${i}"><span class="rzp-fuse-spark"></span></div>` : ''}
      `).join('');
      return `
        <div class="rzp-pcard rzp-pcard-active" data-id="${p.id}" data-active="1">
          <div class="rzp-pcard-active-head">
            <div class="rzp-pcard-active-eye" id="rzp-pact-status-${p.id}"><span class="rzp-pulse"></span><span class="rzp-pact-label">${escapeHtml(SECOND_STEPS[0].label)}…</span></div>
            <div class="rzp-pcard-active-pct" id="rzp-pact-pct-${p.id}">0%</div>
          </div>
          <div class="rzp-stepper rzp-stepper-proc" id="rzp-pact-stepper-${p.id}">${stepsHtml}</div>
          <div class="rzp-meta-strip rzp-meta-strip-proc">
            <span class="rzp-platform" style="background:${platCol.bg};color:${platCol.fg}">АДАПТАЦИЯ</span>
            <span class="rzp-author">${escapeHtml(p.title)}</span>
            <span class="rzp-meta-dot">·</span>
            <span class="rzp-meta-v">источник: <b style="color:var(--t1)">@${escapeHtml(p.source)}</b></span>
            <span class="rzp-meta-dot">·</span>
            <span class="rzp-meta-v">${escapeHtml(p.when)}</span>
          </div>
        </div>
      `;
    }
    // ОБЫЧНАЯ компактная карточка для фоновых
    const stepLabel = SECOND_STEPS[p.step-1]?.label || 'Готовим…';
    const pct = Math.round((p.step-1)/p.total*100);
    const dotsHtml = SECOND_STEPS.map((s,i)=>`<span class="rzp-pdot${i<p.step-1?' done':''}${i===p.step-1?' active':''}"></span>`).join('');
    return `
      <div class="rzp-pcard" data-id="${p.id}">
        <div class="rzp-pcard-thumb">${thumb16x9(p.hue, 'p'+p.id)}
          <span class="rzp-pcard-spark"></span>
        </div>
        <div class="rzp-pcard-body">
          <div class="rzp-pcard-h">
            <span class="rzp-pcard-eye">Адаптация по разбору</span>
            <span class="rzp-pcard-when">${escapeHtml(p.when)}</span>
          </div>
          <div class="rzp-pcard-title">${escapeHtml(p.title)}</div>
          <div class="rzp-pcard-source">источник: <b>@${escapeHtml(p.source)}</b></div>
          <div class="rzp-pcard-status">
            <span class="rzp-pulse"></span>
            <span class="rzp-pcard-step">${escapeHtml(stepLabel)}…</span>
            <span class="rzp-pcard-pct">${pct}%</span>
          </div>
          <div class="rzp-pcard-dots">${dotsHtml}</div>
        </div>
      </div>
    `;
  }

  function renderReadyTab(){
    const items = window.RZP_READY.map(d=>`
      <div class="rzl-card">
        <div class="rzl-thumb">${thumb16x9(d.hue, 'd'+d.id)}<span class="rzl-dur">сценарий</span></div>
        <div class="rzl-meta">
          <div class="rzl-views"><span class="rzl-v">готово</span><span class="rzl-when">${escapeHtml(d.when)}</span></div>
          <div class="rzl-title">${escapeHtml(d.title)}</div>
          <div class="rzl-author">по разбору @${escapeHtml(d.source)}</div>
        </div>
      </div>
    `).join('');
    return `<div class="rzl-grid">${items}</div>`;
  }

  // ─── second pipeline runner (полный stepper на активной карточке) ────
  let secondTimers = [];
  function runSecondPipeline(pid){
    secondTimers.forEach(clearTimeout);
    secondTimers = [];
    const stepperEl = document.getElementById('rzp-pact-stepper-'+pid);
    if (!stepperEl) return;
    const statusLbl = document.querySelector('#rzp-pact-status-'+pid+' .rzp-pact-label');
    const pctEl = document.getElementById('rzp-pact-pct-'+pid);

    function applyState(idx, phase){
      const stepEls = stepperEl.querySelectorAll('.rzp-step');
      stepEls.forEach((el, j) => {
        el.classList.remove('done','running','pending');
        if (j < idx) el.classList.add('done');
        else if (j === idx) el.classList.add(phase === 'done' ? 'done' : 'running');
        else el.classList.add('pending');
      });
      const lineEls = stepperEl.querySelectorAll('.rzp-step-line');
      lineEls.forEach((el,j) => { el.classList.toggle('done', j < idx); el.classList.toggle('pending', j >= idx); });
    }
    function fireBurstAndFuse(idx){
      const stepEls = stepperEl.querySelectorAll('.rzp-step');
      const burst = stepEls[idx]?.querySelector('.rzp-spark-burst');
      if (burst){ burst.classList.remove('go'); void burst.offsetWidth; burst.classList.add('go'); }
      const lineEls = stepperEl.querySelectorAll('.rzp-step-line');
      const line = lineEls[idx];
      const spark = line?.querySelector('.rzp-fuse-spark');
      if (spark){ spark.classList.remove('go'); void spark.offsetWidth; spark.classList.add('go'); }
    }

    applyState(0, 'running');
    if (statusLbl) statusLbl.textContent = SECOND_STEPS[0].label + '…';
    if (pctEl) pctEl.textContent = '0%';

    let cum = 0;
    SECOND_STEPS.forEach((st, i) => {
      cum += st.duration;
      secondTimers.push(setTimeout(()=>{
        fireBurstAndFuse(i);
        const isLast = i === SECOND_STEPS.length - 1;
        const next = SECOND_STEPS[i+1];
        if (isLast){
          applyState(SECOND_STEPS.length, 'done');
          if (statusLbl) statusLbl.textContent = 'Готово';
          if (pctEl) pctEl.textContent = '100%';
        } else {
          applyState(i+1, 'running');
          if (statusLbl) statusLbl.textContent = next.label + '…';
          if (pctEl) pctEl.textContent = Math.round(next.startProgress) + '%';
        }
      }, cum));
    });
  }

  // ─── public API ──────────────────────────────────────────────────
  window.RZP = {
    boot(){
      this.bindNav();
      const heroEl = document.getElementById('rzp-hero');
      if (heroEl) heroEl.style.display = 'flex';
      setTimeout(()=>{
        const inp = document.getElementById('rzp-link');
        if (inp) inp.value = window.RZP_VIDEO_META.url;
        setTimeout(()=>startPipeline(), 700);
      }, 900);
    },
    bindNav(){
      document.querySelectorAll('.nl').forEach(b => {
        b.addEventListener('click', () => {
          const v = b.dataset.view;
          if (!v) return;
          this.go(v);
        });
      });
      document.querySelectorAll('.stb').forEach(b => {
        b.addEventListener('click', () => {
          document.querySelectorAll('.stb').forEach(x=>x.classList.remove('on'));
          b.classList.add('on');
          const tab = b.dataset.stab;
          document.querySelectorAll('.studio-tab').forEach(x => {
            x.style.display = x.dataset.tab === tab ? 'block' : 'none';
          });
          this.refreshTab(tab);
        });
      });
    },
    go(view){
      // если уходим с home и там был done-state — сбрасываем на hero, чтобы при возврате была Главная
      const cur = document.querySelector('.view[data-view="home"]');
      const wasOnHome = cur && cur.style.display !== 'none';
      if (view !== 'home' && wasOnHome && state.phase === 'done'){
        const heroEl = document.getElementById('rzp-hero');
        const pipeEl = document.getElementById('rzp-pipe');
        if (heroEl) heroEl.style.display = 'flex';
        if (pipeEl){ pipeEl.style.display = 'none'; pipeEl.innerHTML = ''; }
        state.phase = 'hero';
      }
      document.querySelectorAll('.nl').forEach(b => b.classList.toggle('on', b.dataset.view === view));
      document.querySelectorAll('.view').forEach(s => s.style.display = s.dataset.view === view ? 'block' : 'none');
      if (view === 'studio'){
        const stab = document.querySelector('.stb[data-stab="razbor"]');
        if (stab) stab.click();
      }
    },
    refreshTab(tab){
      if (tab === 'razbor'){
        const host = document.getElementById('studio-razbor-host');
        if (host && !host.dataset.openItem) host.innerHTML = renderRazborLibrary();
      }
      if (tab === 'proc'){
        const host = document.getElementById('studio-proc-host');
        if (host) host.innerHTML = renderProcessingTab();
        // run animations on current
        window.RZP_PROCESSING.filter(p=>p.current).forEach(p => runSecondPipeline(p.id));
      }
      if (tab === 'done'){
        const host = document.getElementById('studio-done-host');
        if (host) host.innerHTML = renderReadyTab();
      }
    },
    openInStudio(itemId){
      const host = document.getElementById('studio-razbor-host');
      if (host){
        host.dataset.openItem = itemId;
        host.innerHTML = renderDoneState(itemId);
      }
      this.go('studio');
      const stab = document.querySelector('.stb[data-stab="razbor"]');
      if (stab){
        document.querySelectorAll('.stb').forEach(x=>x.classList.remove('on'));
        stab.classList.add('on');
        document.querySelectorAll('.studio-tab').forEach(x => {
          x.style.display = x.dataset.tab === 'razbor' ? 'block' : 'none';
        });
      }
      window.scrollTo({top:0, behavior:'smooth'});
    },
    backToLibrary(){
      const host = document.getElementById('studio-razbor-host');
      if (host){
        delete host.dataset.openItem;
        host.innerHTML = renderRazborLibrary();
      }
    },
    setLibraryFilter(f){
      state.libraryFilter = f;
      const host = document.getElementById('studio-razbor-host');
      if (host && !host.dataset.openItem) host.innerHTML = renderRazborLibrary();
    },
    setLibraryQuery(q){
      state.libraryQuery = q;
      const host = document.getElementById('studio-razbor-host');
      if (!host || host.dataset.openItem) return;
      // re-render grid only, keep input focus
      const grid = host.querySelector('.rzl-grid');
      const count = host.querySelector('.rzl-count');
      const lib = window.RZP_LIBRARY.filter(item => {
        const ql = q.toLowerCase();
        if (ql && !(item.author.toLowerCase().includes(ql) || item.title.toLowerCase().includes(ql))) return false;
        if (state.libraryFilter==='platform_ig') return item.platform==='INSTAGRAM';
        if (state.libraryFilter==='platform_yt') return item.platform==='YOUTUBE';
        if (state.libraryFilter==='platform_tt') return item.platform==='TIKTOK';
        if (state.libraryFilter==='high_er') return item.er > 8;
        return true;
      });
      if (grid) grid.innerHTML = lib.map(it => renderLibraryCard(it)).join('') || '<div class="studio-empty">— ничего не найдено —</div>';
      if (count) count.textContent = `${lib.length} из ${window.RZP_LIBRARY.length}`;
    },
    startNewRazbor(){
      // очищаем Разбор и возвращаемся к hero (Главная)
      const heroEl = document.getElementById('rzp-hero');
      const pipeEl = document.getElementById('rzp-pipe');
      if (heroEl) heroEl.style.display = 'flex';
      if (pipeEl){ pipeEl.style.display = 'none'; pipeEl.innerHTML = ''; }
      state.phase = 'hero';
      const inp = document.getElementById('rzp-link');
      if (inp){ inp.value = ''; inp.focus(); }
      window.scrollTo({top:0, behavior:'smooth'});
    },
    startSecondPipeline(){
      // 1) создаём новый active proc-item
      const m = window.RZP_VIDEO_META;
      const newId = 'p_'+Date.now().toString(36);
      window.RZP_PROCESSING.forEach(p => p.current = false);
      window.RZP_PROCESSING.unshift({
        id: newId,
        source: m.author,
        title: 'Адаптация: «'+(window.RZP_LIBRARY.find(x=>x.current)||{title:'—'}).title+'»',
        step: 1, total: SECOND_STEPS.length, hue: m.thumbnail_hue || 18,
        when: 'только что', current: true,
      });
      // 2) очищаем Разбор перед переходом в студию
      const heroEl = document.getElementById('rzp-hero');
      const pipeEl = document.getElementById('rzp-pipe');
      if (heroEl) heroEl.style.display = 'flex';
      if (pipeEl){ pipeEl.style.display = 'none'; pipeEl.innerHTML = ''; }
      state.phase = 'hero';
      // 3) переходим в studio · в обработке
      document.querySelectorAll('.nl').forEach(b => b.classList.toggle('on', b.dataset.view === 'studio'));
      document.querySelectorAll('.view').forEach(s => s.style.display = s.dataset.view === 'studio' ? 'block' : 'none');
      const procTab = document.querySelector('.stb[data-stab="proc"]');
      if (procTab){
        document.querySelectorAll('.stb').forEach(x=>x.classList.remove('on'));
        procTab.classList.add('on');
        document.querySelectorAll('.studio-tab').forEach(x => {
          x.style.display = x.dataset.tab === 'proc' ? 'block' : 'none';
        });
      }
      // 4) рендерим очередь и запускаем анимацию на новой активной
      const host = document.getElementById('studio-proc-host');
      if (host) host.innerHTML = renderProcessingTab();
      setTimeout(()=>runSecondPipeline(newId), 80);
      window.scrollTo({top:0, behavior:'smooth'});
    },
    replay(){
      this.go('home');
      const heroEl = document.getElementById('rzp-hero');
      const pipeEl = document.getElementById('rzp-pipe');
      if (heroEl){ heroEl.style.display = 'flex'; }
      if (pipeEl){ pipeEl.style.display = 'none'; pipeEl.innerHTML = ''; }
      const host = document.getElementById('studio-razbor-host');
      if (host){ delete host.dataset.openItem; host.innerHTML = '<div class="studio-empty">— разбор появится здесь, когда пайплайн завершится —</div>'; }
      setTimeout(()=>startPipeline(), 600);
    },
    copyText(kind){
      let txt = '';
      if (kind==='strategy') txt = window.RZP_STRATEGY_SECTIONS.map(s => s.title.toUpperCase()+'\n\n'+s.body).join('\n\n———\n\n');
      else if (kind==='transcript') txt = window.RZP_TRANSCRIPT.map(([t,l])=>t+'  '+l).join('\n');
      try { navigator.clipboard.writeText(txt); } catch(_){}
    },
    downloadText(kind){
      let txt = '', name = 'razbor.txt';
      if (kind==='strategy'){ txt = window.RZP_STRATEGY_SECTIONS.map(s => s.title.toUpperCase()+'\n\n'+s.body).join('\n\n———\n\n'); name='strategy.txt'; }
      else if (kind==='transcript'){ txt = window.RZP_TRANSCRIPT.map(([t,l])=>t+'  '+l).join('\n'); name='transcript.txt'; }
      const blob = new Blob([txt], {type:'text/plain;charset=utf-8'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
    },
    startPipeline,
  };

  document.addEventListener('DOMContentLoaded', ()=>window.RZP.boot());
})();
