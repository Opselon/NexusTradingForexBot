// Edge-case validation: empty dataset, long labels, many metrics.
// Simulates API responses by intercepting /api/account/performance/* routes.
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });

async function runCase(name, apiHandler) {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !m.text().includes('Failed to load resource')) errs.push('CONSOLE: ' + m.text()); });
  await page.route('**/api/account/performance**', async route => {
    const url = route.request().url();
    const body = apiHandler(url);
    // period endpoint expects {available, period}
    if (url.includes('/DAY') || url.includes('/WEEK') || url.includes('/MONTH') || url.includes('/YEAR')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ available: true, period: body.periods ? body.periods.DAY : null }) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    }
  });
  await page.goto('http://127.0.0.1:8080/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  await page.evaluate(() => { const b = [...document.querySelectorAll('.tab-btn')]; const a = b.find(x => x.textContent.includes('Account Center')); if (a) a.click(); });
  await page.waitForTimeout(1600);
  const rep = await page.evaluate(() => {
    const hs = [...document.querySelectorAll('h3')];
    const h = hs.find(x => x.textContent.includes('Account Performance'));
    if (!h) return 'PANEL MISSING';
    const panel = h.closest('.bg-panelBg');
    const pb = panel.getBoundingClientRect();
    const deep = document.getElementById('acct-intel-deep');
    const deepHidden = deep ? deep.classList.contains('hidden') : 'MISSING';
    const intel = document.getElementById('acct-intel-texts');
    const it = intel ? intel.textContent.slice(0, 80).replace(/\s+/g, ' ') : 'MISSING';
    const winDenom = document.getElementById('acct-win-denom');
    const wd = winDenom ? winDenom.textContent : 'MISSING';
    const ov = document.documentElement.scrollWidth > window.innerWidth ? 'OVERFLOW' : 'ok';
    return 'panel=' + Math.round(pb.width) + 'x' + Math.round(pb.height) + ' deepHidden=' + deepHidden + ' overflow=' + ov +
      '\nintel: ' + it + '\nwinDenom: ' + wd;
  });
  console.log('== ' + name + ' ==');
  console.log(rep);
  console.log('errors: ' + (errs.join('; ') || '(none)'));
  await page.screenshot({ path: 'C:/Users/Capsizer/source/repos/NexusTradingForexBot/pics/_case_' + name + '.png' });
  await page.close();
}

// Base payload factory
function basePayload(advanced, period, live) {
  return {
    available: true,
    live: live || { balance: 10000, equity: 10000, floating_pnl: 0, margin_free: 8000, open_positions: 0 },
    drawdown: { has_data: false },
    totals: { win_rate: 50 },
    advanced: advanced,
    periods: { DAY: period, WEEK: period, MONTH: period, YEAR: period },
    worker: { running: false },
  };
}

const fullAdvanced = {
  sharpe_ratio: 1.2, sortino_ratio: 0.8, calmar_ratio: 0.5, sqn: 2.1, recovery_factor: 1.4, payoff_ratio: 1.8,
  average_win: 100, average_loss: -80, max_consecutive_wins: 5, max_consecutive_losses: 3,
  equity_volatility_pct: 0.4, profit_standard_error: 1.1, stop_loss_share: 0.8, avg_loss_r: -0.5,
  avg_r_multiple: 0.6, avg_mae_r: 1.2, avg_mfe_r: 2.4, avg_hold_sec: 180, avg_risk_usd: 50,
  r_coverage_ratio: 0.95, sample_trades: 100, win_rate: 55, loss_rate_decided: 45, win_rate_all: 50,
  loss_rate_all: 40, pnl_weighted_win_rate: 60, total_costs: 25, net_pnl: 500, cost_drag_pct: 5,
  expectancy_breakeven_incl: 5, loss_efficiency_pct: 30, win_rate_denominator: 'DECIDED',
};

const fullPeriod = {
  net_pnl: 500, pnl_pct: 5, total_trades: 100, win_rate: 55, expectancy: 5, profit_factor: 1.5,
  average_r: 0.5, max_drawdown_pct: 3, best_trade: 120, worst_trade: -90, average_holding_sec: 180,
  total_risk_deployed: 5000, loss_rate_decided: 45, loss_rate_all: 40, win_rate_all: 50,
  pnl_weighted_win_rate: 60, avg_pnl_per_decided: 5, cost_drag_pct: 5, win_rate_denominator: 'DECIDED',
  gross_profit: 2000, gross_loss: 1500, expectancy_breakeven_incl: 5, breakeven_count: 3,
};

// CASE 1: full data (normal)
await runCase('full', url => basePayload(fullAdvanced, fullPeriod));

// CASE 2: EMPTY dataset (available but no advanced / no period data)
await runCase('empty', url => ({
  available: true, live: null, drawdown: { has_data: false }, totals: null,
  advanced: null, periods: null, worker: { running: false },
}));

// CASE 3: long labels (values with very long strings)
await runCase('longlabels', url => {
  const adv = { ...fullAdvanced, sharpe_ratio: 1234.56789, stop_loss_share: 0.99999, avg_mae_r: 12.3456789, r_coverage_ratio: 0.9999 };
  const per = { ...fullPeriod, net_pnl: -1234567.89, pnl_pct: -99.99 };
  return basePayload(adv, per);
});

// CASE 4: many advanced metrics (extra unknown keys + huge sample)
await runCase('many', url => {
  const adv = { ...fullAdvanced, sample_trades: 99999 };
  return basePayload(adv, fullPeriod);
});

await browser.close();