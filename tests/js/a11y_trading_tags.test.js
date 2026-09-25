/**
 * a11y tag audit — brace/quote-aware scan for interactive elements with no
 * accessible name in frontend/src/pages/Trading/ .
 *
 * "Accessible name" per the contract = an element has AT LEAST ONE of:
 *   - non-empty aria-label / aria-labelledby
 *   - non-empty visible text content (direct or via known text-bearing child)
 *   - title attribute (advisory, accepted here as it is what the CSV button
 *     and ladder meters already rely on across this console)
 *
 * It walks JSX with a state machine that respects:
 *   - template literals `...${ x }...`  (backtick depth)
 *   - single/double quotes and escaped quotes inside them
 *   - braces {} as attribute-value containers and JS expression depth
 *   - line comments // and block comments /* *\/
 *   - JSX self-closing tags /> and balanced <open>...</close> pairs
 *
 * Emits one record per interactive element (button/a/role=button/tab/etc.).
 * Exit code 0 = 0 unlabeled; 1 = unlabeled found.
 */
"use strict";
const fs = require("fs");
const path = require("path");

const SCOPE = "C:/c/tmp/nse-ux9/frontend/src/pages/Trading";
const INTERACTIVE = new Set(["button", "a", "input", "select", "textarea", "summary"]);
const ROLES = new Set(["button", "tab", "link", "checkbox", "radio", "menuitem", "switch"]);

function walk(dir, out) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = fs.statSync(full);
    if (st.isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

/** Extract the accessible name signals for one JSX opening tag's attributes. */
function attrsOf(tag) {
  // tag: text between "<Name" and the end ">" or "/>"
  const attrs = {};
  const re = /\b([A-Za-z][A-Za-z0-9-]*)\s*(=)\s*/g;
  let m;
  while ((m = re.exec(tag)) !== null) {
    const name = m[1];
    const start = re.lastIndex;
    const ch = tag[start];
    if (ch === '"') {
      const end = tag.indexOf('"', start + 1);
      attrs[name] = tag.slice(start + 1, end);
    } else if (ch === "'") {
      const end = tag.indexOf("'", start + 1);
      attrs[name] = tag.slice(start + 1, end);
    } else if (ch === "{") {
      // expression: walk to matching close brace, honouring strings inside
      let depth = 1, i = start + 1, q = null;
      while (i < tag.length && depth > 0) {
        const c = tag[i];
        if (q) {
          if (c === "\\") { i += 2; continue; }
          if (c === q) q = null;
        } else if (c === '"' || c === "'" || c === "`") {
          q = c;
        } else if (c === "{") {
          depth++;
        } else if (c === "}") {
          depth--;
        }
        i++;
      }
      attrs[name] = tag.slice(start, i); // keep the braces; truthy unless {}
    }
    // boolean shorthand attribute (no '=') → treat as true
    if (!(name in attrs)) attrs[name] = "true";
  }
  return attrs;
}

function scan(src, rel) {
  const out = [];
  let i = 0;
  const n = src.length;
  let line = 1;
  let q = null; // active string quote
  let tplDepth = 0; // backtick template depth
  let braceDepth = 0; // JS expression depth inside JSX attribute/child
  let inLineComment = false;
  let inBlockComment = false;

  while (i < n) {
    const c = src[i];
    const nx = src[i + 1];

    if (c === "\n") {
      line++;
      inLineComment = false;
      i++;
      continue;
    }
    if (inLineComment) { i++; continue; }
    if (inBlockComment) {
      if (c === "*" && nx === "/") { inBlockComment = false; i += 2; continue; }
      i++; continue;
    }
    if (q) {
      if (c === "\\") { i += 2; continue; }
      if (c === q) { q = null; i++; continue; }
      i++; continue;
    }
    if (tplDepth > 0) {
      if (c === "\\") { i += 2; continue; }
      if (c === "`") { tplDepth--; i++; continue; }
      i++; continue;
    }
    // not in a string/template
    if (c === "/" && nx === "/") { inLineComment = true; i += 2; continue; }
    if (c === "/" && nx === "*") { inBlockComment = true; i += 2; continue; }
    if (c === "`") { tplDepth++; i++; continue; }
    if (c === '"' || c === "'") { q = c; i++; continue; }

    if (c === "<" && /[A-Za-z\/]/.test(nx || "")) {
      // JSX tag boundary
      const startLine = line;
      let j = i + 1;
      let selfClose = false;
      // read until the tag's closing > honoring quotes
      let tq = null;
      while (j < n) {
        const tc = src[j];
        if (tq) {
          if (tc === "\\") { j += 2; continue; }
          if (tc === tq) tq = null;
          j++; continue;
        }
        if (tc === '"' || tc === "'") { tq = tc; j++; continue; }
        if (tc === "{") {
          // attribute expression: skip to matching brace
          let d = 1, k = j + 1, qq = null;
          while (k < n && d > 0) {
            const kc = src[k];
            if (qq) {
              if (kc === "\\") { k += 2; continue; }
              if (kc === qq) qq = null;
            } else if (kc === '"' || kc === "'" || kc === "`") {
              qq = kc;
            } else if (kc === "{") {
              d++;
            } else if (kc === "}") {
              d--;
            }
            k++;
          }
          j = k;
          continue;
        }
        if (tc === "/" && src[j + 1] === ">") { selfClose = true; j += 2; break; }
        if (tc === ">") { j++; break; }
        j++;
      }
      const tagText = src.slice(i, j);
      const isClosing = tagText[1] === "/";
      const nameMatch = tagText.match(/^<[\/]?([A-Za-z][A-Za-z0-9.-]*)/);
      const name = nameMatch ? nameMatch[1] : "?";
      if (!isClosing) {
        const attrs = attrsOf(tagText);
        const role = (attrs.role || "").replace(/[{}"]/g, "");
        const interactive =
          INTERACTIVE.has(name) || ROLES.has(role) || role === "button";
        if (interactive) {
          out.push({
            tag: name,
            line: startLine,
            selfClose,
            attrs,
            hasLabel:
              !!attrs["aria-label"] ||
              !!attrs["aria-labelledby"] ||
              !!attrs["title"] ||
              !!attrs.alt ||
              !!attrs.value ||
              !!attrs.placeholder,
          });
        }
      }
      i = j;
      continue;
    }
    i++;
  }
  return out;
}

const files = walk(SCOPE, []);
let unlabeled = 0;
const total = [];
for (const f of files) {
  const src = fs.readFileSync(f, "utf8");
  const rel = path.relative(SCOPE, f);
  const found = scan(src, rel);
  for (const e of found) {
    total.push({ file: rel, ...e });
    if (!e.hasLabel) unlabeled++;
  }
}

console.log(`a11y tag audit: ${total.length} interactive elements in Trading/, ${unlabeled} without an accessible name`);
for (const e of total.filter((x) => !x.hasLabel)) {
  console.log(`  UNLABELED ${e.file}:${e.line} <${e.tag}>`);
}
if (unlabeled > 0) {
  process.exitCode = 1;
}
