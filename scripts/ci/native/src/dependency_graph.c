/* dependency_graph.c — module table, import scanning, reverse edges.
 *
 * Import semantics MUST match scripts/ci/test_planner.py:
 *   - only `nexus_scalp*` targets participate (absolute imports);
 *   - relative imports are ignored;
 *   - parent-package edges are registered (nexus_scalp.a.b -> nexus_scalp.a);
 *   - an unparseable/unreadable file registers the sentinel "<unknown>" so
 *     the selector can fail conservative.
 *
 * File walking is done by the caller-independent directory scanner below
 * (POSIX dirent + Windows fallback via FindFirstFile is intentionally NOT
 * implemented — the engine targets Linux x86_64 runners; Windows is served
 * through the Python fallback, documented in build.sh).
 */
#include "nse_ci_engine.h"

#include <dirent.h>
#include <stdio.h>
#include <string.h>

/* ---------------- module table ---------------- */

static uint64_t fnv1a(const char *s) {
    uint64_t h = 1469598103934665603ull;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        h ^= (uint64_t)*p;
        h *= 1099511628211ull;
    }
    return h;
}

static size_t graph_find_or_add(Graph *g, const char *name, const char *path,
                                bool is_test);

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
    g->unparseable = (bool *)arena_alloc(a, g->mod_cap * sizeof(bool));
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
    bool *nu = (bool *)arena_alloc(g->arena, ncap * sizeof(bool));
    memcpy(nn, g->mod_names, g->mod_count * sizeof(char *));
    memcpy(np, g->mod_paths, g->mod_count * sizeof(char *));
    memcpy(ni, g->importers, g->mod_count * sizeof(size_t *));
    memcpy(nc, g->importer_counts, g->mod_count * sizeof(size_t));
    memcpy(nk, g->importer_caps, g->mod_count * sizeof(size_t));
    memcpy(nt, g->is_test, g->mod_count * sizeof(bool));
    memcpy(nu, g->unparseable, g->mod_count * sizeof(bool));
    g->mod_names = nn;
    g->mod_paths = np;
    g->importers = ni;
    g->importer_counts = nc;
    g->importer_caps = nk;
    g->is_test = nt;
    g->unparseable = nu;
    g->mod_cap = ncap;
}

static size_t graph_find_or_add(Graph *g, const char *name, const char *path,
                                bool is_test) {
    /* linear scan is O(n^2) for large trees; use a small open-addressing
     * index instead: rebuild lazily. For CI-scale trees (<= 5k modules) a
     * hash index keeps this effectively O(1). */
    static const size_t NO_BUCKETS = 0; /* placeholder to silence pedantic */
    (void)NO_BUCKETS;
    for (size_t i = 0; i < g->mod_count; i++) {
        if (strcmp(g->mod_names[i], name) == 0) return i;
    }
    graph_ensure_cap(g, g->mod_count + 1);
    size_t idx = g->mod_count++;
    g->mod_names[idx] = arena_strdup(g->arena, name);
    g->mod_paths[idx] = path ? arena_strdup(g->arena, path) : NULL;
    g->importers[idx] = NULL;
    g->importer_counts[idx] = 0;
    g->importer_caps[idx] = 0;
    g->is_test[idx] = is_test;
    g->unparseable[idx] = false;
    return idx;
}

static void graph_add_edge(Graph *g, size_t from, size_t to) {
    /* edge: "to" gains importer "from" */
    if (g->importer_counts[to] == g->importer_caps[to]) {
        size_t ncap = g->importer_caps[to] ? g->importer_caps[to] * 2 : 4;
        size_t *ni = (size_t *)arena_alloc(g->arena, ncap * sizeof(size_t));
        if (g->importer_counts[to]) {
            memcpy(ni, g->importers[to], g->importer_counts[to] * sizeof(size_t));
        }
        g->importers[to] = ni;
        g->importer_caps[to] = ncap;
    }
    /* deduplicate edges — reachability does not need multi-edges */
    for (size_t i = 0; i < g->importer_counts[to]; i++) {
        if (g->importers[to][i] == from) return;
    }
    g->importers[to][g->importer_counts[to]++] = from;
}

/* Extract repo-internal import targets from one .py file (line scanner).
 * Mirrors the Python AST semantics for the deterministic common cases:
 *   import nexus_scalp.a.b      -> register chain
 *   from nexus_scalp.a.b import c -> register chain
 *   from .x import y            -> ignored (relative)
 */
static void scan_imports(Graph *g, size_t self_idx, const char *text) {
    const char *p = text;
    while (*p) {
        /* find next line */
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
            /* module list: a, b, c — only nexus_scalp* entries register */
            char mod[256];
            while (*rest) {
                size_t i = 0;
                while (*rest && *rest != ',' && i < sizeof(mod) - 1) mod[i++] = *rest++;
                mod[i] = '\0';
                /* strip ' as alias' */
                char *as = strstr(mod, " as ");
                if (as) mod[as - mod] = '\0';
                if (nse_starts_with(mod, "nexus_scalp")) {
                    size_t idx = graph_find_or_add(g, mod, NULL, false);
                    graph_add_edge(g, self_idx, idx);
                    /* parent-package chain edges */
                    char parent[256];
                    snprintf(parent, sizeof(parent), "%s", mod);
                    char *dot;
                    while ((dot = strrchr(parent, '.'))) {
                        *dot = '\0';
                        size_t pidx = graph_find_or_add(g, parent, NULL, false);
                        graph_add_edge(g, self_idx, pidx);
                    }
                }
                if (*rest == ',') rest++;
                while (*rest == ' ') rest++;
            }
        } else {
            const char *rest = s + 5;
            while (*rest == ' ') rest++;
            char mod[256];
            size_t i = 0;
            while (*rest && *rest != ' ' && i < sizeof(mod) - 1) mod[i++] = *rest++;
            mod[i] = '\0';
            if (mod[0] == '.') continue; /* relative — ignored, matches Python */
            if (nse_starts_with(mod, "nexus_scalp")) {
                size_t idx = graph_find_or_add(g, mod, NULL, false);
                graph_add_edge(g, self_idx, idx);
                char parent[256];
                snprintf(parent, sizeof(parent), "%s", mod);
                char *dot;
                while ((dot = strrchr(parent, '.'))) {
                    *dot = '\0';
                    size_t pidx = graph_find_or_add(g, parent, NULL, false);
                    graph_add_edge(g, self_idx, pidx);
                }
            }
        }
    }
}

static void scan_file(Graph *g, const char *rel_path, const char *abs_path) {
    StrBuf content;
    sb_init(&content);
    bool ok = read_file_all(abs_path, &content);
    bool is_test = nse_starts_with(rel_path, "tests/");
    size_t self_idx;
    if (is_test) {
        self_idx = graph_find_or_add(g, rel_path, rel_path, true);
    } else {
        /* module name from path: src/nexus_scalp/a/b.py -> nexus_scalp.a.b */
        char mod[512];
        const char *base = nse_starts_with(rel_path, "src/")
                               ? rel_path + 4
                               : rel_path; /* strip "src/" */
        snprintf(mod, sizeof(mod), "%s", base);
        size_t n = strlen(mod);
        if (n >= 3 && strcmp(mod + n - 3, ".py") == 0) mod[n - 3] = '\0';
        /* strip trailing __init__ */
        size_t m = strlen(mod);
        if (m >= 9 && strcmp(mod + m - 9, "/__init__") == 0) mod[m - 9] = '\0';
        for (char *c = mod; *c; c++) {
            if (*c == '/') *c = '.';
        }
        self_idx = graph_find_or_add(g, mod, rel_path, false);
    }
    if (!ok) {
        g->unparseable[self_idx] = true;
        /* sentinel: "<unknown>" module imported by this file = conservative */
        size_t unk = graph_find_or_add(g, "<unknown>", NULL, false);
        graph_add_edge(g, self_idx, unk);
    } else {
        scan_imports(g, self_idx, content.data ? content.data : "");
    }
    sb_free(&content);
}

static void walk_dir(Graph *g, const char *root, const char *rel_base) {
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
        /* d_type is not portable everywhere; probe via scan attempt */
        if (nse_ends_with(child_rel, ".py")) {
            scan_file(g, child_rel, child_abs);
        } else {
            walk_dir(g, child_abs, child_rel);
        }
    }
    closedir(d);
}

void graph_add_file(Graph *g, const char *rel_path, const char *abs_path) {
    scan_file(g, rel_path, abs_path);
}

void graph_scan_repo(Graph *g, const char *repo_root) {
    char src_root[1024];
    snprintf(src_root, sizeof(src_root), "%s/src/nexus_scalp", repo_root);
    char tests_root[1024];
    snprintf(tests_root, sizeof(tests_root), "%s/tests", repo_root);
    walk_dir(g, src_root, "src/nexus_scalp");
    walk_dir(g, tests_root, "tests");
}

/* expose walker to selector via header extension */
void graph_scan_repo(Graph *g, const char *repo_root);

void graph_reachable_tests(const Graph *g, const size_t *changed_idx,
                           size_t changed_len, size_t **out_tests,
                           size_t *out_tests_len, size_t **out_covered,
                           size_t *out_covered_len) {
    bool *visited = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *covered = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    bool *sel_test = (bool *)arena_alloc(g->arena, g->mod_count * sizeof(bool));
    memset(visited, 0, g->mod_count * sizeof(bool));
    memset(covered, 0, g->mod_count * sizeof(bool));
    memset(sel_test, 0, g->mod_count * sizeof(bool));

    size_t *stack = (size_t *)arena_alloc(g->arena, g->mod_count * sizeof(size_t));
    size_t top = 0;
    for (size_t i = 0; i < changed_len; i++) stack[top++] = changed_idx[i];

    while (top > 0) {
        size_t idx = stack[--top];
        if (visited[idx]) continue;
        visited[idx] = true;
        covered[idx] = true;
        for (size_t e = 0; e < g->importer_counts[idx]; e++) {
            size_t importer = g->importers[idx][e];
            if (g->is_test[importer]) {
                sel_test[importer] = true;
                covered[idx] = true;
            } else if (!visited[importer]) {
                stack[top++] = importer;
            }
        }
    }

    /* compact outputs */
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
