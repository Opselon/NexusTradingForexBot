/* ast_metadata.c — builds the dependency graph from the JSON metadata the
 * minimal Python AST extractor (scripts/ci/ast_extract.py) emits.
 *
 * The C17 engine performs ALL analysis and decisions; Python only extracts
 * structural metadata (imports, classes, functions, fixtures, test args,
 * dynamic-import indicators) that a line scanner cannot see reliably.
 *
 * Graph identity: every file is ONE node keyed by its repo-relative path.
 * A module->node index resolves absolute/relative import targets. Edges:
 *   MODULE_IMPORT / RELATIVE_IMPORT (high)   — resolved imports
 *   FIXTURE_DEPENDENCY  (high)               — fixture args -> definer node
 *   FIXTURE_DEPENDENCY  (high)               — autouse conftest -> scoped tests
 *   UNKNOWN (unresolved)                     — relative_unresolved -> expansion
 */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
        char *dot = strrchr(mod, '.');
        if (dot) snprintf(pkg, sizeof(pkg), "%.*s", (int)(dot - mod), mod);
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

            if (level == 0) {
                if (!nse_starts_with(m, "nexus_scalp")) continue;
                size_t tidx = SIZE_MAX;
                for (size_t j = 0; j < n; j++) {
                    if (strcmp(modules[j], m) == 0) { tidx = node_idx[j]; break; }
                }
                if (tidx != SIZE_MAX) {
                    graph_add_edge(g, self_idx, tidx);
                } else {
                    g->relative_unresolved = true;
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
            size_t resolved = SIZE_MAX;
            if (m[0]) {
                char target[1024];
                if (pkg[0]) snprintf(target, sizeof(target), "%s.%s", pkg, m);
                else snprintf(target, sizeof(target), "%s", m);
                for (size_t j = 0; j < n; j++) {
                    if (strcmp(modules[j], target) == 0) { resolved = j; break; }
                }
                if (resolved == SIZE_MAX) {
                    char as_path[1024];
                    snprintf(as_path, sizeof(as_path), "%s", target);
                    for (char *c = as_path; *c; c++) if (*c == '.') *c = '/';
                    char as_py[1200];
                    snprintf(as_py, sizeof(as_py), "%s.py", as_path);
                    for (size_t j = 0; j < n; j++) {
                        if (strcmp(paths[j], as_py) == 0) { resolved = j; break; }
                    }
                }
            } else {
                const JsonValue *syms = json_obj_get(imp, "symbols");
                if (syms && syms->type == JSON_ARRAY) {
                    for (size_t s = 0; s < syms->count && resolved == SIZE_MAX; s++) {
                        if (syms->items[s]->type != JSON_STRING) continue;
                        char target[1024];
                        if (pkg[0]) snprintf(target, sizeof(target), "%s.%s", pkg, syms->items[s]->str);
                        else snprintf(target, sizeof(target), "%s", syms->items[s]->str);
                        for (size_t j = 0; j < n; j++) {
                            if (strcmp(modules[j], target) == 0) { resolved = j; break; }
                        }
                        if (resolved == SIZE_MAX) {
                            char as_py[1200];
                            snprintf(as_py, sizeof(as_py), "%s.py", target);
                            for (size_t j = 0; j < n; j++) {
                                if (strcmp(paths[j], as_py) == 0) { resolved = j; break; }
                            }
                        }
                    }
                }
            }
            if (resolved != SIZE_MAX) {
                graph_add_edge(g, self_idx, node_idx[resolved]);
            } else if (nse_starts_with(m, "nexus_scalp") ||
                       nse_starts_with(m, "tests") || level > 0) {
                g->relative_unresolved = true;
            }
        }
    }

    /* fixture registry */
    typedef struct { char name[128]; size_t node; bool autouse; } FixtureDef;
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
                for (size_t fi = 0; fi < fixture_count; fi++) {
                    if (strcmp(fixtures[fi].name, argl->items[a2]->str) == 0 &&
                        fixtures[fi].node != node_idx[i]) {
                        graph_add_edge(g, node_idx[i], fixtures[fi].node);
                    }
                }
            }
        }
    }

    /* autouse conftest fixtures apply to tests in their directory scope */
    for (size_t fi = 0; fi < fixture_count; fi++) {
        if (!fixtures[fi].autouse) continue;
        const char *def_path = g->mod_paths[fixtures[fi].node];
        if (!def_path) continue;
        char dir[1024];
        snprintf(dir, sizeof(dir), "%s", def_path);
        char *slash = strrchr(dir, '/');
        if (slash) *slash = '\0';
        size_t dir_len = strlen(dir);
        for (size_t nn = 0; nn < g->mod_count; nn++) {
            if (!g->is_test[nn] || nn == fixtures[fi].node) continue;
            if (nse_starts_with(g->mod_names[nn], dir) &&
                (g->mod_names[nn][dir_len] == '/' || dir_len == 0)) {
                graph_add_edge(g, nn, fixtures[fi].node);
            }
        }
    }

    (void)repo_root;
    arena_free(&a);
    return 0;
}
