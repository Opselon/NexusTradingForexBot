/* dependency_graph.c — module table, import scanning (absolute + RELATIVE),
 * reverse edges. Semantics MUST byte-match scripts/ci/test_planner.py:
 *   - only `nexus_scalp*` absolute targets participate;
 *   - RELATIVE imports are resolved against the file's own package;
 *     `from .mod import sym` resolves ONLY .mod (symbols are not modules);
 *     `from . import a, b` treats the aliases as submodules;
 *   - a resolved nexus_scalp target must EXIST under <root>/src/ or the graph
 *     becomes relative_unresolved (conservative expansion upstream);
 *   - a resolved tests target must match a known tests/<path>.py node or the
 *     graph becomes relative_unresolved;
 *   - an unparseable file registers the "<unknown>" sentinel;
 *   - changed test-tree files participate as reachability roots (handled by
 *     the selector via graph_find). */
#include "nse_ci_engine.h"

#include <dirent.h>
#include <stdio.h>
#include <string.h>
#if defined(_WIN32)
#include <io.h>
#include <stdlib.h>
#define access _access
#define F_OK 0
/* MSVC spells the reentrant tokenizer strtok_s; the signature is identical.
 * Without this the file does not compile on Windows at all (implicit
 * declaration of strtok_r, then a link error). */
#define strtok_r strtok_s
#else
#include <unistd.h>
#endif

/* ---------------- module table ---------------- */

void graph_init(Graph *g, Arena *a) {
    g->arena = a;
    g->mod_cap = 1024;
    g->mod_count = 0;
    g->mod_names = (char **)arena_alloc(a, g->mod_cap * sizeof(char *));
    g->mod_paths = (char **)arena_alloc(a, g->mod_cap * sizeof(char *));
    g->importers = (size_t **)arena_alloc(a, g->mod_cap * sizeof(size_t *));
    g->importer_counts = (size_t *)arena_alloc(a, g->mod_cap * sizeof(size_t));
    g->importer_caps = (size_t *)arena_alloc(a, g->mod_cap * sizeof(size_t));
    g->is_test = (bool *)arena_alloc(a, g->mod_cap * sizeof(bool));
    g->dynamic = (bool *)arena_alloc(a, g->mod_cap * sizeof(bool));
    g->unparseable = (bool *)arena_alloc(a, g->mod_cap * sizeof(bool));
    g->relative_unresolved = false;
    g->dynamic_files = 0;
    memset(g->dynamic, 0, g->mod_cap * sizeof(bool));
}

static void graph_ensure_cap(Graph *g, size_t need) {
    if (need <= g->mod_cap) return;
    size_t ncap = g->mod_cap * 2;
    while (ncap < need) ncap *= 2;
    char **nn = (char **)arena_alloc(g->arena, ncap * sizeof(char *));
    char **np = (char **)arena_alloc(g->arena, ncap * sizeof(char *));
    size_t **ni = (size_t **)arena_alloc(g->arena, ncap * sizeof(size_t *));
    size_t *nc = (size_t *)arena_alloc(g->arena, ncap * sizeof(size_t));
    size_t *nk = (size_t *)arena_alloc(g->arena, ncap * sizeof(size_t));
    bool *nt = (bool *)arena_alloc(g->arena, ncap * sizeof(bool));
    bool *nd = (bool *)arena_alloc(g->arena, ncap * sizeof(bool));
    bool *nu = (bool *)arena_alloc(g->arena, ncap * sizeof(bool));
    memcpy(nn, g->mod_names, g->mod_count * sizeof(char *));
    memcpy(np, g->mod_paths, g->mod_count * sizeof(char *));
    memcpy(ni, g->importers, g->mod_count * sizeof(size_t *));
    memcpy(nc, g->importer_counts, g->mod_count * sizeof(size_t));
    memcpy(nk, g->importer_caps, g->mod_count * sizeof(size_t));
    memcpy(nt, g->is_test, g->mod_count * sizeof(bool));
    memcpy(nd, g->dynamic, g->mod_count * sizeof(bool));
    memcpy(nu, g->unparseable, g->mod_count * sizeof(bool));
    g->mod_names = nn;
    g->mod_paths = np;
    g->importers = ni;
    g->importer_counts = nc;
    g->importer_caps = nk;
    g->is_test = nt;
    g->dynamic = nd;
    g->unparseable = nu;
    g->mod_cap = ncap;
}

size_t graph_add_new(Graph *g, const char *name, const char *path,
                            bool is_test) {
    graph_ensure_cap(g, g->mod_count + 1);
    size_t idx = g->mod_count++;
    g->mod_names[idx] = arena_strdup(g->arena, name);
    g->mod_paths[idx] = path ? arena_strdup(g->arena, path) : NULL;
    g->importers[idx] = NULL;
    g->importer_counts[idx] = 0;
    g->importer_caps[idx] = 0;
    g->is_test[idx] = is_test;
    g->unparseable[idx] = false;
    g->dynamic[idx] = false;
    return idx;
}

bool graph_find(const Graph *g, const char *name, size_t *out_idx) {
    for (size_t i = 0; i < g->mod_count; i++) {
        if (strcmp(g->mod_names[i], name) == 0) {
            *out_idx = i;
            return true;
        }
    }
    return false;
}

size_t graph_find_or_add(Graph *g, const char *name, const char *path,
                                bool is_test) {
    size_t idx;
    if (graph_find(g, name, &idx)) return idx;
    return graph_add_new(g, name, path, is_test);
}

void graph_add_edge(Graph *g, size_t from, size_t to) {
    if (g->importer_counts[to] == g->importer_caps[to]) {
        size_t ncap = g->importer_caps[to] ? g->importer_caps[to] * 2 : 4;
        size_t *ni = (size_t *)arena_alloc(g->arena, ncap * sizeof(size_t));
        if (g->importer_counts[to]) {
            memcpy(ni, g->importers[to], g->importer_counts[to] * sizeof(size_t));
        }
        g->importers[to] = ni;
        g->importer_caps[to] = ncap;
    }
    for (size_t i = 0; i < g->importer_counts[to]; i++) {
        if (g->importers[to][i] == from) return;
    }
    g->importers[to][g->importer_counts[to]++] = from;
}

/* register a nexus_scalp target + its parent-package chain */
static void register_chain(Graph *g, size_t self_idx, const char *target) {
    size_t idx = graph_find_or_add(g, target, NULL, false);
    graph_add_edge(g, self_idx, idx);
    char parent[512];
    snprintf(parent, sizeof(parent), "%s", target);
    char *dot;
    while ((dot = strrchr(parent, '.'))) {
        *dot = '\0';
        size_t pidx = graph_find_or_add(g, parent, NULL, false);
        graph_add_edge(g, self_idx, pidx);
    }
}

/* own package for a repo-relative .py path (mirrors Python exactly) */
static void own_package_for(const char *rel_path, bool is_test, char *pkg,
                            size_t pkg_len) {
    char parts[64][128];
    size_t nparts = 0;
    char buf[1024];
    snprintf(buf, sizeof(buf), "%s", rel_path);
    size_t n = strlen(buf);
    if (n >= 3 && strcmp(buf + n - 3, ".py") == 0) buf[n - 3] = '\0';
    char *save = NULL;
    char *tok = strtok_r(buf, "/", &save);
    while (tok && nparts < 64) {
        snprintf(parts[nparts], sizeof(parts[nparts]), "%s", tok);
        nparts++;
        tok = strtok_r(NULL, "/", &save);
    }
    bool is_init = nparts > 0 && strcmp(parts[nparts - 1], "__init__") == 0;
    if (!is_test && nparts > 0 && strcmp(parts[0], "src") == 0) {
        for (size_t i = 0; i + 1 < nparts; i++) {
            snprintf(parts[i], sizeof(parts[i]), "%s", parts[i + 1]);
        }
        nparts--;
    }
    size_t mod_len = is_init ? nparts - 1 : nparts;
    /* package = module parts minus the last one.
     *
     * For a package's __init__.py the module IS the package, so the package
     * keeps every part. Python reference:
     *   src/nexus_scalp/release/__init__.py -> ('nexus_scalp.release',
     *                                          'nexus_scalp.release')
     * Dropping the last segment here made `from .paths import ...` inside
     * release/__init__.py resolve to `nexus_scalp.paths` (nonexistent)
     * instead of `nexus_scalp.release.paths`, which flipped the whole graph
     * to relative_unresolved and forced conservative expansion. */
    size_t pkg_len_parts = is_init ? mod_len : (mod_len ? mod_len - 1 : 0);
    pkg[0] = '\0';
    for (size_t i = 0; i < pkg_len_parts; i++) {
        if (i) strncat(pkg, ".", pkg_len - strlen(pkg) - 1);
        strncat(pkg, parts[i], pkg_len - strlen(pkg) - 1);
    }
    (void)is_init;
}

/* resolve + register ONE relative-import target */
static void resolve_register(Graph *g, size_t self_idx, const char *target,
                             const char *repo_root) {
    if (nse_starts_with(target, "nexus_scalp")) {
        char fs[1024];
        snprintf(fs, sizeof(fs), "%s/src/", repo_root);
        char rel[768];
        snprintf(rel, sizeof(rel), "%s", target);
        for (char *c = rel; *c; c++) {
            if (*c == '.') *c = '/';
        }
        char p1[2048], p2[2048];
        snprintf(p1, sizeof(p1), "%s%s.py", fs, rel);
        snprintf(p2, sizeof(p2), "%s%s/__init__.py", fs, rel);
        if (access(p1, F_OK) == 0 || access(p2, F_OK) == 0) {
            register_chain(g, self_idx, target);
        } else {
            g->relative_unresolved = true;
        }
        return;
    }
    if (nse_starts_with(target, "tests")) {
        char rel[768];
        snprintf(rel, sizeof(rel), "%s", target);
        for (char *c = rel; *c; c++) {
            if (*c == '.') *c = '/';
        }
        char p1[800], p2[800];
        snprintf(p1, sizeof(p1), "%s.py", rel);
        snprintf(p2, sizeof(p2), "%s.py", target);
        size_t idx;
        if (graph_find(g, p1, &idx) || graph_find(g, p2, &idx)) {
            graph_add_edge(g, self_idx, idx);
        } else {
            g->relative_unresolved = true;
        }
        return;
    }
    g->relative_unresolved = true;
}

/* scan one file's import lines (absolute + relative) */
static void scan_imports(Graph *g, size_t self_idx, const char *text,
                         const char *rel_path, const char *repo_root) {
    bool is_test = nse_starts_with(rel_path, "tests/");
    const char *p = text;
    while (*p) {
        const char *eol = strchr(p, '\n');
        size_t len = eol ? (size_t)(eol - p) : strlen(p);
        char line[512];
        size_t take = len < sizeof(line) - 1 ? len : sizeof(line) - 1;
        memcpy(line, p, take);
        line[take] = '\0';
        p = eol ? eol + 1 : p + len;

        char *s = line;
        while (*s == ' ' || *s == '\t') s++;
        bool is_from = nse_starts_with(s, "from ");
        bool is_import = nse_starts_with(s, "import ");
        if (!is_from && !is_import) continue;

        if (is_import) {
            const char *rest = s + 7;
            char mod[256];
            while (*rest) {
                size_t i = 0;
                while (*rest && *rest != ',' && i < sizeof(mod) - 1) mod[i++] = *rest++;
                mod[i] = '\0';
                char *as = strstr(mod, " as ");
                if (as) mod[as - mod] = '\0';
                if (nse_starts_with(mod, "nexus_scalp")) {
                    register_chain(g, self_idx, mod);
                }
                if (*rest == ',') rest++;
                while (*rest == ' ') rest++;
            }
        } else {
            const char *rest = s + 5;
            while (*rest == ' ') rest++;
            int level = 0;
            while (*rest == '.') {
                level++;
                rest++;
            }
            char head[256];
            size_t i = 0;
            while (*rest && *rest != ' ' && i < sizeof(head) - 1) head[i++] = *rest++;
            head[i] = '\0';
            const char *imp = strstr(rest, " import ");
            char aliases[512] = "";
            if (imp) {
                const char *al = imp + 8;
                while (*al == ' ') al++;
                snprintf(aliases, sizeof(aliases), "%s", al);
            }
            if (level == 0) {
                if (nse_starts_with(head, "nexus_scalp")) {
                    register_chain(g, self_idx, head);
                }
            } else {
                char pkg[512];
                own_package_for(rel_path, is_test, pkg, sizeof(pkg));
                char pkg2[512];
                snprintf(pkg2, sizeof(pkg2), "%s", pkg);
                for (int k = 0; k < level - 1; k++) {
                    char *dot = strrchr(pkg2, '.');
                    if (!dot) {
                        pkg2[0] = '\0';
                        break;
                    }
                    *dot = '\0';
                }
                if (head[0]) {
                    char target[768];
                    if (pkg2[0]) snprintf(target, sizeof(target), "%s.%s", pkg2, head);
                    else snprintf(target, sizeof(target), "%s", head);
                    resolve_register(g, self_idx, target, repo_root);
                } else {
                    /* `from . import a, b` — aliases ARE submodules */
                    char *save = NULL;
                    char *tok = strtok_r(aliases, ",", &save);
                    while (tok) {
                        while (*tok == ' ') tok++;
                        char *as = strstr(tok, " as ");
                        if (as) *as = '\0';
                        /* trailing trim */
                        size_t tl = strlen(tok);
                        while (tl > 0 && (tok[tl - 1] == ' ' || tok[tl - 1] == '\r')) {
                            tok[--tl] = '\0';
                        }
                        if (*tok) {
                            char target[768];
                            if (pkg2[0]) snprintf(target, sizeof(target), "%s.%s", pkg2, tok);
                            else snprintf(target, sizeof(target), "%s", tok);
                            resolve_register(g, self_idx, target, repo_root);
                        }
                        tok = strtok_r(NULL, ",", &save);
                    }
                }
            }
        }
    }
}

static void scan_file(Graph *g, const char *rel_path, const char *abs_path,
                      const char *repo_root) {
    StrBuf content;
    sb_init(&content);
    bool ok = read_file_all(abs_path, &content);
    bool is_test = nse_starts_with(rel_path, "tests/");
    size_t self_idx;
    if (is_test) {
        self_idx = graph_find_or_add(g, rel_path, rel_path, true);
    } else {
        char mod[512];
        const char *base = nse_starts_with(rel_path, "src/") ? rel_path + 4 : rel_path;
        snprintf(mod, sizeof(mod), "%s", base);
        size_t n = strlen(mod);
        if (n >= 3 && strcmp(mod + n - 3, ".py") == 0) mod[n - 3] = '\0';
        size_t m = strlen(mod);
        if (m >= 9 && strcmp(mod + m - 9, "/__init__") == 0) mod[m - 9] = '\0';
        for (char *c = mod; *c; c++) {
            if (*c == '/') *c = '.';
        }
        self_idx = graph_find_or_add(g, mod, rel_path, false);
    }
    if (!ok) {
        g->unparseable[self_idx] = true;
        size_t unk = graph_find_or_add(g, "<unknown>", NULL, false);
        graph_add_edge(g, self_idx, unk);
    } else {
        scan_imports(g, self_idx, content.data ? content.data : "", rel_path,
                     repo_root);
    }
    sb_free(&content);
}

static void walk_dir(Graph *g, const char *root, const char *rel_base,
                     const char *repo_root) {
    DIR *d = opendir(root);
    if (!d) return;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        if (strcmp(ent->d_name, "__pycache__") == 0) continue;
        char child_rel[1024];
        char child_abs[2048];
        if (rel_base[0]) {
            snprintf(child_rel, sizeof(child_rel), "%s/%s", rel_base, ent->d_name);
        } else {
            snprintf(child_rel, sizeof(child_rel), "%s", ent->d_name);
        }
        snprintf(child_abs, sizeof(child_abs), "%s/%s", root, ent->d_name);
        if (nse_ends_with(child_rel, ".py")) {
            scan_file(g, child_rel, child_abs, repo_root);
        } else {
            walk_dir(g, child_abs, child_rel, repo_root);
        }
    }
    closedir(d);
}

/* Pre-register every .py under `root` as a node, WITHOUT scanning imports.
 *
 * The Python reference does this in two passes: it collects all test-tree
 * paths first, then resolves imports. A single-pass walk cannot resolve a
 * relative import to a file it has not reached yet — `tests/unit/
 * test_bug542_*.py` sorts before `test_live_state_contract.py`, so
 * `from .test_live_state_contract import _FakeEngine` looked unresolvable
 * and flipped the whole graph to conservative expansion. */
static void preregister_dir(Graph *g, const char *root, const char *rel_base) {
    DIR *d = opendir(root);
    if (!d) return;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        if (strcmp(ent->d_name, "__pycache__") == 0) continue;
        char child_rel[1024];
        char child_abs[2048];
        if (rel_base[0]) {
            snprintf(child_rel, sizeof(child_rel), "%s/%s", rel_base, ent->d_name);
        } else {
            snprintf(child_rel, sizeof(child_rel), "%s", ent->d_name);
        }
        snprintf(child_abs, sizeof(child_abs), "%s/%s", root, ent->d_name);
        if (nse_ends_with(child_rel, ".py")) {
            if (nse_starts_with(child_rel, "tests/")) {
                /* test-tree nodes are keyed by their repo path */
                graph_find_or_add(g, child_rel, child_rel, true);
            } else {
                char mod[512];
                const char *base = nse_starts_with(child_rel, "src/") ? child_rel + 4 : child_rel;
                snprintf(mod, sizeof(mod), "%s", base);
                size_t ml = strlen(mod);
                if (ml >= 3 && strcmp(mod + ml - 3, ".py") == 0) mod[ml - 3] = '\0';
                for (char *c = mod; *c; c++) {
                    if (*c == '/') *c = '.';
                }
                if (nse_ends_with(mod, ".__init__")) mod[strlen(mod) - 9] = '\0';
                graph_find_or_add(g, mod, child_rel, false);
            }
        } else {
            preregister_dir(g, child_abs, child_rel);
        }
    }
    closedir(d);
}

void graph_scan_repo(Graph *g, const char *repo_root) {
    char src_root[1024];
    snprintf(src_root, sizeof(src_root), "%s/src/nexus_scalp", repo_root);
    char tests_root[1024];
    snprintf(tests_root, sizeof(tests_root), "%s/tests", repo_root);
    /* Pass 1: every node exists before any import is resolved. */
    preregister_dir(g, src_root, "src/nexus_scalp");
    preregister_dir(g, tests_root, "tests");
    /* Pass 2: scan imports and add edges. */
    walk_dir(g, src_root, "src/nexus_scalp", repo_root);
    walk_dir(g, tests_root, "tests", repo_root);
}

/* A module imported by more than this many nodes is a selection HUB
 * (mirrors _HUB_IMPORTER_THRESHOLD in scripts/ci/test_planner.py): the
 * traversal never expands through it, so one leaf change cannot select
 * most of the suite via a composition root (measured: 779/860 tests for
 * a single research leaf via live_engine). A hub that is itself changed
 * still selects its direct test importers. */
#define HUB_IMPORTER_THRESHOLD 25

void graph_reachable_tests(const Graph *g, const size_t *changed_idx,
                           size_t changed_len, size_t **out_tests,
                           size_t *out_tests_len, size_t **out_covered,
                           size_t *out_covered_len, bool *out_hub_bounded) {
    bool *visited = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *covered = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *sel_test = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *is_changed = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *is_hub = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    memset(visited, 0, g->mod_count * sizeof(bool));
    memset(covered, 0, g->mod_count * sizeof(bool));
    memset(sel_test, 0, g->mod_count * sizeof(bool));
    memset(is_changed, 0, g->mod_count * sizeof(bool));
    memset(is_hub, 0, g->mod_count * sizeof(bool));
    for (size_t i = 0; i < changed_len; i++) {
        if (changed_idx[i] < g->mod_count) is_changed[changed_idx[i]] = true;
    }
    for (size_t i = 0; i < g->mod_count; i++) {
        is_hub[i] = !g->is_test[i] && g->importer_counts[i] > HUB_IMPORTER_THRESHOLD;
    }
    bool hub_bounded = false;

    /* Mark nodes when ENQUEUED, not when popped. A node may be reachable
     * through several importers while an earlier copy is still pending. If
     * it is marked only on pop, the DFS stack can grow with E (or worse on
     * dense cross-edges) even though it was allocated for only V nodes. */
    size_t *stack = (size_t *)arena_alloc(g->arena, g->mod_count * sizeof(size_t));
    size_t top = 0;
    for (size_t i = 0; i < changed_len; i++) {
        size_t root = changed_idx[i];
        if (root >= g->mod_count) die("changed node index out of range");
        if (!visited[root]) {
            if (top >= g->mod_count) die("DFS stack capacity invariant violated");
            visited[root] = true;
            stack[top++] = root;
        }
    }

    while (top > 0) {
        size_t idx = stack[--top];
        if (is_hub[idx] && !is_changed[idx]) {
            /* Intermediate hub: boundary — never expand through it. */
            hub_bounded = true;
            continue;
        }
        if (g->is_test[idx]) covered[idx] = true; /* changed test covers itself */
        bool has_importer = false;
        for (size_t e = 0; e < g->importer_counts[idx]; e++) {
            size_t importer = g->importers[idx][e];
            if (importer >= g->mod_count) die("dependency graph contains an invalid edge");
            if (g->is_test[importer]) {
                has_importer = true;
                sel_test[importer] = true;
                covered[idx] = true;
            } else if (is_hub[importer]) {
                /* Do not cross a hub: its dependents are not selected
                 * through this path, and it does not cover the module. */
                hub_bounded = true;
            } else {
                has_importer = true;
                if (!visited[importer]) {
                    if (top >= g->mod_count) die("DFS stack capacity invariant violated");
                    visited[importer] = true;
                    stack[top++] = importer;
                }
            }
        }
        if (has_importer) covered[idx] = true;
    }
    if (out_hub_bounded) *out_hub_bounded = hub_bounded;

    size_t n_tests = 0, n_cov = 0;
    for (size_t i = 0; i < g->mod_count; i++) {
        if (sel_test[i]) n_tests++;
        if (covered[i]) n_cov++;
    }
    size_t *tests = (size_t *)arena_alloc(g->arena, (n_tests ? n_tests : 1) * sizeof(size_t));
    size_t *cov = (size_t *)arena_alloc(g->arena, (n_cov ? n_cov : 1) * sizeof(size_t));
    size_t ti = 0, ci = 0;
    for (size_t i = 0; i < g->mod_count; i++) {
        if (sel_test[i]) tests[ti++] = i;
        if (covered[i]) cov[ci++] = i;
    }
    *out_tests = tests;
    *out_tests_len = ti;
    *out_covered = cov;
    *out_covered_len = ci;
}

bool graph_path_is_safety(const char *rel_path) {
    static const char *prefixes[] = {
        "src/nexus_scalp/execution/", "src/nexus_scalp/adapters/",
        "src/nexus_scalp/domain/", "src/nexus_scalp/accounting/",
    };
    for (size_t i = 0; i < sizeof(prefixes) / sizeof(prefixes[0]); i++) {
        if (nse_starts_with(rel_path, prefixes[i])) return true;
    }
    return false;
}
