/**
 * Node ESM resolve hook: lets the frontend's Vite-style extensionless
 * relative imports ("./model") resolve under plain `node --test`.
 * Registered by tests that import feature .ts modules at runtime
 * (Node 24 strips types, but ESM still demands explicit extensions).
 *
 * Scope: relative specifiers only, first match wins; everything else falls
 * through to the default resolver (incl. `@/` aliases, which must stay
 * type-only in pure modules).
 */
export async function resolve(specifier, context, nextResolve) {
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
