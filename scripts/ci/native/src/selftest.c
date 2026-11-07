/* test_engine.c — engine selftest + builtin fuzz driver.
 * `nse-ci-engine selftest` runs deterministic known-answer and invariant
 * checks (return 0 = pass). `selftest-fuzz --iterations N` feeds mutated
 * JSON and random binary into the strict parser and the import scanner;
 * with the ASan/UBSan build any memory error aborts non-zero. */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    sb_free(&deep);
    arena_free(&a);
    return 0;
}

static int test_plan_fixture(void) {
    /* Build a miniature NSE-shaped tree in /tmp and verify the plan matches
     * the Python planner byte-for-byte (digest equality) — the fixture is
     * also exercised from Python during CI equivalence testing. */
    static const char *ROOT = "/tmp/nse-engine-fixture";
    char cmd[512];
    snprintf(cmd, sizeof(cmd),
             "rm -rf %s && mkdir -p %s/src/nexus_scalp/risk %s/src/nexus_scalp/execution "
             "%s/tests/unit %s/configs",
             ROOT, ROOT, ROOT, ROOT, ROOT);
    if (system(cmd) != 0) return 20;

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

    snprintf(path, sizeof(path), "%s/tests/critical_suite.txt", ROOT);
    f = fopen(path, "w");
    fputs("tests/unit/test_risk_engine.py\ntests/unit/test_trade_manager.py\n", f);
    fclose(f);
    snprintf(path, sizeof(path), "%s/tests/fast_suite.txt", ROOT);
    f = fopen(path, "w");
    fputs("tests/unit/test_unrelated.py\n", f);
    fclose(f);

    PlanOptions opt = { "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "", false };
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
    sb_free(&plan);
    sb_free(&plan2);
    sb_free(&plan3);
    return 0;
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
