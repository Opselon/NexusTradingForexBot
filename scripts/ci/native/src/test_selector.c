/* test_selector.c — plan assembly with byte-identical JSON to
 * scripts/ci/test_planner.py (canonical form, same digest basis).
 *
 * Semantics mirrored from the Python planner (the trusted reference):
 *   - global invalidation files/prefixes -> conservative fallback expansion
 *   - unparseable python -> conservative expansion
 *   - missing test mapping (changed source module with no reachable test)
 *     -> reported by FILE PATH + conservative expansion
 *   - safety-critical prefixes -> mandatory expansion of the fallback suites
 *   - changed test files are always selected themselves
 *   - docs-only / no-python changes -> explicit skipped_groups entry
 */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *GLOBAL_INVALIDATION_FILES[] = {
    "pyproject.toml", "requirements.lock", "requirements.txt", "uv.lock",
    "tests/conftest.py", "pytest.ini", "setup.cfg", "tox.ini",
};
static const char *GLOBAL_INVALIDATION_PREFIXES[] = { "configs/" };
static const char *FALLBACK_SUITE_FILES[] = { "tests/critical_suite.txt",
                                              "tests/fast_suite.txt" };

static bool is_global_invalidation(const char *path) {
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

/* Selected-entry registry: test id -> reason flags (bitmask). */
#define R_STATIC 1u
#define R_TEST_CHANGED 2u
#define R_FALLBACK 4u
#define R_SAFETY 8u

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

int engine_build_plan(const char *repo_root, const char *changed_files,
                      const PlanOptions *opt, StrBuf *json_out,
                      char digest_hex[65]) {
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

    /* classify changed sets */
    bool has_python_src = false, has_test_py = false, has_global = false;
    for (size_t i = 0; i < changed_count; i++) {
        const char *f = changed[i].path;
        if (nse_ends_with(f, ".py") && nse_starts_with(f, "src/")) has_python_src = true;
        if (nse_starts_with(f, "tests/") && nse_ends_with(f, ".py")) has_test_py = true;
        if (is_global_invalidation(f)) has_global = true;
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
            if (is_global_invalidation(changed[i].path)) {
                if (!first) sb_append(&r, ", ");
                sb_append(&r, changed[i].path);
                first = false;
                if (r.len > 300) { sb_append(&r, ", ..."); break; }
            }
        }
        fallback_reason = arena_strdup(&arena, r.data ? r.data : "global invalidation");
        sb_free(&r);
    } else if (has_python_src || has_test_py) {
        Graph g;
        graph_init(&g, &arena);
        graph_scan_repo(&g, repo_root);

        bool any_unparseable = false;
        for (size_t i = 0; i < g.mod_count; i++) {
            if (g.unparseable[i]) any_unparseable = true;
        }
        if (any_unparseable) {
            fallback_reason = arena_strdup(
                &arena, "unparseable python file encountered \xe2\x80\x94 conservative expansion");
        }

        /* changed module indices */
        size_t *changed_idx =
            (size_t *)arena_alloc(&arena, (changed_count ? changed_count : 1) * sizeof(size_t));
        size_t changed_idx_len = 0;
        for (size_t i = 0; i < changed_count; i++) {
            const char *f = changed[i].path;
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
            bool found = false;
            for (size_t k = 0; k < g.mod_count; k++) {
                if (strcmp(g.mod_names[k], mod) == 0) {
                    changed_idx[changed_idx_len++] = k;
                    found = true;
                    break;
                }
            }
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
                              &covered, &covered_len);
        for (size_t i = 0; i < tests_len; i++) {
            sel_add(&reg, g.mod_names[tests[i]], R_STATIC);
        }

        /* missing mappings: changed modules not covered */
        for (size_t i = 0; i < changed_idx_len; i++) {
            size_t idx = changed_idx[i];
            if (!covered[idx]) {
                StrBuf r;
                sb_init(&r);
                sb_append(&r, "missing test mapping for: ");
                sb_append(&r, g.mod_paths[idx] ? g.mod_paths[idx] : g.mod_names[idx]);
                if (!fallback_reason) {
                    fallback_reason = arena_strdup(&arena, r.data ? r.data : "");
                }
                sb_free(&r);
            }
        }
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
        snprintf(note_buf, sizeof(note_buf), "%s", fallback_reason);
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
            if (note_buf[0]) {
                strncat(note_buf, "; ", sizeof(note_buf) - strlen(note_buf) - 1);
            }
            strncat(note_buf, safety_note, sizeof(note_buf) - strlen(note_buf) - 1);
            break; /* one note line is enough for the evidence trail */
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
     *   commit_sha, fallback_reason, full_suite_required, missing_mappings,
     *   notes, plan_version, selected_tests, selection_mode, skipped_groups
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
    sb_append(&sb, ",\"missing_mappings\":[]");
    /* NOTE: missing mappings are folded into fallback_reason in v1 (paths are
     * human-actionable); the array stays empty for schema parity. */
    sb_append(&sb, ",\"notes\":[");
    if (note_buf[0]) json_write_escaped(&sb, note_buf);
    sb_append(&sb, "]");
    sb_append(&sb, ",\"plan_version\":\"" NSE_PLAN_VERSION "\"");
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
        if (reg.items[i].reasons & R_FALLBACK) {
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
