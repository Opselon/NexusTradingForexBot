"""Insert the replay panel + script tag into Web/index.html (CHG-0043)."""
from pathlib import Path

p = Path('Web/index.html')
s = p.read_text(encoding='utf-8')

PANEL = r'''
                    <!-- CHG-0043: TRUE REPLAY-ON-CHART (engine-backed session) -->
                    <div id="replay-panel" class="bg-panelBg border border-accentCyan/20 rounded-xl p-4 shadow-xl col-span-2 mt-4 lg:col-span-3">
                        <h3 class="text-sm font-bold text-white mb-1 flex items-center justify-between">
                            <span><i class="fa-solid fa-clock-rotate-left mr-1.5 text-accentCyan"></i> Historical Replay Session (ENGINE-BACKED)</span>
                            <span id="rp-msg" class="text-[10px] px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">idle</span>
                        </h3>
                        <p class="text-xs text-textMuted mb-3 leading-relaxed">
                            Runs the REAL decision pipeline (causal features &rarr; 70D &rarr; model &rarr; policy &rarr; risk &rarr; simulated fills) over local historical data through a logical replay clock. The chart cursor separates KNOWN from UNKNOWN &mdash; future candles never enter decision state.
                        </p>
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div class="space-y-2">
                                <div class="text-[10px] font-bold text-textMuted uppercase tracking-wider">Contract</div>
                                <div class="flex space-x-2">
                                    <input id="rp-start" type="datetime-local" class="bg-darkBg border border-borderClr text-white text-xs rounded px-2 py-1.5 flex-1 focus:outline-none focus:border-accentCyan">
                                    <input id="rp-end" type="datetime-local" class="bg-darkBg border border-borderClr text-white text-xs rounded px-2 py-1.5 flex-1 focus:outline-none focus:border-accentCyan">
                                </div>
                                <div class="flex items-center space-x-2">
                                    <button id="rp-create" class="bg-accentCyan hover:bg-cyan-500 text-black font-bold py-1.5 px-3 rounded text-xs transition">
                                        <i class="fa-solid fa-database mr-1"></i> Create Session
                                    </button>
                                    <label class="text-[10px] text-textMuted flex items-center space-x-1"><input id="rp-regime" type="checkbox" class="accent-cyan-500"> causal regime</label>
                                </div>
                            </div>
                            <div class="space-y-2">
                                <div class="text-[10px] font-bold text-textMuted uppercase tracking-wider">Transport</div>
                                <div class="flex flex-wrap items-center gap-1.5">
                                    <button id="rp-play" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 font-bold py-1.5 px-3 rounded text-xs border border-emerald-500/30 transition"><i class="fa-solid fa-play"></i> Play</button>
                                    <button id="rp-step" class="bg-darkBg hover:bg-panelBg text-white font-bold py-1.5 px-3 rounded text-xs border border-borderClr transition"><i class="fa-solid fa-forward-step"></i> Step</button>
                                    <button id="rp-reset" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 font-bold py-1.5 px-3 rounded text-xs border border-rose-500/30 transition"><i class="fa-solid fa-rotate-left"></i> Reset</button>
                                    <select id="rp-speed" class="bg-darkBg border border-borderClr text-white text-xs rounded px-1.5 py-1.5">
                                        <option value="1">1x</option><option value="5">5x</option><option value="10">10x</option><option value="50">50x</option>
                                    </select>
                                </div>
                                <div class="flex space-x-2">
                                    <input id="rp-seek" type="datetime-local" class="bg-darkBg border border-borderClr text-white text-xs rounded px-2 py-1.5 flex-1">
                                    <button id="rp-seek-btn" class="bg-darkBg hover:bg-panelBg text-white font-bold py-1.5 px-3 rounded text-xs border border-borderClr transition">Seek</button>
                                </div>
                            </div>
                            <div class="space-y-2">
                                <div class="text-[10px] font-bold text-textMuted uppercase tracking-wider">Cursor State</div>
                                <div class="grid grid-cols-3 gap-x-3 gap-y-1 text-xs">
                                    <span class="text-textMuted">Clock</span><span id="rp-clock" class="font-mono text-white col-span-2">&mdash;</span>
                                    <span class="text-textMuted">Price</span><span id="rp-price" class="font-mono text-accentGold">&mdash;</span>
                                    <span class="text-textMuted">Bars</span><span id="rp-bars" class="font-mono text-white">&mdash;</span>
                                    <span class="text-textMuted">Decisions</span><span id="rp-decisions" class="font-mono text-white">&mdash;</span>
                                    <span class="text-textMuted">KNOWN</span><span id="rp-known" class="font-mono text-emerald-400">&mdash;</span>
                                    <span class="text-textMuted">UNKNOWN</span><span id="rp-unknown" class="font-mono text-slate-400">&mdash;</span>
                                    <span class="text-textMuted">Equity</span><span id="rp-equity" class="font-mono text-white">&mdash;</span>
                                    <span class="text-textMuted">Position</span><span id="rp-position" class="font-mono text-white">&mdash;</span>
                                    <span class="text-textMuted">Regime</span><span id="rp-regime-now" class="font-mono text-white">&mdash;</span>
                                </div>
                            </div>
                        </div>
                        <div class="mt-3 pt-3 border-t border-borderClr grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div class="md:col-span-2">
                                <div class="flex items-center space-x-2 mb-1.5">
                                    <span class="text-[10px] font-bold text-textMuted uppercase tracking-wider">Decision drill-down (engine truth)</span>
                                    <input id="rp-decision-seq" type="number" min="1" value="1" class="bg-darkBg border border-borderClr text-white text-xs rounded px-2 py-1 w-20">
                                    <button id="rp-decision-load" class="bg-darkBg hover:bg-panelBg text-white font-bold py-1 px-3 rounded text-xs border border-borderClr transition">Load</button>
                                </div>
                                <div id="rp-decision-box" class="bg-darkBg/50 rounded-lg border border-borderClr p-2 font-mono text-xs text-white min-h-[60px]">no decision loaded</div>
                            </div>
                            <div class="text-[10px] text-textMuted leading-relaxed">
                                <span class="font-bold text-cyan-300">KNOWN</span> = rendered &amp; decision-visible at the cursor.
                                <span class="font-bold text-slate-300">UNKNOWN</span> = dimmed right of the cursor line: locally stored but NOT decision-visible.
                                Trades/positions/NO_TRADE come from the replay engine, never chart-side recomputation.
                            </div>
                        </div>
                    </div>
'''

anchor = '                            <!-- Right: Stat validation tracking -->'
assert anchor in s, 'panel anchor not found'
assert 'id="replay-panel"' not in s
s = s.replace(anchor, PANEL + '\n' + anchor, 1)

anchor2 = '<script src="app.js?v=20260902a"></script>'
assert anchor2 in s, 'script anchor not found'
s = s.replace(anchor2, anchor2 + '\n    <script src="replay_panel.js?v=20260902a"></script>', 1)

p.write_bytes(s.encode('utf-8'))
print('index.html updated: panel + script tag')
