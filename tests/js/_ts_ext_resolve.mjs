/**
 * Node ESM resolve hook: lets the frontend's Vite-style extensionless
 * relative imports ("./model") resolve under plain `node --test`.
 * Registered by tests that import feature .ts modules at runtime
 * (Node 24 strips types, but ESM still demands explicit extensions).
 *
 * Scope: relative specifiers and the `@/` alias, first match wins; everything
 * else falls through to the default resolver. (`@/` aliases are normally
 * type-only in pure modules; model.ts has a runtime `@/features/config/
 * validation` import, so the alias is resolved to frontend/src here too.)
 */
export async function resolve(specifier, context, nextResolve) {
  // The Vite `@` alias maps to frontend/src. model.ts has a runtime
  // `@/features/config/validation` import, so the alias must resolve under
  // plain node. Try the alias first; if it fails, fall through unchanged so
  // type-only aliases keep their normal (erased) behavior.
  if (specifier.startsWith("@/")) {
    const path = await import("node:path");
    const url = await import("node:url");
    const root = path.resolve(
      path.dirname(url.fileURLToPath(import.meta.url)),
      "..", "..", "frontend", "src",
    );
    const rel = specifier.slice("@/".length);
    for (const ext of [".ts", ".tsx", "/index.ts"]) {
      try {
        const abs = path.join(root, rel + ext);
        return await nextResolve(url.pathToFileURL(abs).href, context);
      } catch {
        /* try next extension */
      }
    }
  }
  if ((specifier.startsWith("./") || specifier.startsWith("../")) && !/\.[a-z]+$/i.test(specifier)) {
    for (const ext of [".ts", ".tsx", "/index.ts"]) {
      try {
        return await nextResolve(specifier + ext, context);
      } catch {
        /* try next */
      }
    }
  }
  return nextResolve(specifier, context);
}
