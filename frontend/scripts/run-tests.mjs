import { readdirSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const repoRoot = resolve(import.meta.dirname, "../..");
const testDir = resolve(repoRoot, "tests/js");
const tests = readdirSync(testDir).filter((name) => name.endsWith(".test.js")).sort();

if (tests.length === 0) {
  console.error("No frontend JavaScript tests found under tests/js.");
  process.exit(1);
}

let failed = 0;
for (const name of tests) {
  console.log(`\n=== npm test: ${name} ===`);
  const result = spawnSync(process.execPath, [resolve(testDir, name)], {
    cwd: repoRoot,
    stdio: "inherit",
    env: process.env,
  });
  if (result.status !== 0) failed++;
}

console.log(`\nFrontend JS tests: ${tests.length - failed} passed, ${failed} failed.`);
process.exit(failed === 0 ? 0 : 1);
