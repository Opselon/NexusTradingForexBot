/* ==========================================================================
 * Nexus Scalp Engine — Command Palette + Keyboard Shortcuts (NXPalette) · CHG-0048
 * --------------------------------------------------------------------------
 * Agent: Nexus-Main (UX). Presentation-layer ONLY.
 *
 * Ctrl/Cmd+K opens a searchable command palette (brief §38):
 *   - Navigation: every existing tab (reuses the app's own switchTab).
 *   - Actions: refresh data, run health, scroll-to-signal, toggle pause.
 *   - Help: shortcut cheat sheet.
 *
 * SAFETY (brief §38): the palette NEVER executes dangerous commands.
 * Mode switching / engine stop are deliberately absent — they must go
 * through their confirmation gates. Accidental keyboard input can only
 * navigate or refresh.
 *
 * Shortcuts: Ctrl/Cmd+K palette · Esc close · Alt+1..4 focus tabs ·
 * R refresh (only when no input focused).
 * State: last active tab restored via localStorage (safe UI state only,
 * never restores a runtime action — brief §40).
 * ========================================================================== */
(function (global) {
    'use strict';

    var open = false;
    var commands = [];
    var filtered = [];
    var selected = 0;
    var paletteEl = null;

    function t(key, fallback, vars) {
        var i = global.NX_I18N;
        var s = i ? i.t(key, fallback) : fallback;
        if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
        return s;
    }

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ---- commands (navigation uses the app's own switchTab if present) -----
    var TABS = [
        ['tab-monitoring',   'Live Monitoring',          'fa-chart-line'],
        ['tab-account',      'Account Center',           'fa-wallet'],
        ['tab-ai-analysis',  'AI Intel Hub',             'fa-brain'],
        ['tab-research',     'Strategy Research',        'fa-flask-vial'],
        ['tab-factory',      'Strategy Factory',         'fa-industry'],
        ['tab-news',         'News Intelligence',        'fa-newspaper'],
        ['tab-liquidity',    'Liquidity Intelligence',   'fa-water'],
        ['tab-rules',        'Scalping Rules',           'fa-shield-halved'],
        ['tab-config',       'Bot Settings',             'fa-sliders'],
        ['tab-health',       'System Health',            'fa-heart-pulse'],
        ['tab-debug',        'Debug & Diagnostics',      'fa-bug'],
        ['tab-governance',   'Model Governance',         'fa-scale-balanced'],
        ['tab-incidents',    'Forensic Incident Center', 'fa-triangle-exclamation'],
        ['tab-database',     'Database Management',      'fa-database']
    ];

    function buildCommands() {
        commands = [];
        TABS.forEach(function (tb) {
            commands.push({
                id: 'nav:' + tb[0],
                group: t('ux.palette.group.nav', 'Navigation'),
                icon: tb[2],
                label: tb[1],
                keywords: tb[0] + ' ' + tb[1],
                run: function () { goTab(tb[0]); }
            });
        });
        commands.push(
            { id: 'act:refresh', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-rotate-right',
              label: t('ux.action.refresh', 'Refresh data'), keywords: 'refresh reload data snapshot',
              run: function () { if (typeof fetchSystemSnapshot === 'function') fetchSystemSnapshot(); if (global.NX) global.NX.toast(t('ux.action.refresh', 'Refresh data') + '…', 'info', { ttl: 1500 }); } },
            { id: 'act:health', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-heart-pulse',
              label: t('ux.action.goto_health', 'System health'), keywords: 'health ready degraded status',
              run: function () { goTab('tab-health'); } },
            { id: 'act:signal', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-bullseye',
              label: t('ux.action.goto_signals', 'Current signal'), keywords: 'signal decision ai buy sell no_trade',
              run: function () {
                  goTab('tab-monitoring');
                  setTimeout(function () {
                      var el = document.getElementById('ai-decision-badge');
                      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }, 150);
              } },
            { id: 'act:positions', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-briefcase',
              label: t('ux.action.goto_positions', 'Positions'), keywords: 'positions open trades exposure',
              run: function () { goTab('tab-account'); } },
            { id: 'act:diagnostics', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-bug',
              label: t('ux.action.goto_diagnostics', 'Diagnostics & debug'), keywords: 'debug diagnostics logs runtime',
              run: function () { goTab('tab-debug'); } },
            { id: 'act:replay', group: t('ux.palette.group.actions', 'Actions'), icon: 'fa-clock-rotate-left',
              label: t('ux.action.goto_replay', 'Replay'), keywords: 'replay history backtest',
              run: function () { goTab('tab-monitoring'); } },
            { id: 'help:shortcuts', group: t('ux.palette.group.help', 'Help'), icon: 'fa-keyboard',
              label: t('ux.shortcut.help', 'Keyboard shortcuts'), keywords: 'keys shortcuts help kbd',
              run: function () { showShortcutHelp(); } }
        );
    }

    function goTab(tabId) {
        var btn = document.querySelector('.tab-btn[onclick*="' + tabId + '"]');
        if (typeof switchTab === 'function') {
            switchTab(tabId, btn || undefined);
            try { global.localStorage && global.localStorage.setItem('nexus.ui.tab', tabId); } catch (e) { /* ignore */ }
        }
    }

    function showShortcutHelp() {
        if (global.NX && global.NX.confirmTyped) {
            global.NX.confirmTyped({
                title: t('ux.shortcut.help', 'Keyboard shortcuts'),
                body: '<div class="text-xs space-y-1.5 font-mono">' +
                    '<div><b>Ctrl/Cmd + K</b> — ' + t('ux.palette.placeholder', 'Command palette') + '</div>' +
                    '<div><b>Alt + 1..4</b> — Monitoring / Account / AI Hub / Health</div>' +
                    '<div><b>R</b> — ' + t('ux.action.refresh', 'Refresh data') + ' (when not typing)</div>' +
                    '<div><b>Esc</b> — close dialogs</div></div>',
                confirmLabel: 'OK'
            });
        }
    }

    // ---- palette DOM --------------------------------------------------------
    function ensurePalette() {
        if (paletteEl && document.body.contains(paletteEl)) return;
        paletteEl = document.createElement('div');
        paletteEl.id = 'ux-palette';
        paletteEl.className = 'fixed inset-0 z-[9996] hidden items-start justify-center pt-[12vh] bg-black/50 backdrop-blur-sm';
        paletteEl.innerHTML =
            '<div class="w-full max-w-xl mx-4 bg-panelBg border border-borderClr rounded-xl shadow-2xl overflow-hidden" role="dialog" aria-modal="true" aria-label="Command palette">' +
            '<input id="ux-palette-input" type="text" autocomplete="off" spellcheck="false" ' +
            'class="w-full bg-darkBg border-b border-borderClr px-4 py-3.5 text-sm text-white focus:outline-none" />' +
            '<div id="ux-palette-list" class="max-h-80 overflow-y-auto py-1" role="listbox"></div>' +
            '<div class="px-4 py-2 border-t border-borderClr text-[10px] text-textMuted flex justify-between">' +
            '<span>' + t('ux.palette.hint', '↑↓ navigate · Enter run · Esc close') + '</span>' +
            '<span class="font-mono">Ctrl/Cmd+K</span></div></div>';
        document.body.appendChild(paletteEl);
        var input = document.getElementById('ux-palette-input');
        input.setAttribute('aria-label', t('ux.palette.placeholder', 'Search commands…'));
        input.addEventListener('input', function () { filter(input.value); });
        paletteEl.addEventListener('mousedown', function (e) { if (e.target === paletteEl) close(); });
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
            else if (e.key === 'Enter') { e.preventDefault(); runSelected(); }
            else if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
    }

    function filter(q) {
        q = (q || '').trim().toLowerCase();
        filtered = !q ? commands.slice(0, 12) : commands.filter(function (c) {
            return (c.label + ' ' + c.keywords + ' ' + c.group).toLowerCase().indexOf(q) !== -1;
        }).slice(0, 12);
        selected = 0;
        renderList();
    }

    function renderList() {
        var list = document.getElementById('ux-palette-list');
        if (!list) return;
        if (!filtered.length) {
            list.innerHTML = '<div class="px-4 py-6 text-sm text-textMuted italic">' + esc(t('ux.palette.empty', 'No results')) + '</div>';
            return;
        }
        var html = '';
        var lastGroup = null;
        filtered.forEach(function (c, i) {
            if (c.group !== lastGroup) {
                html += '<div class="px-4 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-textMuted">' + esc(c.group) + '</div>';
                lastGroup = c.group;
            }
            html += '<div role="option" aria-selected="' + (i === selected) + '" data-idx="' + i + '" ' +
                'class="ux-palette-item flex items-center gap-3 px-4 py-2.5 cursor-pointer text-sm ' +
                (i === selected ? 'bg-accentCyan/10 text-white' : 'text-gray-300') + '">' +
                '<i class="fa-solid ' + c.icon + ' w-4 text-accentCyan" aria-hidden="true"></i>' +
                '<span>' + esc(c.label) + '</span></div>';
        });
        list.innerHTML = html;
        list.querySelectorAll('.ux-palette-item').forEach(function (row) {
            row.addEventListener('click', function () { selected = parseInt(row.getAttribute('data-idx'), 10); runSelected(); });
            row.addEventListener('mousemove', function () {
                var i = parseInt(row.getAttribute('data-idx'), 10);
                if (i !== selected) { selected = i; renderList(); }
            });
        });
    }

    function move(dir) {
        if (!filtered.length) return;
        selected = (selected + dir + filtered.length) % filtered.length;
        renderList();
    }

    function runSelected() {
        var c = filtered[selected];
        if (!c) return;
        close();
        c.run();
    }

    function show() {
        buildCommands();
        ensurePalette();
        open = true;
        paletteEl.classList.remove('hidden');
        paletteEl.classList.add('flex');
        var input = document.getElementById('ux-palette-input');
        input.value = '';
        filter('');
        setTimeout(function () { input.focus(); }, 20);
    }

    function close() {
        open = false;
        if (paletteEl) {
            paletteEl.classList.add('hidden');
            paletteEl.classList.remove('flex');
        }
    }

    // ---- global keys --------------------------------------------------------
    var NAV_ALTS = { '1': 'tab-monitoring', '2': 'tab-account', '3': 'tab-ai-analysis', '4': 'tab-health' };

    document.addEventListener('keydown', function (e) {
        var mod = e.ctrlKey || e.metaKey;
        if (mod && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            open ? close() : show();
            return;
        }
        if (open) return; // palette input handles its own keys
        if (e.altKey && NAV_ALTS[e.key]) {
            var el = document.activeElement;
            var typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
            if (!typing) { e.preventDefault(); goTab(NAV_ALTS[e.key]); }
            return;
        }
        if ((e.key === 'r' || e.key === 'R') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
            var el2 = document.activeElement;
            var typing2 = el2 && (el2.tagName === 'INPUT' || el2.tagName === 'TEXTAREA' || el2.isContentEditable || el2.tagName === 'SELECT');
            if (!typing2 && typeof fetchSystemSnapshot === 'function') {
                fetchSystemSnapshot();
                if (global.NX) global.NX.toast(t('ux.action.refresh', 'Refresh data') + '…', 'info', { ttl: 1200 });
            }
        }
    });

    // ---- last-tab restore (safe UI state only) ------------------------------
    document.addEventListener('DOMContentLoaded', function () {
        try {
            var tab = global.localStorage && global.localStorage.getItem('nexus.ui.tab');
            if (tab && tab !== 'tab-monitoring' && typeof switchTab === 'function' &&
                document.getElementById(tab) && document.querySelector('.tab-btn[onclick*="' + tab + '"]')) {
                goTab(tab);
            }
        } catch (e) { /* storage unavailable */ }
    });

    global.NXPalette = { open: show, close: close, isOpen: function () { return open; }, help: showShortcutHelp };
})(window);
