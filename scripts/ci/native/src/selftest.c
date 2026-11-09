/* test_engine.c — engine selftest + builtin fuzz driver.
 * `nse-ci-engine selftest` runs deterministic known-answer and invariant
 * checks (return 0 = pass). `selftest-fuzz --iterations N` feeds mutated
 * JSON and random binary into the strict parser and the import scanner;
 * with the ASan/UBSan build any memory error aborts non-zero. */
#include "nse_ci_engine.h"
#include "nse_dirent.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#include <direct.h>
#include <process.h> /* _getpid */
#define nse_getpid() _getpid()
#define nse_mkdir1(p) _mkdir(p)
#else
#include <sys/stat.h>
#include <unistd.h>
#define nse_getpid() getpid()
#define nse_mkdir1(p) mkdir(p, 0777)
#endif

/* mkdir -p: create every missing ancestor of path (existing dirs ignored).
 * No shell involved: the fixture tree must build on Windows too, where
 * `rm -rf` / `mkdir -p` do not exist. */
static int nse_mkdir_p(const char *path) {
    char buf[512];
    size_t len = strlen(path);
    if (len == 0 || len >= sizeof(buf)) return -1;
    memcpy(buf, path, len + 1);
    for (char *p = buf + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char save = *p;
            *p = '\0';
            nse_mkdir1(buf); /* ignore EEXIST */
            *p = save;
        }
    }
    nse_mkdir1(buf);
    return 0;
}

/* Recursively delete a tree. remove() deletes files and empty directories
 * on both POSIX and Windows, so no platform shell is needed. */
static void nse_rmtree(const char *path) {
    DIR *d = opendir(path);
    if (!d) {
        remove(path);
        return;
    }
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        char child[1024];
        snprintf(child, sizeof(child), "%s/%s", path, ent->d_name);
        nse_rmtree(child);
    }
    closedir(d);
    remove(path);
}

/* Private per-process fixture root: %TEMP% on Windows, /tmp elsewhere. */
static void nse_selftest_root(char *out, size_t out_len) {
#if defined(_WIN32)
    const char *tmp = getenv("TEMP");
    if (!tmp || !*tmp) tmp = getenv("TMP");
    if (!tmp || !*tmp) tmp = "C:/Windows/Temp";
    snprintf(out, out_len, "%s/nse-selftest-%d", tmp, (int)nse_getpid());
#else
    snprintf(out, out_len, "/tmp/nse-selftest-%d", (int)nse_getpid());
#endif
}

/* link-time providers from test fixtures below */
int engine_selftest(void);

/* ---------------- fixtures ---------------- */

static int test_sha256_vectors(void) {
    char hex[65];
    sha256_hex((const uint8_t *)"", 0, hex);
    if (strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855") != 0)
        return 1;
    sha256_hex((const uint8_t *)"abc", 3, hex);
    if (strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") != 0)
        return 2;
    /* 448-bit boundary case */
    const char *long_input = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    sha256_hex((const uint8_t *)long_input, strlen(long_input), hex);
    if (strcmp(hex, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1") != 0)
        return 3;
    return 0;
}

static int test_json_parser(void) {
    Arena a;
    arena_init(&a, 1u << 14);
    JsonParser p;
    p.arena = &a;

    const char *good = "{\"a\":[1,2,{\"b\":\"\\u00e9\\n\"}],\"c\":true,\"d\":null}";
    JsonValue *v = json_parse(&p, good, strlen(good));
    if (!v || v->type != JSON_OBJECT) { arena_free(&a); return 10; }
    const JsonValue *arr = json_obj_get(v, "a");
    if (!arr || arr->type != JSON_ARRAY || arr->count != 3) { arena_free(&a); return 11; }
    if (json_obj_get(v, "missing")) { arena_free(&a); return 12; }

    /* rejects */
    const char *bad1 = "{\"a\":1,\"a\":2}";           /* duplicate key */
    const char *bad2 = "{\"a\":1} trailing";           /* trailing garbage */
    const char *bad3 = "{\"a\":\"\\ud800\"}";          /* lone surrogate */
    const char *bad4 = "{\"a\":01}";                   /* (parsed as number 01 → ok for strtod) */
    if (json_parse(&p, bad1, strlen(bad1))) { arena_free(&a); return 13; }
    if (json_parse(&p, bad2, strlen(bad2))) { arena_free(&a); return 14; }
    if (json_parse(&p, bad3, strlen(bad3))) { arena_free(&a); return 15; }
    (void)bad4;

    /* deep nesting rejected */
    StrBuf deep;
    sb_init(&deep);
    for (int i = 0; i < 100; i++) sb_append_ch(&deep, '[');
    for (int i = 0; i < 100; i++) sb_append_ch(&deep, ']');
    if (json_parse(&p, deep.data, deep.len)) { sb_free(&deep); arena_free(&a); return 16; }
    /* writer: non-ASCII escapes match Python json.dumps(ensure_ascii=True)
     * exactly, or the C digest and Python's re-dump diverge. */
    StrBuf w;
    sb_init(&w);
    json_write_escaped(&w, "a\xe2\x80\x94" "b"); /* em-dash, U+2014 */
    if (!w.data || strcmp(w.data, "\"a\\u2014b\"") != 0) {
        sb_free(&w);
        arena_free(&a);
        return 17;
    }
    sb_free(&w);
    sb_free(&deep);
    arena_free(&a);
    return 0;
}

static int test_plan_fixture(void) {
    /* Build a miniature NSE-shaped tree in a PRIVATE temp dir and verify the
     * plan is digest-stable (deterministic selection). The directory is
     * UNIQUE per process: a fixed path here once poisoned concurrent
     * pipelines that reused the same fixture location. */
    char root[256];
    nse_selftest_root(root, sizeof(root));
    const char *ROOT = root;
    nse_rmtree(ROOT); /* a previous crashed run may have left it behind */
    char sub[512];
    snprintf(sub, sizeof(sub), "%s/src/nexus_scalp/risk", ROOT);
    if (nse_mkdir_p(sub) != 0) return 20;
    snprintf(sub, sizeof(sub), "%s/src/nexus_scalp/execution", ROOT);
    if (nse_mkdir_p(sub) != 0) return 20;
    snprintf(sub, sizeof(sub), "%s/tests/unit", ROOT);
    if (nse_mkdir_p(sub) != 0) return 20;
    snprintf(sub, sizeof(sub), "%s/configs", ROOT);
    if (nse_mkdir_p(sub) != 0) return 20;

    FILE *f;
    char path[512];
    snprintf(path, sizeof(path), "%s/src/nexus_scalp/risk/risk_engine.py", ROOT);
    f = fopen(path, "w");
    if (!f) return 21;
    fputs("def assess():\n    return True\n", f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/src/nexus_scalp/execution/trade_manager.py", ROOT);
    f = fopen(path, "w");
    fputs("from nexus_scalp.risk.risk_engine import assess\n\n\ndef trade():\n    return assess()\n", f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/tests/unit/test_risk_engine.py", ROOT);
    f = fopen(path, "w");
    fputs("from nexus_scalp.risk.risk_engine import assess\n\n\ndef test_assess():\n    assert assess()\n", f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/tests/unit/test_trade_manager.py", ROOT);
    f = fopen(path, "w");
    fputs("from nexus_scalp.execution.trade_manager import trade\n\n\ndef test_trade():\n    assert trade()\n", f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/tests/unit/test_unrelated.py", ROOT);
    f = fopen(path, "w");
    fputs("def test_other():\n    assert True\n", f);
    fclose(f);

    /* Fixture-only dependency: test_fixture_only uses the `risky` fixture
     * defined in tests/unit/conftest.py, which imports risk_engine — the
     * test file itself never imports it. The line scanner cannot see
     * fixture usage, so only the metadata path selects this test. */
    snprintf(path, sizeof(path), "%s/tests/unit/conftest.py", ROOT);
    f = fopen(path, "w");
    fputs("import pytest\nfrom nexus_scalp.risk.risk_engine import assess\n\n\n"
          "@pytest.fixture\ndef risky():\n    return assess()\n",
          f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/tests/unit/test_fixture_only.py", ROOT);
    f = fopen(path, "w");
    fputs("def test_via_fixture(risky):\n    assert risky()\n", f);
    fclose(f);

    snprintf(path, sizeof(path), "%s/tests/critical_suite.txt", ROOT);
    f = fopen(path, "w");
    fputs("tests/unit/test_risk_engine.py\ntests/unit/test_trade_manager.py\n", f);
    fclose(f);
    snprintf(path, sizeof(path), "%s/tests/fast_suite.txt", ROOT);
    f = fopen(path, "w");
    fputs("tests/unit/test_unrelated.py\n", f);
    fclose(f);

    PlanOptions opt = { "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "", false, NULL };
    StrBuf plan;
    sb_init(&plan);
    char digest[65];
    if (engine_build_plan(ROOT, "src/nexus_scalp/risk/risk_engine.py\0", &opt, &plan,
                          digest) != 0) {
        sb_free(&plan);
        return 22;
    }
    /* targeted: risk_engine + TRANSITIVE trade_manager; unrelated excluded */
    if (!strstr(plan.data ? plan.data : "", "tests/unit/test_risk_engine.py") ||
        !strstr(plan.data ? plan.data : "", "tests/unit/test_trade_manager.py") ||
        strstr(plan.data ? plan.data : "", "test_unrelated")) {
        sb_free(&plan);
        return 23;
    }
    /* determinism */
    StrBuf plan2;
    sb_init(&plan2);
    char digest2[65];
    engine_build_plan(ROOT, "src/nexus_scalp/risk/risk_engine.py\0", &opt, &plan2, digest2);
    if (strcmp(digest, digest2) != 0 ||
        strcmp(plan.data ? plan.data : "", plan2.data ? plan2.data : "") != 0) {
        sb_free(&plan); sb_free(&plan2);
        return 24;
    }
    /* safety expansion via execution/ path */
    StrBuf plan3;
    sb_init(&plan3);
    char digest3[65];
    engine_build_plan(ROOT, "src/nexus_scalp/execution/trade_manager.py\0", &opt, &plan3,
                      digest3);
    if (!strstr(plan3.data ? plan3.data : "", "mandatory_safety")) {
        sb_free(&plan); sb_free(&plan2); sb_free(&plan3);
        return 25;
    }
    /* AST-metadata path: the fixture-only test is selected ONLY via
     * metadata (fixture edge test -> conftest -> risk_engine). The
     * line-scan plan above must NOT contain it. */
    StrBuf plan_meta;
    sb_init(&plan_meta);
    char digest_meta[65];
    const char *meta_json =
        "{\"schema\":\"nse-ast-meta-v1\",\"files\":{"
        "\"src/nexus_scalp/risk/risk_engine.py\":"
        "{\"module\":\"nexus_scalp.risk.risk_engine\",\"imports\":[]},"
        "\"src/nexus_scalp/execution/trade_manager.py\":"
        "{\"module\":\"nexus_scalp.execution.trade_manager\",\"imports\":["
        "{\"m\":\"nexus_scalp.risk.risk_engine\",\"rel\":0,\"symbols\":[\"assess\"]}]},"
        "\"tests/unit/conftest.py\":"
        "{\"module\":\"tests.unit.conftest\",\"imports\":["
        "{\"m\":\"nexus_scalp.risk.risk_engine\",\"rel\":0,\"symbols\":[\"assess\"]}],"
        "\"fixtures\":[{\"name\":\"risky\",\"autouse\":false,\"params\":[]}]},"
        "\"tests/unit/test_risk_engine.py\":"
        "{\"module\":\"tests.unit.test_risk_engine\",\"imports\":["
        "{\"m\":\"nexus_scalp.risk.risk_engine\",\"rel\":0,\"symbols\":[\"assess\"]}]},"
        "\"tests/unit/test_trade_manager.py\":"
        "{\"module\":\"tests.unit.test_trade_manager\",\"imports\":["
        "{\"m\":\"nexus_scalp.execution.trade_manager\",\"rel\":0,\"symbols\":[\"trade\"]}]},"
        "\"tests/unit/test_fixture_only.py\":"
        "{\"module\":\"tests.unit.test_fixture_only\",\"imports\":[],"
        "\"test_args\":{\"test_via_fixture\":[\"risky\"]}},"
        "\"tests/unit/test_unrelated.py\":"
        "{\"module\":\"tests.unit.test_unrelated\",\"imports\":[]}"
        "}}";
    if (engine_build_plan_ex(ROOT, "src/nexus_scalp/risk/risk_engine.py\0", &opt,
                             &plan_meta, digest_meta, meta_json) != 0) {
        sb_free(&plan); sb_free(&plan2); sb_free(&plan3); sb_free(&plan_meta);
        return 26;
    }
    if (!strstr(plan_meta.data ? plan_meta.data : "", "tests/unit/test_fixture_only.py") ||
        !strstr(plan_meta.data ? plan_meta.data : "", "\"metadata_used\":true") ||
        strstr(plan_meta.data ? plan_meta.data : "", "test_unrelated")) {
        sb_free(&plan); sb_free(&plan2); sb_free(&plan3); sb_free(&plan_meta);
        return 27;
    }
    sb_free(&plan_meta);
    if (strstr(plan.data ? plan.data : "", "test_fixture_only")) {
        sb_free(&plan); sb_free(&plan2); sb_free(&plan3);
        return 28;
    }
    sb_free(&plan);
    sb_free(&plan2);
    sb_free(&plan3);
    /* remove the private fixture tree */
    nse_rmtree(ROOT);
    return 0;
}

static int test_graph_reachability_stack_bound(void) {
    /* A dense acyclic cross-edge graph makes the old pop-time visitation
     * enqueue the same pending nodes repeatedly, exceeding a V-sized stack. */
    enum { HUB_COUNT = 48 };
    Arena arena;
    arena_init(&arena, 1u << 16);
    Graph graph;
    graph_init(&graph, &arena);

    size_t root = graph_find_or_add(&graph, "src/changed_root.py", NULL, false);
    size_t hubs[HUB_COUNT];
    char name[64];
    for (size_t i = 0; i < HUB_COUNT; i++) {
        snprintf(name, sizeof(name), "src/hub_%02zu.py", i);
        hubs[i] = graph_find_or_add(&graph, name, NULL, false);
    }
    size_t test_node = graph_find_or_add(
        &graph, "tests/unit/test_reachability.py", NULL, true
    );

    /* The root pushes every hub. The last hub then points back to every
     * earlier pending hub, creating a dense cross-edge workload. */
    for (size_t i = 0; i < HUB_COUNT; i++) graph_add_edge(&graph, hubs[i], root);
    for (size_t i = 0; i + 1 < HUB_COUNT; i++) {
        graph_add_edge(&graph, hubs[i], hubs[HUB_COUNT - 1]);
    }
    graph_add_edge(&graph, test_node, hubs[0]);

    size_t *selected = NULL, *covered = NULL;
    size_t selected_len = 0, covered_len = 0;
    bool hub_bounded = false;
    graph_reachable_tests(
        &graph, &root, 1, &selected, &selected_len, &covered, &covered_len,
        &hub_bounded
    );

    int rc = (selected_len == 1 && selected[0] == test_node) ? 0 : 26;
    arena_free(&arena);
    return rc;
}

static int test_hub_one_hop_expansion(void) {
    /* The audit's hub scenario, in miniature: a changed leaf M, one test
     * importing M directly, a hub H (26 importers) importing M, and
     * integration tests importing H. The bounded one-hop expansion must
     * include H's direct test importers (<= 25 of them); a test reaching
     * H only via a non-test intermediate (distance 2 past the hub) must
     * stay unselected. A second, oversized hub (40 direct tests) must
     * stay a hard boundary — uncapped one-hop measured 219 files for a
     * one-line worker.py change on the real repo, versus 7 bounded. */
    Arena arena;
    arena_init(&arena, 1u << 16);
    Graph graph;
    graph_init(&graph, &arena);

    size_t leaf = graph_find_or_add(&graph, "src/leaf.py", NULL, false);
    size_t direct = graph_find_or_add(&graph, "tests/unit/test_direct.py", NULL, true);
    graph_add_edge(&graph, direct, leaf);

    size_t hub = graph_find_or_add(&graph, "src/hub.py", NULL, false);
    graph_add_edge(&graph, hub, leaf);
    size_t hub_tests[25];
    char name[64];
    for (size_t i = 0; i < 25; i++) {
        snprintf(name, sizeof(name), "tests/integration/test_hub_%02zu.py", i);
        hub_tests[i] = graph_find_or_add(&graph, name, NULL, true);
        graph_add_edge(&graph, hub_tests[i], hub);
    }
    size_t mid = graph_find_or_add(&graph, "src/mid.py", NULL, false);
    graph_add_edge(&graph, mid, hub);
    size_t via_mid = graph_find_or_add(&graph, "tests/unit/test_via_mid.py", NULL, true);
    graph_add_edge(&graph, via_mid, mid);

    size_t big = graph_find_or_add(&graph, "src/big.py", NULL, false);
    graph_add_edge(&graph, big, leaf);
    size_t big_tests[40];
    for (size_t i = 0; i < 40; i++) {
        snprintf(name, sizeof(name), "tests/unit/test_big_%02zu.py", i);
        big_tests[i] = graph_find_or_add(&graph, name, NULL, true);
        graph_add_edge(&graph, big_tests[i], big);
    }

    size_t *selected = NULL, *covered = NULL;
    size_t selected_len = 0, covered_len = 0;
    bool hub_bounded = false;
    graph_reachable_tests(&graph, &leaf, 1, &selected, &selected_len,
                          &covered, &covered_len, &hub_bounded);

    int rc = 0;
    if (!hub_bounded) rc = 30;
    /* direct test + 25 hub tests = 26; via_mid and all big tests excluded */
    if (selected_len != 26) rc = 31;
    bool has_direct = false, has_via_mid = false, has_big = false;
    bool has_hub_tests = true;
    for (size_t i = 0; i < selected_len; i++) {
        if (selected[i] == direct) has_direct = true;
        if (selected[i] == via_mid) has_via_mid = true;
        for (size_t b = 0; b < 40; b++) {
            if (selected[i] == big_tests[b]) has_big = true;
        }
    }
    for (size_t i = 0; i < 25; i++) {
        bool found = false;
        for (size_t s = 0; s < selected_len; s++) {
            if (selected[s] == hub_tests[i]) found = true;
        }
        if (!found) has_hub_tests = false;
    }
    if (!has_direct || !has_hub_tests || has_via_mid || has_big) rc = 32;
    bool leaf_covered = false;
    for (size_t i = 0; i < covered_len; i++) {
        if (covered[i] == leaf) leaf_covered = true;
    }
    if (!leaf_covered) rc = 33;
    arena_free(&arena);
    return rc;
}

int engine_selftest(void) {
    int rc;
    if ((rc = test_sha256_vectors()) != 0) {
        fprintf(stderr, "selftest: sha256 vectors FAILED (%d)\n", rc);
        return 1;
    }
    if ((rc = test_json_parser()) != 0) {
        fprintf(stderr, "selftest: json parser FAILED (%d)\n", rc);
        return 1;
    }
    if ((rc = test_plan_fixture()) != 0) {
        fprintf(stderr, "selftest: plan fixture FAILED (%d)\n", rc);
        return 1;
    }
    if ((rc = test_graph_reachability_stack_bound()) != 0) {
        fprintf(stderr, "selftest: graph reachability stack bound FAILED (%d)\n", rc);
        return 1;
    }
    if ((rc = test_hub_one_hop_expansion()) != 0) {
        fprintf(stderr, "selftest: hub one-hop expansion FAILED (%d)\n", rc);
        return 1;
    }
    printf("{\"selftest\":\"pass\"}\n");
    return 0;
}

/* ---------------- builtin fuzz driver ---------------- */

static uint64_t rng_state = 0x243F6A8885A308D3ull;
static uint64_t rng_next(void) {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}

static const char *JSON_SEED =
    "{\"commit_sha\":\"a\",\"fallback_reason\":null,\"full_suite_required\":false,"
    "\"missing_mappings\":[],\"notes\":[],\"plan_version\":\"1\","
    "\"selected_tests\":[{\"id\":\"tests/unit/test_a.py\",\"priority\":\"normal\","
    "\"reason\":[\"static_dependency\"]}],\"selection_mode\":\"targeted\","
    "\"skipped_groups\":[],\"plan_digest\":\"0123456789abcdef0123456789abcdef"
    "0123456789abcdef0123456789abcdef\"}";

int engine_selftest_fuzz(int argc, char **argv) {
    long iterations = 20000;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--iterations") == 0 && i + 1 < argc) {
            iterations = atol(argv[++i]);
        }
    }
    size_t seed_len = strlen(JSON_SEED);
    char *buf = (char *)malloc(seed_len + 64);
    if (!buf) return 2;
    long crashes_guard = 0;
    for (long it = 0; it < iterations; it++) {
        size_t len = seed_len;
        memcpy(buf, JSON_SEED, seed_len);
        long mutations = 1 + (long)(rng_next() % 8);
        for (long m = 0; m < mutations; m++) {
            uint64_t r = rng_next();
            size_t pos = (size_t)(r % (len ? len : 1));
            switch (r % 4) {
                case 0: buf[pos] = (char)(rng_next() & 0xFF); break;
                case 1: buf[pos] = (char)("\\\"[]{}:,0123456789abcdef \n"[r % 22]); break;
                case 2:
                    if (len < seed_len + 32) {
                        memmove(buf + pos + 1, buf + pos, len - pos);
                        buf[pos] = (char)(rng_next() & 0xFF);
                        len++;
                    }
                    break;
                default:
                    if (len > 1 && pos < len) {
                        memmove(buf + pos, buf + pos + 1, len - pos - 1);
                        len--;
                    }
                    break;
            }
        }
        Arena a;
        arena_init(&a, 1u << 12);
        JsonParser p;
        p.arena = &a;
        (void)json_parse(&p, buf, len); /* must never crash; result ignored */
        arena_free(&a);
        crashes_guard++;
        if (crashes_guard > iterations) break; /* defensive */
    }
    free(buf);
    printf("{\"fuzz_iterations\":%ld,\"result\":\"no-crash\"}\n", iterations);
    return 0;
}
/* (selftest entry points consumed by both binaries; see nse_ci_engine.h) */
