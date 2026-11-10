/* ast_metadata.c — builds the dependency graph from the JSON metadata the
 * minimal Python AST extractor (scripts/ci/ast_extract.py) emits.
 *
 * The C17 engine performs ALL analysis and decisions; Python only extracts
 * structural metadata (imports, classes, functions, fixtures, test args,
 * dynamic-import indicators) that a line scanner cannot see reliably.
 *
 * Graph identity: every file is ONE node keyed by its repo-relative path.
 * A module->node index resolves absolute/relative import targets. Edges:
 *   MODULE_IMPORT / RELATIVE_IMPORT (high)   — resolved imports; a symbol
 *       of `from X import sym` is ALSO resolved as the submodule X.sym
 *       (a namespace package `X` alone owns no file), and every resolved
 *       module also edges to its ancestor package nodes (parent chain),
 *       so changing a package __init__.py selects submodule importers.
 *   DYNAMIC_IMPORT (high)                    — literal importlib targets
 *   FIXTURE_DEPENDENCY  (high)               — fixture args -> definer node
 *   FIXTURE_DEPENDENCY  (high)               — autouse conftest -> scoped tests
 *   UNKNOWN (unresolved)                     — relative_unresolved -> expansion
 * A stale absolute import (target module deleted, still referenced behind
 * try/except) keeps a SYNTHETIC node, mirroring the line scanner: the edge
 * exists but no file can ever be selected through it, and it sets no
 * uncertainty flag. Only an unresolvable RELATIVE import flips
 * relative_unresolved.
 */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Resolve a dotted module name to its graph node, or SIZE_MAX. */
static size_t find_module_node(char **modules, size_t *node_idx, size_t n,
                               const char *name) {
    for (size_t j = 0; j < n; j++) {
        if (strcmp(modules[j], name) == 0) return node_idx[j];
    }
    return SIZE_MAX;
}

/* True when `name` is a namespace package: no __init__.py of its own, but
 * a strict prefix of at least one known module (PEP 420). Importing it
 * alone owns no file, so it contributes no edge and no uncertainty. */
static bool is_namespace_prefix(char **modules, size_t n, const char *name) {
    size_t len = strlen(name);
    for (size_t j = 0; j < n; j++) {
        if (strncmp(modules[j], name, len) == 0 && modules[j][len] == '.') return true;
    }
    return false;
}

/* Edge to a module node plus each ancestor package node that exists —
 * the metadata-path mirror of the line scanner's register_chain().
 * Without the chain, changing a package __init__.py would miss every
 * importer of the package's submodules (a false negative). */
static void add_module_edge(Graph *g, size_t self_idx, const char *target,
                            char **modules, size_t *node_idx, size_t n) {
    char prefix[1024];
    snprintf(prefix, sizeof(prefix), "%s", target);
    for (;;) {
        size_t t = find_module_node(modules, node_idx, n, prefix);
        if (t != SIZE_MAX) graph_add_edge(g, self_idx, t);
        char *dot = strrchr(prefix, '.');
        if (!dot) break;
        *dot = '\0';
    }
}

/* Does this file's metadata define a fixture with this name? (Local
 * definitions shadow conftest/plugin fixtures of the same name.) */
static bool file_defines_fixture(const JsonValue *info, const char *name) {
    const JsonValue *fx = json_obj_get(info, "fixtures");
    if (!fx || fx->type != JSON_ARRAY) return false;
    for (size_t k = 0; k < fx->count; k++) {
        const JsonValue *nv = json_obj_get(fx->items[k], "name");
        if (nv && nv->type == JSON_STRING && strcmp(nv->str, name) == 0) return true;
    }
    return false;
}

/* Directory of a file path ("tests/unit/conftest.py" -> "tests/unit"). */
static void file_dir_of(const char *path, char *dir, size_t dir_len) {
    snprintf(dir, dir_len, "%s", path);
    char *slash = strrchr(dir, '/');
    if (slash) *slash = '\0';
    else dir[0] = '\0';
}

/* True when repo-relative path `p` is under directory `dir`. */
static bool path_in_dir(const char *p, const char *dir) {
    size_t dlen = strlen(dir);
    if (dlen == 0) return true;
    return nse_starts_with(p, dir) && p[dlen] == '/';
}

static bool is_conftest_path(const char *rel) {
    size_t rlen = strlen(rel);
    return rlen >= 12 && strcmp(rel + rlen - 12, "/conftest.py") == 0;
}

/* Try to resolve `target` as a module; on success add the edge (+ parent
 * chain) and return true. Also resolves the target as a file path
 * (tests tree targets, where the dotted name and the path agree). */
static bool resolve_target_edge(Graph *g, size_t self_idx, const char *target,
                                char **modules, size_t *node_idx,
                                char **paths, size_t n) {
    if (find_module_node(modules, node_idx, n, target) != SIZE_MAX) {
        add_module_edge(g, self_idx, target, modules, node_idx, n);
        return true;
    }
    char as_py[1200];
    snprintf(as_py, sizeof(as_py), "%s.py", target);
    for (char *c = as_py; *c; c++) if (*c == '.') *c = '/';
    for (size_t j = 0; j < n; j++) {
        if (strcmp(paths[j], as_py) == 0) {
            graph_add_edge(g, self_idx, node_idx[j]);
            return true;
        }
    }
    return false;
}

int graph_build_from_metadata(Graph *g, const char *repo_root,
                              const char *meta_json, char *err, size_t err_len) {
    Arena a;
    arena_init(&a, 1u << 18);
    JsonParser p;
    p.arena = &a;
    JsonValue *root = json_parse(&p, meta_json, strlen(meta_json));
    if (!root || root->type != JSON_OBJECT) {
        snprintf(err, err_len, "metadata is not a JSON object");
        arena_free(&a);
        return -1;
    }
    const JsonValue *files = json_obj_get(root, "files");
    if (!files || files->type != JSON_OBJECT) {
        snprintf(err, err_len, "metadata missing files object");
        arena_free(&a);
        return -1;
    }
    size_t n = files->count;

    size_t *node_idx = (size_t *)arena_alloc(&a, (n ? n : 1) * sizeof(size_t));
    char **modules = (char **)arena_alloc(&a, (n ? n : 1) * sizeof(char *));
    char **packages = (char **)arena_alloc(&a, (n ? n : 1) * sizeof(char *));
    char **paths = (char **)arena_alloc(&a, (n ? n : 1) * sizeof(char *));

    /* pass 1: one node per file + module/package registry */
    for (size_t i = 0; i < n; i++) {
        const char *rel = files->keys[i];
        const JsonValue *info = files->items[i];
        paths[i] = arena_strdup(&a, rel);
        node_idx[i] = graph_find_or_add(g, rel, rel, nse_starts_with(rel, "tests/"));

        const JsonValue *mv = json_obj_get(info, "module");
        char mod[1024] = "";
        if (mv && mv->type == JSON_STRING) snprintf(mod, sizeof(mod), "%s", mv->str);
        modules[i] = arena_strdup(&a, mod);
        char pkg[1024] = "";
        /* A package's __init__.py IS the package: its relative imports
         * resolve against the full module name. The line scanner fixed
         * this in own_package_for() (2026-11-09, release/__init__.py);
         * the metadata builder had the same bug — `from .paths import …`
         * inside release/__init__.py resolved to `nexus_scalp.paths`
         * (nonexistent) and flipped the whole graph to
         * relative_unresolved, so every metadata plan expanded. */
        size_t rlen = strlen(rel);
        bool is_init = rlen >= 12 && strcmp(rel + rlen - 12, "/__init__.py") == 0;
        if (is_init) {
            snprintf(pkg, sizeof(pkg), "%s", mod);
        } else {
            char *dot = strrchr(mod, '.');
            if (dot) snprintf(pkg, sizeof(pkg), "%.*s", (int)(dot - mod), mod);
        }
        packages[i] = arena_strdup(&a, pkg);
    }

    /* pass 2: imports (absolute + relative, resolved via the module index) */
    for (size_t i = 0; i < n; i++) {
        const JsonValue *info = files->items[i];
        size_t self_idx = node_idx[i];
        const JsonValue *unparseable = json_obj_get(info, "unparseable");
        if (unparseable && unparseable->type == JSON_BOOL && unparseable->b) {
            g->unparseable[self_idx] = true;
            size_t unk = graph_find_or_add(g, "<unknown>", NULL, false);
            graph_add_edge(g, self_idx, unk);
        }
        const JsonValue *imports = json_obj_get(info, "imports");
        if (!imports || imports->type != JSON_ARRAY) continue;
        for (size_t k = 0; k < imports->count; k++) {
            const JsonValue *imp = imports->items[k];
            const JsonValue *mv = json_obj_get(imp, "m");
            const JsonValue *lv = json_obj_get(imp, "rel");
            if (!mv || mv->type != JSON_STRING || !lv || lv->type != JSON_NUMBER) {
                g->relative_unresolved = true;
                continue;
            }
            int level = (int)lv->num;
            const char *m = mv->str;
            const JsonValue *syms0 = json_obj_get(imp, "symbols");

            if (level == 0) {
                /* Absolute import. The extractor records the module part
                 * (`from X import a, b` -> m=X, symbols=[a, b]); a symbol
                 * is very often itself a submodule (`from nexus_scalp.web
                 * import server` -> nexus_scalp.web.server), and `X` alone
                 * may not even exist as a file (namespace package, no
                 * __init__.py). Resolve X AND every X.symbol as modules:
                 * an edge to X alone misses a change to the submodule
                 * file (a false negative the line scanner shares). */
                if (!nse_starts_with(m, "nexus_scalp") &&
                    !nse_starts_with(m, "tests")) {
                    continue;
                }
                bool resolved_any = false;
                if (resolve_target_edge(g, self_idx, m, modules, node_idx,
                                        paths, n)) {
                    resolved_any = true;
                }
                if (syms0 && syms0->type == JSON_ARRAY) {
                    for (size_t s = 0; s < syms0->count; s++) {
                        if (syms0->items[s]->type != JSON_STRING) continue;
                        char sub[1024];
                        snprintf(sub, sizeof(sub), "%s.%s", m,
                                 syms0->items[s]->str);
                        if (resolve_target_edge(g, self_idx, sub, modules,
                                                node_idx, paths, n)) {
                            resolved_any = true;
                        }
                    }
                }
                if (!resolved_any && !is_namespace_prefix(modules, n, m)) {
                    /* Stale/broken absolute import (a module deleted after
                     * a refactor, still referenced behind try/except — e.g.
                     * nexus_scalp.database.audit_repository): mirror the
                     * line scanner and keep a synthetic node so the edge
                     * exists but no file can ever be selected through it.
                     * No expansion flag — the target has no file, so it
                     * can never be a changed file. */
                    size_t tidx = graph_find_or_add(g, m, NULL, false);
                    graph_add_edge(g, self_idx, tidx);
                }
                continue;
            }
            /* relative: resolve against this node's package */
            char pkg[1024];
            snprintf(pkg, sizeof(pkg), "%s", packages[i]);
            for (int s = 0; s < level - 1 && *pkg; s++) {
                char *dot = strrchr(pkg, '.');
                if (!dot) { pkg[0] = '\0'; break; }
                *dot = '\0';
            }
            bool resolved_any = false;
            if (m[0]) {
                char target[1024];
                if (pkg[0]) snprintf(target, sizeof(target), "%s.%s", pkg, m);
                else snprintf(target, sizeof(target), "%s", m);
                if (resolve_target_edge(g, self_idx, target, modules,
                                        node_idx, paths, n)) {
                    resolved_any = true;
                }
                /* `from .pkg import sub` — sub may be the real module
                 * while pkg is only a namespace package. */
                if (syms0 && syms0->type == JSON_ARRAY) {
                    for (size_t s = 0; s < syms0->count; s++) {
                        if (syms0->items[s]->type != JSON_STRING) continue;
                        char sub[1024];
                        snprintf(sub, sizeof(sub), "%s.%s", target,
                                 syms0->items[s]->str);
                        if (resolve_target_edge(g, self_idx, sub, modules,
                                                node_idx, paths, n)) {
                            resolved_any = true;
                        }
                    }
                }
            } else {
                const JsonValue *syms = json_obj_get(imp, "symbols");
                if (syms && syms->type == JSON_ARRAY) {
                    for (size_t s = 0; s < syms->count; s++) {
                        if (syms->items[s]->type != JSON_STRING) continue;
                        char target[1024];
                        if (pkg[0]) snprintf(target, sizeof(target), "%s.%s", pkg, syms->items[s]->str);
                        else snprintf(target, sizeof(target), "%s", syms->items[s]->str);
                        if (resolve_target_edge(g, self_idx, target, modules,
                                                node_idx, paths, n)) {
                            resolved_any = true;
                        }
                    }
                }
            }
            if (!resolved_any) {
                g->relative_unresolved = true;
            }
        }
    }

    /* dynamic imports (importlib.import_module / __import__ with a literal
     * target): the line scanner cannot see these at all, so a test that
     * depends on a module ONLY through a dynamic import was previously
     * unselectable. Literal nexus_scalp/tests targets get real edges.
     * A non-literal target ("<dynamic>") is unknowable statically: it is
     * counted (g->dynamic_files, surfaced in the plan notes) but does NOT
     * force expansion — 11 files in the tree use one, and expanding every
     * plan for them would make the metadata path useless. Residual risk
     * is documented in docs/C17_ARCHITECTURE.md. */
    for (size_t i = 0; i < n; i++) {
        const JsonValue *info = files->items[i];
        size_t self_idx = node_idx[i];
        const JsonValue *dyn = json_obj_get(info, "dynamic_imports");
        if (!dyn || dyn->type != JSON_ARRAY || dyn->count == 0) continue;
        g->dynamic[self_idx] = true;
        g->dynamic_files++;
        bool has_unresolved_dynamic = false;
        for (size_t k = 0; k < dyn->count; k++) {
            if (dyn->items[k]->type != JSON_STRING) continue;
            const char *t = dyn->items[k]->str;
            if (strcmp(t, "<dynamic>") == 0) {
                has_unresolved_dynamic = true;
                continue;
            }
            if (!nse_starts_with(t, "nexus_scalp") && !nse_starts_with(t, "tests")) {
                continue;
            }
            if (!resolve_target_edge(g, self_idx, t, modules, node_idx, paths, n)) {
                size_t tidx = graph_find_or_add(g, t, NULL, false);
                graph_add_edge(g, self_idx, tidx);
            }
        }
        if (has_unresolved_dynamic) g->dynamic_unresolved_files++;
    }

    /* pytest_plugins: a conftest registers a helper module as a plugin,
     * which makes that module's fixtures visible to tests under the
     * conftest's directory (tests/conftest.py registers
     * tests.helpers.shadow70_fixtures, so its `contract` / `tmp_artifacts`
     * fixtures are effectively global). plugin_scope[j] is the scope dir
     * for the file at metadata index j ("" when not a plugin); the
     * shortest (widest) scope wins when several conftests register it. */
    char **plugin_scope = (char **)arena_alloc(&a, (n ? n : 1) * sizeof(char *));
    for (size_t i = 0; i < n; i++) plugin_scope[i] = arena_strdup(&a, "");
    for (size_t i = 0; i < n; i++) {
        if (!is_conftest_path(paths[i])) continue;
        const JsonValue *pp = json_obj_get(files->items[i], "pytest_plugins");
        if (!pp || pp->type != JSON_ARRAY) continue;
        char dir[1024];
        file_dir_of(paths[i], dir, sizeof(dir));
        for (size_t k = 0; k < pp->count; k++) {
            if (pp->items[k]->type != JSON_STRING) continue;
            size_t j = find_module_node(modules, node_idx, n, pp->items[k]->str);
            if (j == SIZE_MAX) continue;
            /* node_idx[] maps file index -> graph node; find the file index */
            size_t fidx = SIZE_MAX;
            for (size_t m2 = 0; m2 < n; m2++) {
                if (node_idx[m2] == j) { fidx = m2; break; }
            }
            if (fidx == SIZE_MAX) continue;
            if (!plugin_scope[fidx][0] || strlen(dir) < strlen(plugin_scope[fidx])) {
                plugin_scope[fidx] = arena_strdup(&a, dir);
            }
        }
    }

    /* fixture registry.
     * Scope-aware (pytest semantics) — the old global name match linked
     * every user of e.g. `temp_audit_repo` (defined separately in 13
     * test files) to every definer: 13x13 spurious edges, which the
     * test-node traversal then followed. Now a fixture creates
     * cross-file edges only when it is visible cross-file:
     *   - defined in a conftest.py: visible to tests under that
     *     conftest's directory (unless the test file defines a fixture
     *     of the same name — local shadowing);
     *   - defined in a pytest_plugins module: visible to tests under
     *     the registering conftest's directory (same shadowing rule);
     *   - defined anywhere else (a test module, a helper): visible ONLY
     *     in its own file, where the plain import edges already cover
     *     the dependency — no cross-file fixture edge is added. */
    typedef struct {
        char name[128];
        size_t node;
        bool autouse;
        char scope[1024]; /* "" = no cross-file visibility */
    } FixtureDef;
    FixtureDef *fixtures = (FixtureDef *)arena_alloc(&a, 1024 * sizeof(FixtureDef));
    size_t fixture_count = 0;
    for (size_t i = 0; i < n && fixture_count < 1024; i++) {
        const JsonValue *fx = json_obj_get(files->items[i], "fixtures");
        if (!fx || fx->type != JSON_ARRAY) continue;
        for (size_t k = 0; k < fx->count && fixture_count < 1024; k++) {
            const JsonValue *entry = fx->items[k];
            const JsonValue *nv = json_obj_get(entry, "name");
            const JsonValue *av = json_obj_get(entry, "autouse");
            if (!nv || nv->type != JSON_STRING) continue;
            snprintf(fixtures[fixture_count].name,
                     sizeof(fixtures[fixture_count].name), "%s", nv->str);
            fixtures[fixture_count].node = node_idx[i];
            fixtures[fixture_count].autouse = av && av->type == JSON_BOOL && av->b;
            fixtures[fixture_count].scope[0] = '\0';
            if (is_conftest_path(paths[i])) {
                file_dir_of(paths[i], fixtures[fixture_count].scope,
                            sizeof(fixtures[fixture_count].scope));
            } else if (plugin_scope[i][0]) {
                snprintf(fixtures[fixture_count].scope,
                         sizeof(fixtures[fixture_count].scope), "%s", plugin_scope[i]);
            }
            fixture_count++;
        }
    }

    /* fixture usage edges: test positional args -> definer node */
    for (size_t i = 0; i < n; i++) {
        const JsonValue *ta = json_obj_get(files->items[i], "test_args");
        if (!ta || ta->type != JSON_OBJECT) continue;
        for (size_t k = 0; k < ta->count; k++) {
            const JsonValue *argl = ta->items[k];
            if (!argl || argl->type != JSON_ARRAY) continue;
            for (size_t a2 = 0; a2 < argl->count; a2++) {
                if (argl->items[a2]->type != JSON_STRING) continue;
                const char *arg = argl->items[a2]->str;
                if (file_defines_fixture(files->items[i], arg)) continue;
                for (size_t fi = 0; fi < fixture_count; fi++) {
                    if (strcmp(fixtures[fi].name, arg) != 0) continue;
                    if (!fixtures[fi].scope[0]) continue;
                    if (fixtures[fi].node == node_idx[i]) continue;
                    if (!path_in_dir(paths[i], fixtures[fi].scope)) continue;
                    graph_add_edge(g, node_idx[i], fixtures[fi].node);
                }
            }
        }
    }

    /* autouse fixtures apply to tests in their scope directory.
     * Only conftest/plugin definers: an autouse fixture in a test module
     * applies to that module alone (no cross-file edge). */
    for (size_t fi = 0; fi < fixture_count; fi++) {
        if (!fixtures[fi].autouse || !fixtures[fi].scope[0]) continue;
        for (size_t i = 0; i < n; i++) {
            if (!nse_starts_with(paths[i], "tests/")) continue;
            if (node_idx[i] == fixtures[fi].node) continue;
            if (!path_in_dir(paths[i], fixtures[fi].scope)) continue;
            if (file_defines_fixture(files->items[i], fixtures[fi].name)) continue;
            graph_add_edge(g, node_idx[i], fixtures[fi].node);
        }
    }

    (void)repo_root;
    arena_free(&a);
    return 0;
}
