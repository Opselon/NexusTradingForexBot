/**
 * Control Center rules-of-hooks regression test (2026-09-28).
 *
 * ROOT CAUSE TESTED: frontend/src/features/control-center/ui/OverviewPanels.tsx
 * called `useMemo` AFTER conditional early returns (`if (pending) return ...`,
 * `if (error) return ...`), violating the rules of hooks. On the live
 * production build this threw "Minified React error #310" and the whole
 * Control Center page crashed into the ErrorBoundary fallback
 * ("Control Center crashed while rendering") — visible in the browser
 * console and the a11y snapshot. The API was healthy the entire time; the
 * operator saw zero evidence from a system that was fully up.
 *
 * This test does not need a DOM or a React runtime: the rules of hooks are a
 * static, syntactic property — a hook must never appear after an early
 * return in the same function body. We parse the TSX and walk each function
 * declaration, so a re-introduced early-return-before-hook fails fast in CI
 * rather than in the operator's browser.
 *
 * Run: node --test tests/js/control_center_hooks.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'node:url';

const __filename_esm = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename_esm);

const SRC_DIR = path.join(
  __dirname,
  '..',
  '..',
  'frontend',
  'src',
  'features',
  'control-center',
);

const REACT_HOOKS = new Set([
  'useState',
  'useEffect',
  'useContext',
  'useReducer',
  'useCallback',
  'useMemo',
  'useRef',
  'useLayoutEffect',
  'useDebugValue',
  'useTransition',
  'useDeferredValue',
  'useImperativeHandle',
  'useId',
  'useSyncExternalStore',
]);

/**
 * A tiny brace/paren/quote-aware scanner over the raw source. We do not need
 * a full TS parser — we need to know, per top-level function, whether a hook
 * CALL appears textually AFTER a `return` that is guarded by a condition.
 *
 * The failure shape is specifically: `if (<cond>) return <expr>;` (or a
 * bare `return ...;` mid-body) followed later by a `useXxx(` call. A hook
 * before every return is legal; a hook after ANY return statement is not.
 */
function scanFunctionBody(body) {
  let depth = 0;
  let i = 0;
  let returned = false;
  const hookSites = [];
  let quote = null;

  while (i < body.length) {
    const ch = body[i];
    const two = body.slice(i, i + 2);

    if (quote) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      quote = ch;
      i += 1;
      continue;
    }
    // strip line comments + block comments so a `return` inside a comment
    // cannot be mistaken for control flow
    if (two === '//') {
      const nl = body.indexOf('\n', i);
      i = nl === -1 ? body.length : nl + 1;
      continue;
    }
    if (two === '/*') {
      const end = body.indexOf('*/', i + 2);
      i = end === -1 ? body.length : end + 2;
      continue;
    }

    if (ch === '{' || ch === '(' || ch === '[') depth += 1;
    if (ch === '}' || ch === ')' || ch === ']') depth = 1 > 0 ? Math.max(0, depth - 1) : depth;

    if (depth === 0 || ch === '}') {
      // A return at the top level of this function body ends the linear hook
      // region. Any hook found later is after an unconditional return, which
      // is dead code but still an error class; the early-return form
      // (`if (c) return`) is the crash we care about and is caught below.
      const word = /\breturn\b/.test(body.slice(Math.max(0, i - 6), i + 6));
      if (word) returned = true;
    }
    i += 1;
  }
  return { returned };
}

/**
 * The precise defect: `if (<cond>) return ...;` appearing BEFORE a hook call
 * at the same nesting depth in the function body. Detecting this requires no
 * full parse — we split the body on the guarded-return pattern and check that
 * no hook call follows it at depth 1.
 */
function earlyReturnsBeforeHooks(src, fnName, bodyStart) {
  const findings = [];
  // Walk the body tracking brace depth, string state and comments; record the
  // index of every `return` that is preceded by an `if (...)` at the same
  // depth, then look for a hook call after it at depth 1.
  let i = bodyStart;
  let depth = 0;
  let quote = null;
  let guardedReturnAt = null;

  const isHookCall = (at) => {
    for (const h of REACT_HOOKS) {
      if (src.startsWith(h + '(', at)) return h;
    }
    return null;
  };

  while (i < src.length) {
    const ch = src[i];
    const two = src.slice(i, i + 2);
    if (quote) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; i += 1; continue; }
    if (two === '//') {
      const nl = src.indexOf('\n', i);
      i = nl === -1 ? src.length : nl + 1;
      continue;
    }
    if (two === '/*') {
      const end = src.indexOf('*/', i + 2);
      i = end === -1 ? src.length : end + 2;
      continue;
    }
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) break; // end of this function body
    }

    // Detect `... return ... ;` at function-body depth (depth === 1 inside
    // the outermost block of the function).
    if (depth === 1 && /\breturn\b/.test(src.slice(i, i + 6)) && !src.slice(i, i + 7).includes('//')) {
      if (guardedReturnAt === null) guardedReturnAt = i;
    }
    // Detect a hook call at body depth AFTER a guarded return.
    if (guardedReturnAt !== null && depth === 1) {
      const wordEnd = i;
      const hook = isHookCall(wordEnd) || isHookCall(wordEnd + 1);
      if (hook && !/[A-Za-z0-9_$]/.test(src[wordEnd - 1] || '')) {
        findings.push({ hook, at: guardedReturnAt });
        guardedReturnAt = null;
      }
    }
    i += 1;
  }
  return findings;
}

function checkFile(file) {
  const abs = path.join(SRC_DIR, file);
  const src = fs.readFileSync(abs, 'utf8');
  const problems = [];

  // Locate every exported/named function component and inspect its body.
  const decls = [
    ...src.matchAll(/export\s+function\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*{/g),
    ...src.matchAll(/function\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*{/g),
  ];
  for (const m of decls) {
    const name = m[1];
    const bodyStart = m.index + m[0].length - 1; // at the '{'
    const findings = earlyReturnsBeforeHooks(src, name, bodyStart);
    for (const f of findings) {
      problems.push(`${name}(): hook ${f.hook}(...) appears after an early return — rules of hooks violation (React #310 crash).`);
    }
  }
  return problems;
}

test('OverviewPanels does not call hooks after an early return', () => {
  const problems = checkFile('ui/OverviewPanels.tsx');
  assert.deepStrictEqual(problems, [], problems.join('\n'));
});

test('every Control Center component keeps hooks before any return', () => {
  const files = fs.readdirSync(SRC_DIR).filter((f) => f.endsWith('.tsx'));
  for (const f of files) {
    const problems = checkFile(f);
    assert.deepStrictEqual(problems, [], `${f}: ${problems.join('\n')}`);
  }
});

test('the fixed source no longer contains the crashing shape', () => {
  const src = fs.readFileSync(path.join(SRC_DIR, 'ui', 'OverviewPanels.tsx'), 'utf8');
  // The exact pre-fix ordering that crashed: an early return for pending
  // followed later by useMemo.
  const earlyReturnIdx = src.indexOf('if (pending) return');
  const memoIdx = src.indexOf('useMemo(');
  assert.ok(earlyReturnIdx !== -1, 'fixture invariant: the pending guard still exists');
  assert.ok(memoIdx !== -1, 'fixture invariant: the memo still exists');
  assert.ok(
    memoIdx < earlyReturnIdx,
    'useMemo must run BEFORE the pending/error early returns, otherwise the ' +
      'component throws "Rendered more hooks than during the previous render" ' +
      'and the whole Control Center page crashes.',
  );
});
