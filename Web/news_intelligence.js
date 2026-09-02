/* =========================================================================
 * NEXUS CONTROL CENTER — News Intelligence (0100)
 * -------------------------------------------------------------------------
 * Vanilla-JS companion module for the News tab. Provides (no build system):
 *   - AI readiness status (secret-free) + Strategy Factory CTA
 *   - per-article AI analysis with a real state machine (idle/analyzing/done/failed)
 *   - bounded batch analysis with real progress
 *   - recoverable IRRELEVANT classification (auto-prune, Pro Mode)
 *   - Active / All / Irrelevant filtering
 *   - restore of IRRELEVANT -> ACTIVE
 *   - loading / empty / error / success states (no raw stack traces in DOM)
 *
 * All HTTP goes through window.NX.api; user-facing errors go through
 * NX.Forensic.toast + NX.Forensic.normalizeError (never "TypeError: Failed
 * to fetch" rendered to the page).
 * ========================================================================= */
window.NewsIntel = window.NewsIntel || {};

(function () {
  'use strict';

  // Single source of truth for view state (§81).
  var state = {
    aiStatus: null,        // last ai_status payload
    filter: 'ACTIVE',      // ACTIVE | ALL | IRRELEVANT
    analyzing: {},         // article_id -> true (in-flight dedup, §50)
    batchRunning: false,
    pruneRunning: false,
  };

  function el(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Friendly AI-status banner (§7). Never exposes secrets.
  function renderAIStatus() {
    var box = el('news-ai-status');
    if (!box) return;
    var s = state.aiStatus;
    if (!s) { box.innerHTML = ''; return; }
    var cls = 'ni-chip ni-chip-unknown';
    var label = 'AI UNKNOWN';
    if (s.state === 'AVAILABLE') { cls = 'ni-chip ni-chip-ready'; label = 'AI READY'; }
    else if (s.state === 'NOT_CONFIGURED') { cls = 'ni-chip ni-chip-notcfg'; label = 'AI NOT CONFIGURED'; }
    else if (s.state === 'UNAVAILABLE') { cls = 'ni-chip ni-chip-unavail'; label = 'AI UNAVAILABLE'; }
    else if (s.state === 'MISCONFIGURED') { cls = 'ni-chip ni-chip-misconfig'; label = 'AI MISCONFIGURED'; }
    var html = '<span class="' + cls + '"><i class="fa-solid fa-robot mr-1"></i>' + esc(label) + '</span>';
    if (s.provider) html += '<span class="ni-muted">provider: ' + esc(s.provider) + '</span>';
    if (s.model) html += '<span class="ni-muted">model: ' + esc(s.model) + '</span>';
    if (s.state === 'NOT_CONFIGURED' || s.state === 'MISCONFIGURED') {
      html += '<button onclick="NewsIntel.openFactoryCTA()" class="ml-1 bg-accentCyan/10 text-accentCyan border border-accentCyan/40 rounded px-2 py-0.5 text-[10px] font-bold hover:bg-accentCyan/20 transition">Configure in Strategy Factory</button>';
    }
    box.innerHTML = html;
  }

  // Load AI status (§58: refresh on tab view + reasonable cadence).
  async function loadAIStatus() {
    try {
      var res = await NX.api.get('/api/news/ai-status', { component: 'News', action: 'AI_STATUS' });
      if (res && res.ok) {
        state.aiStatus = (res.body && res.body.ai_status) || null;
      } else {
        state.aiStatus = { state: 'UNKNOWN', detail: (res && res.error && res.error.message) || '' };
      }
    } catch (e) {
      state.aiStatus = { state: 'UNKNOWN', detail: 'status check failed' };
    }
    renderAIStatus();
    renderProControls();
    return state.aiStatus;
  }

  NewsIntel.openFactoryCTA = function () {
    // Route to the Strategy Factory config UI (no separate News settings page).
    try {
      if (typeof switchTab === 'function') switchTab('tab-config');
      var sf = el('tab-config');
      if (sf) sf.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { /* noop */ }
    NX.Forensic.toast.info('Open Strategy Factory → LLM Provider to enable News AI analysis.');
  };

  // ----------------------------------------------------------------------
  // AI analysis result rendering (compact, secondary to source facts, §21/§46)
  // ----------------------------------------------------------------------
  function aiCardHTML(ai) {
    if (!ai) return '';
    var statusTag = '';
    if (ai.analysis_status === 'failed') {
      statusTag = '<span class="ni-chip ni-chip-misconfig">AI FAILED</span>';
    } else if (ai.insufficient_evidence) {
      statusTag = '<span class="ni-chip ni-chip-notcfg">INSUFFICIENT EVIDENCE</span>';
    } else if (ai.summary || ai.market_relevance) {
      statusTag = '<span class="ni-chip ni-chip-ready">AI ANALYZED</span>';
    }
    var facts = (ai.key_facts && ai.key_facts.length)
      ? '<div class="mt-1"><span class="ni-muted text-[9px] uppercase">Key facts:</span> ' +
        ai.key_facts.slice(0, 6).map(function (f) { return '<span class="ni-fact">' + esc(f) + '</span>'; }).join('; ') + '</div>'
      : '';
    var unc = (ai.uncertainties && ai.uncertainties.length)
      ? '<div class="mt-1 text-[9px] text-amber-300/80"><span class="uppercase">Uncertainties:</span> ' +
        ai.uncertainties.slice(0, 4).map(function (u) { return esc(u); }).join('; ') + '</div>'
      : '';
    var meta = '<div class="mt-1 text-[9px] ni-muted">';
    if (ai.sentiment) meta += 'Sentiment: ' + esc(ai.sentiment) + ' · ';
    if (ai.provider) meta += 'Provider: ' + esc(ai.provider) + ' · ';
    if (ai.model) meta += 'Model: ' + esc(ai.model) + ' · ';
    if (ai.analysis_version) meta += 'v' + esc(ai.analysis_version);
    meta += '</div>';
    return '<div class="ni-ai-card rounded p-2 mt-1.5 text-[10px]">' +
      statusTag +
      (ai.summary ? '<div class="mt-1 ni-fact">' + esc(ai.summary) + '</div>' : '') +
      (ai.market_relevance ? '<div class="mt-0.5 ni-muted"><span class="uppercase">Market rel:</span> ' + esc(ai.market_relevance) + '</div>' : '') +
      (ai.xauusd_relevance ? '<div class="ni-muted"><span class="uppercase">XAUUSD rel:</span> ' + esc(ai.xauusd_relevance) + '</div>' : '') +
      (ai.potential_market_impact ? '<div class="ni-muted"><span class="uppercase">Impact:</span> ' + esc(ai.potential_market_impact) + '</div>' : '') +
      facts + unc + meta +
      '</div>';
  }

  // ----------------------------------------------------------------------
  // Per-article AI analysis (state machine idle/analyzing/done/failed, §20)
  // ----------------------------------------------------------------------
  NewsIntel.analyzeArticle = function (articleId) {
    if (state.analyzing[articleId]) { return; } // dedup in-flight (§50)
    state.analyzing[articleId] = true;
    var btn = el('ni-analyze-' + articleId);
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="ni-spinner"></span> Analyzing…'; }
    NX.api.post('/api/news/analyze/' + encodeURIComponent(articleId), {}, { component: 'News', action: 'ANALYZE' })
      .then(function (res) {
        if (!res || !res.ok) {
          var n = NX.Forensic.normalizeError(res, { component: 'News', action: 'ANALYZE', endpoint: '/api/news/analyze' });
          NX.Forensic.toast.error(n.message, { detail: n.detail });
          return;
        }
        NX.Forensic.toast.success('AI analysis complete.');
        // refresh the feed to show the result (server owns state).
        if (typeof loadNewsFeed === 'function') loadNewsFeed();
      })
      .catch(function (e) {
        var n = NX.Forensic.normalizeError(e, { component: 'News', action: 'ANALYZE' });
        NX.Forensic.toast.error(n.message, { detail: n.detail });
      })
      .finally(function () {
        state.analyzing[articleId] = false;
        if (btn) { btn.disabled = false; btn.innerHTML = 'Analyze with AI'; }
      });
  };

  NewsIntel.reAnalyze = function (articleId) {
    NX.api.post('/api/news/analyze/' + encodeURIComponent(articleId) + '?force=true', {}, { component: 'News', action: 'REANALYZE' })
      .then(function (res) {
        if (!res || !res.ok) {
          var n = NX.Forensic.normalizeError(res, { component: 'News', action: 'REANALYZE' });
          NX.Forensic.toast.error(n.message, { detail: n.detail });
          return;
        }
        NX.Forensic.toast.success('AI re-analysis complete.');
        if (typeof loadNewsFeed === 'function') loadNewsFeed();
      })
      .catch(function (e) {
        var n = NX.Forensic.normalizeError(e, { component: 'News', action: 'REANALYZE' });
        NX.Forensic.toast.error(n.message, { detail: n.detail });
      });
  };

  // ----------------------------------------------------------------------
  // Filters (§35): Active / All / Irrelevant. Default Active.
  // ----------------------------------------------------------------------
  NewsIntel.setNewsFilter = function (f) {
    state.filter = f;
    var btns = document.querySelectorAll('.news-filter-btn');
    btns.forEach(function (b) {
      var v = b.getAttribute('data-news-filter');
      if (v === f) b.classList.add('is-active'); else b.classList.remove('is-active');
    });
    if (typeof loadNewsFeed === 'function') loadNewsFeed();
  };

  function renderStatusCounts(counts) {
    var box = el('news-status-counts');
    if (!box) return;
    if (!counts) { box.textContent = ''; return; }
    var parts = [];
    if (counts.ACTIVE != null) parts.push('Active ' + counts.ACTIVE);
    if (counts.IRRELEVANT != null) parts.push('Irrelevant ' + counts.IRRELEVANT);
    box.textContent = parts.join(' · ');
  }
  NewsIntel.renderStatusCounts = renderStatusCounts;

  // ----------------------------------------------------------------------
  // Pro Mode controls: Auto-prune (§25/§31/§33). Backend-enforced by the
  // news subsystem being enabled. UI shows the control always but routes to
  // the news toggle if the subsystem is off.
  // ----------------------------------------------------------------------
  function renderProControls() {
    var box = el('news-pro-controls');
    if (!box) return;
    var html = '' +
      '<span class="text-[9px] uppercase tracking-widest text-textMuted">Pro:</span>' +
      '<button id="news-autoprune-btn" onclick="NewsIntel.autoPrune()" class="bg-amber-500/10 text-amber-300 border border-amber-500/40 rounded px-2.5 py-1 text-[10px] font-bold hover:bg-amber-500/20 transition">Hide unrelated (auto-prune)</button>' +
      '<button id="news-batch-btn" onclick="NewsIntel.batchAnalyze()" class="bg-accentCyan/10 text-accentCyan border border-accentCyan/40 rounded px-2.5 py-1 text-[10px] font-bold hover:bg-accentCyan/20 transition">AI-analyze visible</button>';
    box.innerHTML = html;
  }

  NewsIntel.autoPrune = function () {
    if (state.pruneRunning) { return; }
    // Confirmation (§33): explain recoverability, type-to-confirm not required
    // but an explicit modal confirm is used (no fake success).
    var ok = window.confirm(
      'Mark unrelated News as Irrelevant?\n\n' +
      'Original articles are preserved and remain recoverable (status: IRRELEVANT). ' +
      'This uses importance + XAUUSD relevance, not a blunt "not gold => delete".'
    );
    if (!ok) { return; }
    state.pruneRunning = true;
    var btn = el('news-autoprune-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="ni-spinner"></span> Pruning…'; }
    NX.api.post('/api/news/auto-prune', { actor: 'pro_user' }, { component: 'News', action: 'AUTO_PRUNE' })
      .then(function (res) {
        if (!res || !res.ok) {
          var n = NX.Forensic.normalizeError(res, { component: 'News', action: 'AUTO_PRUNE' });
          NX.Forensic.toast.error(n.message, { detail: n.detail });
          return;
        }
        var r = res.body || {};
        NX.Forensic.toast.success(
          'Pruning complete: ' + (r.marked_irrelevant || 0) + ' marked irrelevant, ' +
          (r.preserved || 0) + ' preserved.',
          { detail: 'already irrelevant: ' + (r.already_irrelevant || 0) }
        );
        if (typeof loadNewsFeed === 'function') loadNewsFeed();
      })
      .catch(function (e) {
        var n = NX.Forensic.normalizeError(e, { component: 'News', action: 'AUTO_PRUNE' });
        NX.Forensic.toast.error(n.message, { detail: n.detail });
      })
      .finally(function () {
        state.pruneRunning = false;
        if (btn) { btn.disabled = false; btn.innerHTML = 'Hide unrelated (auto-prune)'; }
      });
  };

  NewsIntel.batchAnalyze = function () {
    if (state.batchRunning) { return; }
    // Gather currently-visible article ids from the feed DOM (no separate store).
    var feed = el('news-feed');
    if (!feed) return;
    var ids = Array.prototype.slice.call(feed.querySelectorAll('[data-article-id]'))
      .map(function (n) { return n.getAttribute('data-article-id'); })
      .filter(Boolean);
    if (!ids.length) { NX.Forensic.toast.info('No visible articles to analyze.'); return; }
    state.batchRunning = true;
    var btn = el('news-batch-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="ni-spinner"></span> Analyzing ' + ids.length + '…'; }
    NX.api.post('/api/news/analyze/batch', { article_ids: ids }, { component: 'News', action: 'BATCH_ANALYZE' })
      .then(function (res) {
        if (!res || !res.ok) {
          var n = NX.Forensic.normalizeError(res, { component: 'News', action: 'BATCH_ANALYZE' });
          NX.Forensic.toast.error(n.message, { detail: n.detail });
          return;
        }
        var r = res.body || {};
        NX.Forensic.toast.success(
          'Batch AI analysis: ' + (r.completed || 0) + ' completed, ' + (r.failed || 0) + ' failed.',
          { detail: (r.skipped || 0) + ' skipped (existing)' }
        );
        if (typeof loadNewsFeed === 'function') loadNewsFeed();
      })
      .catch(function (e) {
        var n = NX.Forensic.normalizeError(e, { component: 'News', action: 'BATCH_ANALYZE' });
        NX.Forensic.toast.error(n.message, { detail: n.detail });
      })
      .finally(function () {
        state.batchRunning = false;
        if (btn) { btn.disabled = false; btn.innerHTML = 'AI-analyze visible'; }
      });
  };

  NewsIntel.restoreArticle = function (articleId) {
    NX.api.post('/api/news/' + encodeURIComponent(articleId) + '/restore', {}, { component: 'News', action: 'RESTORE' })
      .then(function (res) {
        if (!res || !res.ok) {
          var n = NX.Forensic.normalizeError(res, { component: 'News', action: 'RESTORE' });
          NX.Forensic.toast.error(n.message, { detail: n.detail });
          return;
        }
        NX.Forensic.toast.success('Article restored to Active.');
        if (typeof loadNewsFeed === 'function') loadNewsFeed();
      })
      .catch(function (e) {
        var n = NX.Forensic.normalizeError(e, { component: 'News', action: 'RESTORE' });
        NX.Forensic.toast.error(n.message, { detail: n.detail });
      });
  };

  // ----------------------------------------------------------------------
  // Article card builder (used by app.js loadNewsFeed to enrich each row).
  // Returns an HTML string appended after the source-fact summary.
  // ----------------------------------------------------------------------
  NewsIntel.articleExtrasHTML = function (a) {
    var html = '';
    var status = (a.article_status || 'ACTIVE');
    if (status === 'IRRELEVANT') {
      html += '<span class="ni-chip ni-status-irrelevant">IRRELEVANT</span> ';
      html += '<button onclick="NewsIntel.restoreArticle(\'' + esc(a.article_id) + '\')" class="text-[9px] bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 rounded px-1.5 py-0.5 hover:bg-emerald-500/20">Restore</button> ';
    } else {
      html += '<span class="ni-chip ni-status-active">ACTIVE</span> ';
    }
    // AI analysis action + result.
    if (a.ai_analysis && (a.ai_analysis.summary || a.ai_analysis.analysis_status === 'failed')) {
      html += '<button onclick="NewsIntel.reAnalyze(\'' + esc(a.article_id) + '\')" class="text-[9px] bg-accentCyan/10 text-accentCyan border border-accentCyan/30 rounded px-2 py-0.5 hover:bg-accentCyan/20">Re-analyze</button>';
      html += aiCardHTML(a.ai_analysis);
    } else {
      html += '<button id="ni-analyze-' + esc(a.article_id) + '" onclick="NewsIntel.analyzeArticle(\'' + esc(a.article_id) + '\')" class="text-[9px] bg-accentCyan/10 text-accentCyan border border-accentCyan/30 rounded px-2 py-0.5 hover:bg-accentCyan/20">Analyze with AI</button>';
    }
    return html;
  };


  // ----------------------------------------------------------------------
  // PRO AUTO CONSOLE — live pass/answer/error from /api/news/pro/* (News Tab)
  // Polls /api/news/pro/console?since_seq=... on a 1.5s interval when the
  // News tab is visible. Every route answer and worker pass is traced here.
  // ----------------------------------------------------------------------
  var _proSeq = 0;
  var _proTimer = null;
  var _proRunning = false;

  function _proEsc(s){ return esc(s); }
  function _proKindLabel(kind){
    var map = { cycle_start:'CYCLE', cycle_done:'DONE', analysis_ok:'ANALYSIS', ai_ok:'LLM ANSWER', ai_failed:'LLM FAIL', fallback:'FALLBACK', skip:'SKIP', error:'ERROR', junk_prune:'JUNK', purge:'PURGE', junk_failed:'JUNK FAIL', analysis_failed:'FAIL' };
    return map[kind] || String(kind||'').toUpperCase();
  }
  function _proRow(e){
    var kind = e.kind || 'log';
    var label = _proKindLabel(kind);
    var ts = (e.ts||'').slice(11,19);
    var color = 'text-slate-300';
    if(kind==='ai_ok') color='text-emerald-300';
    else if(kind==='cycle_start' || kind==='cycle_done') color='text-cyan-300';
    else if(kind==='error' || kind==='analysis_failed' || kind==='ai_failed') color='text-red-300';
    else if(kind==='junk_prune' || kind==='purge') color='text-amber-300';
    var msg = e.msg || e.summary || '';
    var extra = '';
    if(e.answer) extra = ' \u00b7 <span class="text-cyan-200">' + _proEsc(JSON.stringify(e.answer).slice(0,220)) + '</span>';
    if(e.via) extra += ' <span class="text-slate-500">via:' + _proEsc(e.via) + '</span>';
    if(e.article_id) extra += ' <span class="text-slate-500">#' + _proEsc(String(e.article_id).slice(0,10)) + '</span>';
    if(e.sentiment) extra += ' <span class="text-amber-200">' + _proEsc(e.sentiment) + '</span>';
    if(e.provider) extra += ' <span class="text-slate-500">' + _proEsc(e.provider) + '</span>';
    return '<div class="flex gap-2 ' + color + '"><span class="text-slate-500 shrink-0">' + _proEsc(ts) + '</span><span class="font-black shrink-0">' + _proEsc(label) + '</span><span class="min-w-0 break-words whitespace-normal line-clamp-2 flex-1" title="' + _proEsc(String(msg)) + '">' + _proEsc(String(msg).slice(0,260)) + extra + '</span></div>';
  }
  function _appendProEntries(entries){
    var log = document.getElementById('news-pro-console-log');
    if(!log || !entries || !entries.length) return;
    if(log.children.length===1 && /Console idle|Polling console/.test(log.innerHTML)) log.innerHTML='';
    entries.forEach(function(e){
      _proSeq = Math.max(_proSeq, Number(e.seq||0));
      var row = document.createElement('div');
      row.innerHTML = _proRow(e);
      log.appendChild(row.firstChild || row);
    });
    while(log.children.length > 400) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }
  async function _pollProConsole(){
    try{
      var res = await NX.api.get('/api/news/pro/console?limit=200&since_seq=' + _proSeq, { component:'News', action:'PRO_CONSOLE' });
      if(res && res.ok && res.body && Array.isArray(res.body.entries) && res.body.entries.length){
        _appendProEntries(res.body.entries);
      }
    }catch(_){}
  }
  async function _pollProStatus(){
    try{
      var res = await NX.api.get('/api/news/pro/status', { component:'News', action:'PRO_STATUS' });
      if(!res || !res.ok || !res.body) return;
      var c = res.body.counts, prov = res.body.provider, last = res.body.latest_ai;
      var pend = document.getElementById('news-pro-pending'), tot = document.getElementById('news-pro-total'), sc = document.getElementById('news-pro-status-counts'), pv = document.getElementById('news-pro-console-provider'), badge = document.getElementById('news-pro-console-badge'), pend2 = document.getElementById('news-pro-console-pending');
      if(pend) pend.textContent = String(c.pending);
      if(tot) tot.textContent = String(c.total);
      if(sc) sc.textContent = JSON.stringify(c.status_counts||{});
      if(pv && prov) pv.textContent = prov.provider_available ? (prov.provider_name + ' ' + prov.model) : (prov.ai_status && prov.ai_status.state ? prov.ai_status.state : 'LLM unavailable -> local fallback');
      if(badge){
        var enabled = null;
        try{ var tgl = document.getElementById('news-auto-toggle'); if(tgl) enabled = !!tgl.checked; }catch(_){}
        badge.textContent = (enabled ? 'AUTO ON' : 'AUTO OFF') + ' \u00b7 ' + (prov && prov.provider_available ? 'LLM' : 'LOCAL');
        badge.className = 'text-[9px] font-mono px-1.5 py-0.5 rounded border ' + (enabled ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' : 'bg-slate-500/10 text-slate-400 border-slate-500/20');
      }
      if(pend2) pend2.textContent = c.pending ? (c.pending + ' pending') : '';
      var lastBox = document.getElementById('news-pro-last-answer');
      if(lastBox && last){ var _t=(last.summary||''); lastBox.textContent=_t.slice(0,180)+(last.sentiment?' \u00b7 '+last.sentiment:''); lastBox.title=_t; }
      var alist = document.getElementById('news-pro-answers-list');
      if(alist){
        var r2 = await NX.api.get('/api/news/pro/latest-answers?limit=8', { component:'News', action:'PRO_ANSWERS' });
        if(r2 && r2.ok && r2.body && Array.isArray(r2.body.answers) && r2.body.answers.length){
          alist.innerHTML = r2.body.answers.map(function(a){
            return '<div class="border border-borderClr/30 rounded px-2 py-1 bg-darkBg/30"><div class="text-slate-200">' + _proEsc((a.summary||'').slice(0,160)) + '</div><div class="text-slate-500">' + _proEsc(a.sentiment||'') + ' \u00b7 ' + _proEsc(a.provider||'') + ' ' + _proEsc(a.model||'') + ' \u00b7 ' + _proEsc((a.analyzed_at||'').slice(0,19)) + '</div></div>';
          }).join('');
        }
      }
    }catch(_){}
  }
  NewsIntel.startProConsole = function(){
    if(_proTimer) clearInterval(_proTimer);
    _proSeq = 0;
    var log = document.getElementById('news-pro-console-log'); if(log) log.innerHTML = '<div class="text-slate-500 italic">Polling console... every pass and LLM answer will appear here (route: /api/news/pro/console).</div>';
    _pollProConsole(); _pollProStatus();
    _proTimer = setInterval(function(){
      var tab = document.getElementById('tab-news');
      if(tab && tab.classList.contains('hidden')) return;
      _pollProConsole(); _pollProStatus();
    }, 1500);
  };
  NewsIntel.stopProConsole = function(){ if(_proTimer) clearInterval(_proTimer); _proTimer=null; };
  NewsIntel.refreshProStatus = function(){ _pollProStatus(); _pollProConsole(); };
  NewsIntel.clearProConsole = function(){ var log=document.getElementById('news-pro-console-log'); if(log) log.innerHTML='<div class="text-slate-500 italic">Cleared.</div>'; _proSeq=0; };
  NewsIntel.proAnalyzeAll = async function(){
    var btn = document.getElementById('news-pro-analyze-all');
    if(_proRunning) return;
    _proRunning=true; if(btn){ btn.disabled=true; btn.textContent='Running...'; }
    try{
      var res = await NX.api.post('/api/news/pro/analyze-all', { limit: 200 }, { component:'News', action:'PRO_ANALYZE_ALL' });
      if(!res || !res.ok){ var n=NX.Forensic.normalizeError(res,{component:'News',action:'PRO_ANALYZE_ALL'}); NX.Forensic.toast.error(n.message,{detail:n.detail}); }
      else { NX.Forensic.toast.success('PRO drain complete \u2014 see console.'); if(typeof loadNewsFeed==='function') loadNewsFeed(); }
    }catch(e){ var n=NX.Forensic.normalizeError(e,{component:'News',action:'PRO_ANALYZE_ALL'}); NX.Forensic.toast.error(n.message,{detail:n.detail}); }
    finally{ _proRunning=false; if(btn){ btn.disabled=false; btn.textContent='Analyze ALL'; } _pollProConsole(); _pollProStatus(); }
  };
  NewsIntel.proPurge = async function(hard){
    try{
      var res = await NX.api.post('/api/news/pro/purge', { hard_delete: !!hard, limit: 5000 }, { component:'News', action:'PRO_PURGE' });
      if(!res || !res.ok){ var n=NX.Forensic.normalizeError(res,{component:'News',action:'PRO_PURGE'}); NX.Forensic.toast.error(n.message,{detail:n.detail}); return; }
      var b=res.body||{}; NX.Forensic.toast.success(hard ? ('Hard purge: deleted '+(b.deleted||0)) : ('IRRELEVANT: '+(b.total_irrelevant||0)+' (candidates '+(b.candidates||0)+')'));
      _pollProStatus(); _pollProConsole(); if(typeof loadNewsFeed==='function') loadNewsFeed();
    }catch(e){ var n=NX.Forensic.normalizeError(e,{component:'News',action:'PRO_PURGE'}); NX.Forensic.toast.error(n.message,{detail:n.detail}); }
  };


  // Auto-kick PRO console if user lands/refreshes on News tab (not only on switchTab)
  (function(){
    try{
      var kick = function(){
        var tab=document.getElementById('tab-news');
        if(tab && !tab.classList.contains('hidden') && NewsIntel.startProConsole) NewsIntel.startProConsole();
      };
      if(document.readyState !== 'loading') setTimeout(kick, 600);
      else document.addEventListener('DOMContentLoaded', function(){ setTimeout(kick, 700); });
    }catch(_){}
  })();

  NewsIntel.state = state;
})();

