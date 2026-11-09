/* main.c — CLI surface. Malformed native output must never produce a
 * successful CI verdict: every subcommand exits non-zero on ANY failure and
 * callers (Python) treat non-zero as "native unavailable / failed". */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(void) {
    fprintf(stderr,
        "nse-ci-engine v" NSE_ENGINE_VERSION "\n"
        "usage:\n"
        "  nse-ci-engine plan --repo-root DIR --files \"a.py b.py\" [--commit-sha SHA]\n"
        "                     [--event NAME] [--full-suite] [--out FILE]\n"
        "                     [--metadata FILE] [--pyproject-scope SCOPE]\n"
        "  nse-ci-engine classify --base SHA --head SHA [--repo-root DIR]\n"
        "  nse-ci-engine fingerprint --repo-root DIR --test tests/unit/test_x.py\n"
        "  nse-ci-engine validate-plan plan.json\n"
        "  nse-ci-engine resource-info\n"
        "  nse-ci-engine selftest\n"
        "  nse-ci-engine selftest-fuzz --iterations N\n"
        "  nse-ci-engine version\n");
}

static int cmd_plan(int argc, char **argv) {
    const char *repo_root = ".";
    const char *files = NULL;
    const char *sha = "";
    const char *event = "";
    const char *out = NULL;
    const char *metadata_file = NULL;
    const char *pyproject_scope = NULL;
    bool full = false;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--repo-root") == 0 && i + 1 < argc) repo_root = argv[++i];
        else if (strcmp(argv[i], "--files") == 0 && i + 1 < argc) files = argv[++i];
        else if (strcmp(argv[i], "--commit-sha") == 0 && i + 1 < argc) sha = argv[++i];
        else if (strcmp(argv[i], "--event") == 0 && i + 1 < argc) event = argv[++i];
        else if (strcmp(argv[i], "--full-suite") == 0) full = true;
        else if (strcmp(argv[i], "--out") == 0 && i + 1 < argc) out = argv[++i];
        else if (strcmp(argv[i], "--metadata") == 0 && i + 1 < argc) metadata_file = argv[++i];
        else if (strcmp(argv[i], "--pyproject-scope") == 0 && i + 1 < argc) pyproject_scope = argv[++i];
    }
    if (!files) {
        fprintf(stderr, "plan: --files required\n");
        return 2;
    }
    /* CLI list is space-separated (mirrors the Python CLI contract). */
    StrBuf nul;
    sb_init(&nul);
    const char *p = files;
    while (*p) {
        const char *e = strchr(p, ' ');
        size_t n = e ? (size_t)(e - p) : strlen(p);
        if (n) sb_append_n(&nul, p, n), sb_append_ch(&nul, '\0');
        p = e ? e + 1 : p + n;
    }
    sb_append_ch(&nul, '\0'); /* terminate the NUL-separated list */

    PlanOptions opt = { sha, event, full, pyproject_scope };
    StrBuf json;
    sb_init(&json);
    StrBuf meta;
    sb_init(&meta);
    const char *meta_json = NULL;
    if (metadata_file) {
        if (!read_file_all(metadata_file, &meta)) {
            fprintf(stderr, "plan: cannot read metadata file %s\n", metadata_file);
            sb_free(&nul);
            sb_free(&json);
            sb_free(&meta);
            return 3;
        }
        meta_json = meta.data;
    }
    char digest[65];
    if (engine_build_plan_ex(repo_root, nul.data ? nul.data : "\0", &opt, &json,
                             digest, meta_json) != 0) {
        sb_free(&nul);
        sb_free(&json);
        sb_free(&meta);
        return 3;
    }
    sb_free(&meta);
    /* append digest field (excluded from the hashed bytes by construction) */
    StrBuf final;
    sb_init(&final);
    sb_append_n(&final, json.data ? json.data : "", json.len ? json.len - 1 : 0);
    sb_append(&final, ",\"plan_digest\":\"");
    sb_append(&final, digest);
    sb_append(&final, "\"}");
    printf("%s\n", final.data ? final.data : "{}");
    if (out) {
        FILE *f = fopen(out, "wb");
        if (!f) {
            fprintf(stderr, "plan: cannot write %s\n", out);
            sb_free(&nul); sb_free(&json); sb_free(&final);
            return 3;
        }
        fputs(final.data ? final.data : "{}", f);
        fputc('\n', f);
        fclose(f);
    }
    sb_free(&nul);
    sb_free(&json);
    sb_free(&final);
    return 0;
}

static int cmd_changed_files(int argc, char **argv) {
    const char *base = NULL, *head = NULL;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--base") == 0 && i + 1 < argc) base = argv[++i];
        else if (strcmp(argv[i], "--head") == 0 && i + 1 < argc) head = argv[++i];
    }
    if (!base || !head) {
        fprintf(stderr, "changed-files: --base and --head required\n");
        return 2;
    }
    StrBuf files;
    sb_init(&files);
    if (change_detector_git_files(base, head, &files) != 0) {
        sb_free(&files);
        fprintf(stderr, "changed-files: git diff failed\n");
        return 3;
    }
    for (const char *p = files.data ? files.data : ""; *p; p += strlen(p) + 1) {
        printf("%s\n", p);
    }
    sb_free(&files);
    return 0;
}

static int cmd_classify(int argc, char **argv) {
    const char *base = NULL, *head = NULL;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--base") == 0 && i + 1 < argc) base = argv[++i];
        else if (strcmp(argv[i], "--head") == 0 && i + 1 < argc) head = argv[++i];
    }
    if (!base || !head) {
        fprintf(stderr, "classify: --base and --head required\n");
        return 2;
    }
    StrBuf files;
    sb_init(&files);
    if (change_detector_git_files(base, head, &files) != 0) {
        sb_free(&files);
        fprintf(stderr, "classify: git diff failed\n");
        return 3;
    }
    printf("{\"lanes\":{\"python\":false,\"web\":false,\"js\":false,\"docker\":false,"
           "\"ci\":false,\"docs\":false,\"deps\":false,\"scripts\":false,\"release\":false},"
           "\"files\":[");
    unsigned acc = 0;
    bool first = true;
    for (const char *p = files.data ? files.data : ""; *p; p += strlen(p) + 1) {
        unsigned lanes = classify_path(p);
        acc |= lanes;
        if (!first) printf(",");
        first = false;
        printf("{\"path\":");
        /* reuse writer via a temp StrBuf (printf quoting) */
        StrBuf q;
        sb_init(&q);
        json_write_escaped(&q, p);
        printf("%s", q.data ? q.data : "\"\"");
        sb_free(&q);
        printf(",\"lanes\":%u}", lanes);
    }
    printf("],\"lane_python\":%s,\"lane_js\":%s,\"lane_docs_only\":%s}\n",
           (acc & LANE_PYTHON) ? "true" : "false",
           (acc & LANE_JS) ? "true" : "false",
           (acc & LANE_DOCS) && !(acc & LANE_PYTHON) && !(acc & LANE_JS) ? "true" : "false");
    sb_free(&files);
    return 0;
}

static int cmd_fingerprint(int argc, char **argv) {
    const char *repo_root = ".", *test = NULL;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--repo-root") == 0 && i + 1 < argc) repo_root = argv[++i];
        else if (strcmp(argv[i], "--test") == 0 && i + 1 < argc) test = argv[++i];
    }
    if (!test) {
        fprintf(stderr, "fingerprint: --test required\n");
        return 2;
    }
    StrBuf json;
    sb_init(&json);
    if (engine_fingerprint_test(repo_root, test, &json) != 0) {
        sb_free(&json);
        fprintf(stderr, "fingerprint: cannot read test file\n");
        return 3;
    }
    printf("%s\n", json.data ? json.data : "{}");
    sb_free(&json);
    return 0;
}

static int cmd_validate_plan(int argc, char **argv) {
    const char *path = NULL;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--plan") == 0 && i + 1 < argc) path = argv[++i];
        else if (!path) path = argv[i];
    }
    if (!path) {
        fprintf(stderr, "validate-plan: plan path required\n");
        return 2;
    }
    StrBuf content;
    sb_init(&content);
    if (!read_file_all(path, &content)) {
        fprintf(stderr, "validate-plan: cannot read %s\n", path);
        return 3;
    }
    Arena a;
    arena_init(&a, 1u << 16);
    JsonParser p;
    p.arena = &a;
    JsonValue *v = json_parse(&p, content.data ? content.data : "", content.len);
    int rc = 0;
    char err[128] = "";
    if (!v) {
        snprintf(err, sizeof(err), "%s", p.err);
        rc = -1;
    } else if (v->type != JSON_OBJECT) {
        snprintf(err, sizeof(err), "plan must be an object");
        rc = -1;
    } else {
        const JsonValue *st = json_obj_get(v, "selected_tests");
        const JsonValue *pv = json_obj_get(v, "plan_version");
        const JsonValue *dg = json_obj_get(v, "plan_digest");
        const JsonValue *fs = json_obj_get(v, "full_suite_required");
        if (!st || st->type != JSON_ARRAY) { snprintf(err, sizeof(err), "missing selected_tests array"); rc = -1; }
        else if (!pv || pv->type != JSON_STRING) { snprintf(err, sizeof(err), "missing plan_version"); rc = -1; }
        else if (!dg || dg->type != JSON_STRING || strlen(dg->str) != 64) { snprintf(err, sizeof(err), "missing 64-hex plan_digest"); rc = -1; }
        else if (!fs || fs->type != JSON_BOOL) { snprintf(err, sizeof(err), "missing full_suite_required"); rc = -1; }
        else {
            for (size_t i = 0; i < st->count && rc == 0; i++) {
                const JsonValue *e = st->items[i];
                const JsonValue *id = e ? json_obj_get(e, "id") : NULL;
                const JsonValue *rs = e ? json_obj_get(e, "reason") : NULL;
                if (!e || e->type != JSON_OBJECT || !id || id->type != JSON_STRING ||
                    !rs || rs->type != JSON_ARRAY || rs->count == 0) {
                    snprintf(err, sizeof(err), "selected_tests[%zu] malformed", i);
                    rc = -1;
                }
            }
        }
    }
    if (rc != 0) fprintf(stderr, "validate-plan: INVALID: %s\n", err);
    else printf("{\"valid\":true}\n");
    sb_free(&content);
    arena_free(&a);
    return rc == 0 ? 0 : 4;
}

static int cmd_resource_info(void) {
    ResourceInfo r;
    resource_detect(&r);
    printf("{\"cpu_effective\":%ld,\"mem_total_mb\":%ld,\"mem_limit_mb\":%ld}\n",
           r.cpu_effective, r.mem_total_mb, r.mem_limit_mb);
    return 0;
}

int nse_engine_main(int argc, char **argv) {
    if (argc < 2) {
        usage();
        return 2;
    }
    const char *cmd = argv[1];
    if (strcmp(cmd, "plan") == 0) return cmd_plan(argc - 2, argv + 2);
    if (strcmp(cmd, "changed-files") == 0) return cmd_changed_files(argc - 2, argv + 2);
    if (strcmp(cmd, "classify") == 0) return cmd_classify(argc - 2, argv + 2);
    if (strcmp(cmd, "fingerprint") == 0) return cmd_fingerprint(argc - 2, argv + 2);
    if (strcmp(cmd, "validate-plan") == 0) return cmd_validate_plan(argc - 2, argv + 2);
    if (strcmp(cmd, "resource-info") == 0) return cmd_resource_info();
    if (strcmp(cmd, "version") == 0) {
        printf("%s\n", NSE_ENGINE_VERSION);
        return 0;
    }
    if (strcmp(cmd, "selftest") == 0) return engine_selftest();
    if (strcmp(cmd, "selftest-fuzz") == 0) return engine_selftest_fuzz(argc - 2, argv + 2);
    usage();
    return 2;
}

int main(int argc, char **argv) { return nse_engine_main(argc, argv); }
