/* test_selector.c — plan assembly (canonical JSON form, same digest basis).
 *
 * Selection semantics:
 *   - global invalidation -> conservative fallback expansion. THE canonical
 *     policy lives here and ONLY here (is_global_invalidation): no other
 *     file decides it. pyproject.toml is classified semantically by the
 *     scope string Python passes in (--pyproject-scope, computed from the
 *     parsed base/head toml diff); every other global file invalidates
 *     unconditionally. See docs/C17_ARCHITECTURE.md.
 *   - unparseable python -> conservative expansion
 *   - missing test mapping (changed source module with no reachable test)
 *     -> reported by FILE PATH + conservative expansion
 *   - safety-critical prefixes -> mandatory expansion of the fallback suites
 *   - changed test files are always selected themselves
 *   - docs-only / no-python changes -> explicit skipped_groups entry
 */
#include "nse_ci_engine.h"

#if defined(_WIN32)
#include <io.h>
#define access _access
#define F_OK 0
#else
#include <unistd.h>
#endif
#include "nse_dirent.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *GLOBAL_INVALIDATION_FILES[] = {
    "requirements.lock", "requirements.txt", "uv.lock",
    "tests/conftest.py", "pytest.ini", "setup.cfg", "tox.ini",
};
static const char *GLOBAL_INVALIDATION_PREFIXES[] = { "configs/" };
static const char *FALLBACK_SUITE_FILES[] = { "tests/critical_suite.txt",
                                              "tests/fast_suite.txt" };

/* Semantic verdict for a pyproject.toml change, from the scope string
 * Python computed by diffing the parsed base/head toml
 * (classify_pyproject_diff in run_test_plan.py). Tokens:
 *   pytest — [tool.pytest.ini_options] (collection/behaviour for every test)
 *   deps   — [project] dependencies / requires-python / optional-dependencies
 *   build  — [build-system] / [tool.setuptools.*] (what tests run against)
 *   mypy   — [tool.mypy.*] only (type-checking; mypy runs in its own step)
 *   lint   — [tool.ruff.*] only (lint; ruff runs in its own step)
 *   meta   — [project] name/version/description only
 *   scripts — [project.scripts] (console entry points; fail closed to
 *     global — no finer CLI selection exists)
 *   unknown — an unrecognised section, or the diff could not be classified
 * Global iff any token is pytest/deps/build/scripts/unknown. A NULL scope (old
 * callers that do not pass --pyproject-scope) -> global (fail closed).
 * An EMPTY scope means "no semantic difference" (comment/whitespace-only
 * diff) -> NOT global.
 * mypy/lint/meta-only changes are NOT global: they select the tests/ci
 * integrity suite (CI-tooling scope, same as a workflow change) instead
 * of the full critical suite. */
static bool pyproject_scope_is_global(const char *scope) {
    if (!scope) return true; /* unclassified -> fail closed */
    if (!*scope) return false; /* no semantic difference */
    /* comma-separated tokens; global on pytest/deps/build/scripts/unknown */
    const char *p = scope;
    while (*p) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);
        if ((len == 6 && strncmp(p, "pytest", 6) == 0) ||
            (len == 4 && strncmp(p, "deps", 4) == 0) ||
            (len == 5 && strncmp(p, "build", 5) == 0) ||
            (len == 7 && strncmp(p, "scripts", 7) == 0) ||
            (len == 7 && strncmp(p, "unknown", 7) == 0)) {
            return true;
        }
        p = comma ? comma + 1 : p + len;
    }
    return false;
}

static bool is_global_invalidation(const char *path, const char *pyproject_scope) {
    if (strcmp(path, "pyproject.toml") == 0) {
        return pyproject_scope_is_global(pyproject_scope);
    }
    for (size_t i = 0;
         i < sizeof(GLOBAL_INVALIDATION_FILES) / sizeof(GLOBAL_INVALIDATION_FILES[0]);
         i++) {
        if (strcmp(path, GLOBAL_INVALIDATION_FILES[i]) == 0) return true;
    }
    for (size_t i = 0;
         i < sizeof(GLOBAL_INVALIDATION_PREFIXES) / sizeof(GLOBAL_INVALIDATION_PREFIXES[0]);
         i++) {
        if (nse_starts_with(path, GLOBAL_INVALIDATION_PREFIXES[i])) return true;
    }
    return false;
}

/* Append one evidence note ("; "-separated, single string in the plan). */
static void note_append(char *buf, size_t buf_len, const char *text) {
    if (buf[0]) {
        strncat(buf, "; ", buf_len - strlen(buf) - 1);
    }
    strncat(buf, text, buf_len - strlen(buf) - 1);
}

/* Selected-entry registry: test id -> reason flags (bitmask). */
#define R_STATIC 1u
#define R_TEST_CHANGED 2u
#define R_FALLBACK 4u
#define R_SAFETY 8u
#define R_CI_TOOLING 16u

typedef struct SelEntry {
    char *id;
    unsigned reasons;
} SelEntry;

typedef struct SelRegistry {
    Arena *arena;
    SelEntry *items;
    size_t count;
    size_t cap;
} SelRegistry;

static void sel_add(SelRegistry *r, const char *id, unsigned reasons) {
    for (size_t i = 0; i < r->count; i++) {
        if (strcmp(r->items[i].id, id) == 0) {
            r->items[i].reasons |= reasons;
            return;
        }
    }
    if (r->count == r->cap) {
        size_t ncap = r->cap ? r->cap * 2 : 64;
        SelEntry *ni = (SelEntry *)arena_alloc(r->arena, ncap * sizeof(SelEntry));
        if (r->count) memcpy(ni, r->items, r->count * sizeof(SelEntry));
        r->items = ni;
        r->cap = ncap;
    }
    r->items[r->count].id = arena_strdup(r->arena, id);
    r->items[r->count].reasons = reasons;
    r->count++;
}

static int cmp_strp(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

static int sel_cmp(const void *a, const void *b) {
    const SelEntry *ea = (const SelEntry *)a;
    const SelEntry *eb = (const SelEntry *)b;
    return strcmp(ea->id, eb->id);
}

static void load_fallback_suite(const char *repo_root, const char *suite_file,
                                StrBuf *out_nul) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", repo_root, suite_file);
    StrBuf content;
    sb_init(&content);
    if (!read_file_all(path, &content)) {
        sb_free(&content);
        return;
    }
    /* split lines, trim, skip comments, append NUL-terminated into out */
    const char *p = content.data ? content.data : "";
    while (*p) {
        const char *eol = strchr(p, '\n');
        size_t len = eol ? (size_t)(eol - p) : strlen(p);
        char line[1024];
        size_t take = len < sizeof(line) - 1 ? len : sizeof(line) - 1;
        memcpy(line, p, take);
        line[take] = '\0';
        p = eol ? eol + 1 : p + len;
        char *s = line;
        while (*s == ' ' || *s == '\t') s++;
        if (*s == '\0' || *s == '#') continue;
        sb_append_n(out_nul, s, strlen(s) + 1);
    }
    sb_free(&content);
}

int engine_build_plan_ex(const char *repo_root, const char *changed_files,
                      const PlanOptions *opt, StrBuf *json_out,
                      char digest_hex[65], const char *metadata_json) {
    if (!repo_root || !changed_files || !opt || !json_out || !digest_hex) return -1;

    Arena arena;
    arena_init(&arena, 1u << 18);
    int rc = 0;

    /* parse changed files (NUL-separated) */
    typedef struct { char *path; } ChangedEntry;
    ChangedEntry changed[4096];
    size_t changed_count = 0;
    for (const char *p = changed_files; *p && changed_count < 4096; p += strlen(p) + 1) {
        char norm[1024];
        snprintf(norm, sizeof(norm), "%s", p);
        normalize_path(norm);
        changed[changed_count++].path = arena_strdup(&arena, norm);
    }

    SelRegistry reg;
    reg.arena = &arena;
    reg.items = NULL;
    reg.count = reg.cap = 0;

    char *fallback_reason = NULL;
    char note_buf[2048];
    note_buf[0] = '\0';
    bool hub_bounded = false;
    bool metadata_used = false; /* the graph really came from AST metadata */

    /* classify changed sets */
    bool has_python_src = false, has_test_py = false, has_global = false;
    bool pyproject_tooling = false; /* pyproject.toml, non-global scope */
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_ends_with(f, ".py") && nse_starts_with(f, "src/")) has_python_src = true;
        if (nse_starts_with(f, "tests/") && nse_ends_with(f, ".py")) has_test_py = true;
        if (is_global_invalidation(f, opt->pyproject_scope)) has_global = true;
        if (strcmp(f, "pyproject.toml") == 0 &&
            !is_global_invalidation(f, opt->pyproject_scope)) {
            pyproject_tooling = true;
        }
    }

    /* files list as NUL-separated for graph scanner */
    StrBuf py_files;
    sb_init(&py_files);
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_ends_with(f, ".py") && nse_starts_with(f, "src/")) {
            sb_append_n(&py_files, f, strlen(f) + 1);
        }
    }

    /* missing-mapping paths (function scope: emitted in the canonical JSON) */
    char *missing[256];
    size_t missing_len = 0;

    bool full_required = opt->full_suite_required ||
                         (opt->event && strcmp(opt->event, "schedule") == 0);

    if (full_required) {
        fallback_reason = arena_strdup(&arena, "full validation requested by event/policy");
    } else if (has_global) {
        StrBuf r;
        sb_init(&r);
        sb_append(&r, "global invalidation: ");
        bool first = true;
        for (size_t i = 0; i < changed_count && i < 999; i++) {
            if (is_global_invalidation(changed[i].path, opt->pyproject_scope)) {
                if (!first) sb_append(&r, ", ");
                sb_append(&r, changed[i].path);
                if (strcmp(changed[i].path, "pyproject.toml") == 0 &&
                    opt->pyproject_scope && *opt->pyproject_scope) {
                    sb_append(&r, " (");
                    sb_append(&r, opt->pyproject_scope);
                    sb_append(&r, ")");
                }
                first = false;
                if (r.len > 300) { sb_append(&r, ", ..."); break; }
            }
        }
        fallback_reason = arena_strdup(&arena, r.data ? r.data : "global invalidation");
        sb_free(&r);
    } else if (has_python_src || has_test_py) {
        Graph g;
        graph_init(&g, &arena);
        if (metadata_json) {
            /* Production path: the graph comes from the AST metadata
             * Python extracted. Malformed metadata FAILS CLOSED — falling
             * back to the weaker line scanner would silently change
             * selection semantics (and hide the extractor bug). */
            char meta_err[256] = "";
            if (graph_build_from_metadata(&g, repo_root, metadata_json, meta_err,
                                          sizeof(meta_err)) != 0) {
                fprintf(stderr, "plan: metadata graph build failed: %s\n",
                        meta_err[0] ? meta_err : "unknown error");
                sb_free(&py_files);
                arena_free(&arena);
                return -1;
            }
            metadata_used = true;
        } else {
            graph_scan_repo(&g, repo_root);
        }
        if (metadata_used && g.dynamic_unresolved_files > 0) {
            /* Non-literal dynamic imports are counted, not expanded (see
             * ast_metadata.c); the note keeps the residual visible. */
            char dyn_note[128];
            snprintf(dyn_note, sizeof(dyn_note),
                     "%zu file(s) use non-literal dynamic imports "
                     "(statically unresolvable)",
                     g.dynamic_unresolved_files);
            note_append(note_buf, sizeof(note_buf), dyn_note);
        }

        bool any_unparseable = false;
        for (size_t i = 0; i < g.mod_count; i++) {
            if (g.unparseable[i]) any_unparseable = true;
        }
        if (any_unparseable) {
            fallback_reason = arena_strdup(
                &arena, "unparseable python file encountered \xe2\x80\x94 conservative expansion");
        }
        if (g.relative_unresolved) {
            /* Uncertainty reason (stable string, part of the plan evidence). */
            fallback_reason = fallback_reason
                                  ? fallback_reason
                                  : arena_strdup(
                                        &arena,
                                        "unresolved relative imports \xe2\x80\x94 graph uncertain, conservative expansion");
        }

        /* changed module indices */
        size_t *changed_idx =
            (size_t *)arena_alloc(&arena, (changed_count ? changed_count : 1) * sizeof(size_t));
        size_t changed_idx_len = 0;
        int dbg = getenv("NSE_PLAN_DEBUG") ? 1 : 0;
        if (dbg) fprintf(stderr, "[dbg] mod_count=%zu changed_count=%zu\n", g.mod_count, changed_count);
        for (size_t i = 0; i < changed_count; i++) {
            const char *f = changed[i].path;
            if (dbg) fprintf(stderr, "[dbg] changed[%zu]=%s\n", i, f);
            /* changed TEST-tree files are reachability roots too (their
             * importers — e.g. tests using a shared helper — must run) */
            if (nse_starts_with(f, "tests/") && nse_ends_with(f, ".py")) {
                size_t idx;
                bool tf = graph_find(&g, f, &idx);
                if (dbg) fprintf(stderr, "[dbg]   test-root find=%d\n", tf);
                if (tf) {
                    changed_idx[changed_idx_len++] = idx;
                }
                continue;
            }
            if (!(nse_ends_with(f, ".py") && nse_starts_with(f, "src/"))) continue;
            /* recompute module name exactly like the scanner does */
            char mod[512];
            const char *base = f + 4; /* strip "src/" */
            snprintf(mod, sizeof(mod), "%s", base);
            size_t n = strlen(mod);
            if (n >= 3 && strcmp(mod + n - 3, ".py") == 0) mod[n - 3] = '\0';
            size_t m = strlen(mod);
            if (m >= 9 && strcmp(mod + m - 9, "/__init__") == 0) mod[m - 9] = '\0';
            for (char *c = mod; *c; c++) if (*c == '/') *c = '.';
            if (dbg) fprintf(stderr, "[dbg]   mod=%s\n", mod);
            /* AST-metadata graphs key nodes by PATH; line-scan graphs key
             * source nodes by dotted module name. Try BOTH identities. */
            bool found = false;
            {
                size_t pidx;
                if (graph_find(&g, f, &pidx)) {
                    changed_idx[changed_idx_len++] = pidx;
                    found = true;
                }
            }
            if (!found) {
                for (size_t k = 0; k < g.mod_count; k++) {
                    if (strcmp(g.mod_names[k], mod) == 0) {
                        changed_idx[changed_idx_len++] = k;
                        found = true;
                        break;
                    }
                }
            }
            if (dbg) fprintf(stderr, "[dbg]   found=%d changed_idx_len=%zu\n", found, changed_idx_len);
            if (!found) {
                /* file exists but was not scanned (outside scan roots) —
                 * conservative: treat as missing mapping */
                StrBuf r;
                sb_init(&r);
                sb_append(&r, "missing test mapping for: ");
                sb_append(&r, f);
                if (!fallback_reason) {
                    fallback_reason = arena_strdup(&arena, r.data ? r.data : "");
                }
                sb_free(&r);
            }
        }

        size_t *tests, *covered;
        size_t tests_len, covered_len;
        graph_reachable_tests(&g, changed_idx, changed_idx_len, &tests, &tests_len,
                              &covered, &covered_len, &hub_bounded);
        if (dbg) {
            fprintf(stderr, "[dbg] covered_len=%zu tests_len=%zu rel_unresolved=%d\n",
                    covered_len, tests_len, g.relative_unresolved);
            for (size_t i = 0; i < changed_idx_len; i++) {
                fprintf(stderr, "[dbg]   changed idx=%zu name=%s\n", changed_idx[i],
                        g.mod_names[changed_idx[i]]);
            }
            for (size_t i = 0; i < tests_len; i++) {
                fprintf(stderr, "[dbg]   selected test: %s\n", g.mod_names[tests[i]]);
            }
        }
        /* `covered` is a list of COVERED MODULE INDICES (not a bool map) —
         * build the boolean lookup the missing-mapping check needs. */
        bool *covered_map =
            (bool *)arena_alloc(&arena, g.mod_count * sizeof(bool));
        memset(covered_map, 0, g.mod_count * sizeof(bool));
        for (size_t i = 0; i < covered_len; i++) {
            covered_map[covered[i]] = true;
        }
        for (size_t i = 0; i < tests_len; i++) {
            sel_add(&reg, g.mod_names[tests[i]], R_STATIC);
        }

        /* missing mappings: changed modules not covered — collected as file
         * PATHS (sorted later for canonical JSON parity with Python). */


        for (size_t i = 0; i < changed_idx_len && missing_len < 256; i++) {
            size_t idx = changed_idx[i];
            if (!covered_map[idx]) {
                missing[missing_len++] =
                    arena_strdup(&arena,
                                 g.mod_paths[idx] ? g.mod_paths[idx]
                                                  : g.mod_names[idx]);
                StrBuf r;
                sb_init(&r);
                sb_append(&r, "missing test mapping for: ");
                sb_append(&r, missing[missing_len - 1]);
                if (!fallback_reason) {
                    fallback_reason = arena_strdup(&arena, r.data ? r.data : "");
                }
                sb_free(&r);
            }
        }
        /* not-scanned src files also count as missing mappings (a file the
         * graph never saw — by dotted name OR by path identity). */
        for (size_t i = 0; i < changed_count && missing_len < 256; i++) {
            const char *f = changed[i].path;
            if (!(nse_ends_with(f, ".py") && nse_starts_with(f, "src/"))) continue;
            char mod[512];
            const char *base2 = f + 4;
            snprintf(mod, sizeof(mod), "%s", base2);
            size_t n2 = strlen(mod);
            if (n2 >= 3 && strcmp(mod + n2 - 3, ".py") == 0) mod[n2 - 3] = '\0';
            for (char *c = mod; *c; c++) if (*c == '/') *c = '.';
            bool in_graph = false;
            for (size_t k = 0; k < g.mod_count; k++) {
                if (strcmp(g.mod_names[k], mod) == 0 ||
                    (g.mod_paths[k] && strcmp(g.mod_paths[k], f) == 0)) {
                    in_graph = true;
                    break;
                }
            }
            if (!in_graph) missing[missing_len++] = arena_strdup(&arena, f);
        }
        /* deduplicate (a path can land here twice across the two loops) */
        if (missing_len > 1) {
            qsort(missing, missing_len, sizeof(char *), cmp_strp);
            size_t w = 1;
            for (size_t r2 = 1; r2 < missing_len; r2++) {
                if (strcmp(missing[r2], missing[r2 - 1]) != 0) missing[w++] = missing[r2];
            }
            missing_len = w;
        }
        if (missing_len > 1) qsort(missing, missing_len, sizeof(char *), cmp_strp);
        sb_free(&py_files);
    }

    /* changed test files always selected */
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_starts_with(f, "tests/") && nse_ends_with(f, ".py")) {
            sel_add(&reg, f, R_TEST_CHANGED);
        }
    }

    /* fallback expansion */
    if (fallback_reason) {
        StrBuf suite;
        sb_init(&suite);
        for (size_t i = 0;
             i < sizeof(FALLBACK_SUITE_FILES) / sizeof(FALLBACK_SUITE_FILES[0]); i++) {
            load_fallback_suite(repo_root, FALLBACK_SUITE_FILES[i], &suite);
        }
        for (const char *p = suite.data ? suite.data : ""; *p; p += strlen(p) + 1) {
            sel_add(&reg, p, R_FALLBACK);
        }
        sb_free(&suite);
        /* Append, do not overwrite: notes recorded earlier (e.g. the
         * dynamic-import note) are evidence too. */
        note_append(note_buf, sizeof(note_buf), fallback_reason);
    }

    /* mandatory safety expansion */
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_ends_with(f, ".py") && nse_starts_with(f, "src/") &&
            graph_path_is_safety(f)) {
            StrBuf suite;
            sb_init(&suite);
            for (size_t k = 0;
                 k < sizeof(FALLBACK_SUITE_FILES) / sizeof(FALLBACK_SUITE_FILES[0]);
                 k++) {
                load_fallback_suite(repo_root, FALLBACK_SUITE_FILES[k], &suite);
            }
            for (const char *p = suite.data ? suite.data : ""; *p; p += strlen(p) + 1) {
                sel_add(&reg, p, R_SAFETY);
            }
            sb_free(&suite);
            char safety_note[1024];
            snprintf(safety_note, sizeof(safety_note), "safety-critical modules changed: %s", f);
            note_append(note_buf, sizeof(note_buf), safety_note);
            break; /* one note line is enough for the evidence trail */
        }
    }

    /* Hub-bounded reachability: recorded ONCE in the plan notes so the
     * evidence trail shows the selection was truncated (and how: the
     * hub's direct test importers are included by the bounded one-hop
     * expansion). Previously this sat inside the loop above and was
     * appended once per changed file. */
    if (hub_bounded) {
        note_append(note_buf, sizeof(note_buf),
                    "hub-bounded reachability: modules with >25 importers are "
                    "selection boundaries; a reached hub's direct test "
                    "importers are included only when there are at most 25 "
                    "of them (bounded one-hop expansion)");
    }

    /* CI tooling:
     * scripts/ci/<name>.py -> tests/ci/test_<name>.py when present;
     * workflow changes select the whole tests/ci integrity suite.
     * A pyproject.toml change whose semantic scope is NOT global
     * (mypy/lint/meta only) is CI-tooling scope too: it selects the
     * tests/ci integrity suite as a smoke check instead of the full
     * critical suite. */
    int workflows_changed = 0;
    for (size_t i = 0; i < changed_count; i++) {
        if (nse_starts_with(changed[i].path, ".github/workflows/")) {
            workflows_changed = 1;
            break;
        }
    }
    if (workflows_changed || pyproject_tooling) {
        char ci_dir[1024];
        snprintf(ci_dir, sizeof(ci_dir), "%s/tests/ci", repo_root);
        DIR *d = opendir(ci_dir);
        if (d) {
            struct dirent *ent;
            while ((ent = readdir(d)) != NULL) {
                if (nse_starts_with(ent->d_name, "test_") &&
                    nse_ends_with(ent->d_name, ".py")) {
                    char rel[1200];
                    snprintf(rel, sizeof(rel), "tests/ci/%s", ent->d_name);
                    sel_add(&reg, rel, R_CI_TOOLING);
                }
            }
            closedir(d);
        }
    }
    if (pyproject_tooling) {
        /* Evidence for why the full suite did NOT run on a pyproject.toml
         * change: the semantic scope excludes pytest/deps/build. */
        char tool_note[256];
        snprintf(tool_note, sizeof(tool_note),
                 "pyproject.toml change limited to %s — CI-tooling scope, "
                 "full suite not required",
                 opt->pyproject_scope && *opt->pyproject_scope
                     ? opt->pyproject_scope
                     : "no semantic difference");
        note_append(note_buf, sizeof(note_buf), tool_note);
    }
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_starts_with(f, "scripts/ci/") && nse_ends_with(f, ".py")) {
            const char *base = f + strlen("scripts/ci/");
            char cand[1200];
            char fs_path[1400];
            snprintf(cand, sizeof(cand), "tests/ci/test_%s", base);
            snprintf(fs_path, sizeof(fs_path), "%s/%s", repo_root, cand);
            if (access(fs_path, F_OK) == 0) {
                sel_add(&reg, cand, R_CI_TOOLING);
            }
        }
    }

    /* docs-only / nothing-relevant skip group */
    bool any_relevant = has_python_src || has_test_py;
    if (!any_relevant) {
        /* emitted below in the JSON */
    }

    /* sort registry */
    if (reg.count > 1) qsort(reg.items, reg.count, sizeof(SelEntry), sel_cmp);

    /* ---------------- canonical JSON (sorted keys, compact) ----------------
     * Python: json.dumps(obj, sort_keys=True, separators=(",", ":"))
     * Top-level sorted keys:
     *   cached_results, commit_sha, fallback_reason, full_suite_required,
     *   metadata_used, missing_mappings, notes, plan_version,
     *   pyproject_scope, selected_tests, selection_mode, skipped_groups
     * (plan_digest excluded, then computed over these exact bytes)
     */
    StrBuf sb;
    sb_init(&sb);
    sb_append(&sb, "{\"cached_results\":[]");
    sb_append(&sb, ",\"commit_sha\":");
    json_write_escaped(&sb, opt->commit_sha ? opt->commit_sha : "");
    sb_append(&sb, ",\"fallback_reason\":");
    if (fallback_reason) json_write_escaped(&sb, fallback_reason);
    else sb_append(&sb, "null");
    sb_append(&sb, ",\"full_suite_required\":");
    sb_append(&sb, fallback_reason ? "true" : "false");
    sb_append(&sb, ",\"metadata_used\":");
    sb_append(&sb, metadata_used ? "true" : "false");
    sb_append(&sb, ",\"missing_mappings\":");
    json_write_str_array(&sb, missing, missing_len);
    sb_append(&sb, ",\"notes\":[");
    if (note_buf[0]) json_write_escaped(&sb, note_buf);
    sb_append(&sb, "]");
    sb_append(&sb, ",\"plan_version\":\"" NSE_PLAN_VERSION "\"");
    sb_append(&sb, ",\"pyproject_scope\":");
    if (opt->pyproject_scope) json_write_escaped(&sb, opt->pyproject_scope);
    else sb_append(&sb, "null");
    sb_append(&sb, ",\"selected_tests\":[");
    for (size_t i = 0; i < reg.count; i++) {
        if (i) sb_append_ch(&sb, ',');
        sb_append(&sb, "{\"id\":");
        json_write_escaped(&sb, reg.items[i].id);
        sb_append(&sb, ",\"priority\":\"");
        sb_append(&sb, (reg.items[i].reasons & (R_FALLBACK | R_SAFETY)) ? "critical" : "normal");
        sb_append(&sb, "\",\"reason\":[");
        /* reason list must be SORTED (Python sorts it):
         * conservative_fallback < mandatory_safety < static_dependency < test_changed */
        bool any = false;
        if (reg.items[i].reasons & R_CI_TOOLING) {
            sb_append(&sb, "\"ci_tooling\"");
            any = true;
        }
        if (reg.items[i].reasons & R_FALLBACK) {
            if (any) sb_append_ch(&sb, ',');
            sb_append(&sb, "\"conservative_fallback\"");
            any = true;
        }
        if (reg.items[i].reasons & R_SAFETY) {
            if (any) sb_append_ch(&sb, ',');
            sb_append(&sb, "\"mandatory_safety\"");
            any = true;
        }
        if (reg.items[i].reasons & R_STATIC) {
            if (any) sb_append_ch(&sb, ',');
            sb_append(&sb, "\"static_dependency\"");
            any = true;
        }
        if (reg.items[i].reasons & R_TEST_CHANGED) {
            if (any) sb_append_ch(&sb, ',');
            sb_append(&sb, "\"test_changed\"");
        }
        sb_append(&sb, "]}");
    }
    sb_append(&sb, "]");
    sb_append(&sb, ",\"selection_mode\":\"");
    sb_append(&sb, fallback_reason ? "expanded" : "targeted");
    sb_append(&sb, "\",\"skipped_groups\":[");
    if (!any_relevant) {
        sb_append(&sb, "{\"name\":\"python-tests\",\"reason\":\"no python source or test files changed\"}");
    }
    sb_append(&sb, "]}");

    sha256_hex((const uint8_t *)(sb.data ? sb.data : ""), sb.len, digest_hex);
    sb_append(json_out, sb.data ? sb.data : "");
    sb_free(&sb);
    arena_free(&arena);
    return rc;
}

int engine_build_plan(const char *repo_root, const char *changed_files,
                      const PlanOptions *opt, StrBuf *json_out, char digest_hex[65]) {
    return engine_build_plan_ex(repo_root, changed_files, opt, json_out,
                                digest_hex, NULL);
}
