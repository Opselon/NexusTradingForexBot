/* nse_ci_engine.h — NSE native CI engine (portable C17).
 *
 * Optimization engine only: Python/pytest remains the test execution
 * framework. This engine is the SOLE test planner (no Python planner,
 * no parity twin); its plans are verified by digest self-checks and
 * the selftest suite.
 *
 * Ownership model: every allocation belongs to an Arena; freeing the arena
 * releases everything. No global mutable state exists anywhere in the engine.
 */
#ifndef NSE_CI_ENGINE_H
#define NSE_CI_ENGINE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define NSE_ENGINE_VERSION "0.1.0"
#define NSE_PLAN_VERSION "1"

/* ------------------------------------------------------------------ */
/* Arena allocator — checked, bounded, no per-object frees.            */
/* ------------------------------------------------------------------ */
typedef struct ArenaBlock {
    struct ArenaBlock *next;
    size_t cap;
    size_t used;
    /* data follows */
} ArenaBlock;

typedef struct Arena {
    ArenaBlock *head;
    size_t block_size;
    size_t total_allocated;
} Arena;

void arena_init(Arena *a, size_t block_size);
void *arena_alloc(Arena *a, size_t n);           /* aborts on OOM */
char *arena_strdup(Arena *a, const char *s);
char *arena_strndup(Arena *a, const char *s, size_t n);
void arena_free(Arena *a);

/* ------------------------------------------------------------------ */
/* Dynamic string buffer (grown with checked reallocation).            */
/* ------------------------------------------------------------------ */
typedef struct StrBuf {
    char *data;
    size_t len;
    size_t cap;
    bool oom; /* sticky: set on allocation failure, all appends become no-op */
} StrBuf;

void sb_init(StrBuf *sb);
void sb_append_n(StrBuf *sb, const char *s, size_t n);
void sb_append(StrBuf *sb, const char *s);
void sb_append_ch(StrBuf *sb, char c);
void sb_append_i64(StrBuf *sb, long long v);
void sb_free(StrBuf *sb);

/* ------------------------------------------------------------------ */
/* SHA-256 (FIPS 180-4). Self-tested against known-answer vectors.     */
/* ------------------------------------------------------------------ */
typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t buf[64];
    size_t buflen;
} Sha256;

void sha256_init(Sha256 *ctx);
void sha256_update(Sha256 *ctx, const uint8_t *data, size_t len);
void sha256_final(Sha256 *ctx, uint8_t out[32]);
void sha256_hex(const uint8_t *data, size_t len, char out[65]);

/* ------------------------------------------------------------------ */
/* Strict JSON parser (recursive descent, depth-bounded, fuzzed).      */
/* Only what the engine needs: objects, arrays, strings, numbers,      */
/* bools, null. Duplicate keys REJECTED. Depth > 64 REJECTED.          */
/* ------------------------------------------------------------------ */
typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_NUMBER,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT
} JsonType;

typedef struct JsonValue JsonValue;
struct JsonValue {
    JsonType type;
    bool b;
    double num;
    char *str;              /* arena-owned, unescaped */
    JsonValue **items;      /* array/object values */
    char **keys;            /* object keys (parallel to items) */
    size_t count;
};

typedef struct JsonParser {
    Arena *arena;
    const char *src;
    size_t pos;
    size_t len;
    int depth;
    char err[128];
    bool failed;
} JsonParser;

JsonValue *json_parse(JsonParser *p, const char *src, size_t len);
const JsonValue *json_obj_get(const JsonValue *obj, const char *key);

/* ------------------------------------------------------------------ */
/* Change classification (mirrors scripts/ci/classify_changes.py).     */
/* ------------------------------------------------------------------ */
typedef enum {
    LANE_PYTHON = 1 << 0,
    LANE_WEB = 1 << 1,
    LANE_JS = 1 << 2,
    LANE_DOCKER = 1 << 3,
    LANE_CI = 1 << 4,
    LANE_DOCS = 1 << 5,
    LANE_DEPS = 1 << 6,
    LANE_SCRIPTS = 1 << 7,
    LANE_RELEASE = 1 << 8
} Lane;

unsigned classify_path(const char *path);

/* ------------------------------------------------------------------ */
/* Dependency graph + test selector (Algorithm 1 + mandatory safety).  */
/* ------------------------------------------------------------------ */
typedef struct Graph {
    Arena *arena;
    /* module table: open addressing, linear probing */
    char **mod_names;      /* dotted module name or tests/<path> identity */
    char **mod_paths;      /* owning repo-relative file path */
    size_t mod_count;
    size_t mod_cap;
    /* edges: module index -> arena list of importer module indices */
    size_t **importers;
    size_t *importer_counts;
    size_t *importer_caps;
    bool *is_test;
    bool *dynamic;
    bool *unparseable;
    bool relative_unresolved; /* any relative import failed to resolve */
    size_t dynamic_files;     /* files containing dynamic-import indicators */
} Graph;

/* ast_metadata.c — build the graph from the Python AST extractor's JSON. */
int graph_build_from_metadata(Graph *g, const char *repo_root,
                              const char *meta_json, char *err, size_t err_len);

void graph_init(Graph *g, Arena *a);
void graph_add_file(Graph *g, const char *rel_path, const char *abs_path);
/* BFS reverse reachability from changed module indices. Also reports
 * (via out_hub_bounded, may be NULL) whether hub-bounding truncated the
 * expansion: a module with more than _HUB_IMPORTER_THRESHOLD importers is
 * a selection boundary (hub-bounded reachability). */
void graph_reachable_tests(const Graph *g, const size_t *changed_idx,
                           size_t changed_len, size_t **out_tests,
                           size_t *out_tests_len, size_t **out_covered,
                           size_t *out_covered_len, bool *out_hub_bounded);
/* Safety-critical source prefixes. */
bool graph_path_is_safety(const char *rel_path);

/* ------------------------------------------------------------------ */
/* Plan building — emits canonical plan JSON (same schema, same canonical */
/* key order, same digest).                                            */
/* ------------------------------------------------------------------ */
typedef struct PlanOptions {
    const char *commit_sha;
    const char *event;         /* "" | pull_request | push | schedule | workflow_dispatch */
    bool full_suite_required;  /* explicit override */
} PlanOptions;

/* changed_files: NUL-separated list of repo-relative paths. */
int engine_build_plan(const char *repo_root, const char *changed_files,
                      const PlanOptions *opt, StrBuf *json_out,
                      char digest_hex[65]);
int engine_build_plan_ex(const char *repo_root, const char *changed_files,
                         const PlanOptions *opt, StrBuf *json_out,
                         char digest_hex[65], const char *metadata_json);

/* ------------------------------------------------------------------ */
/* Resource detection (cgroup v2/v1 + fallback).                       */
/* ------------------------------------------------------------------ */
typedef struct ResourceInfo {
    long cpu_effective;     /* effective CPU threads (cgroup-quota aware) */
    long mem_total_mb;
    long mem_limit_mb;      /* cgroup limit or -1 */
} ResourceInfo;

void resource_detect(ResourceInfo *out);

/* ------------------------------------------------------------------ */
/* Cache metadata record (Layer-B shared-cache provenance).            */
/* ------------------------------------------------------------------ */
int cache_metadata_validate(const char *json_text, char *err, size_t err_len);
void cache_metadata_emit(StrBuf *sb, const char *os, const char *arch,
                         const char *pyver, const char *uvver,
                         const char *lock_hash, const char *schema);

/* ------------------------------------------------------------------ */
/* CLI entry (main.c).                                                 */
/* ------------------------------------------------------------------ */
int nse_engine_main(int argc, char **argv);

/* json_output.c */
void json_write_escaped(StrBuf *sb, const char *s);
void json_write_str_array(StrBuf *sb, char *const *items, size_t count);

/* util.c helpers */
void die(const char *msg);
bool read_file_all(const char *path, StrBuf *out); /* false on any failure */
void normalize_path(char *path); /* backslashes -> /, strip leading ./ */
bool nse_starts_with(const char *p, const char *pre);
bool nse_ends_with(const char *p, const char *suf);
long nse_sysconf_cpus(void);
bool graph_find(const Graph *g, const char *name, size_t *out_idx);
size_t graph_find_or_add(Graph *g, const char *name, const char *path, bool is_test);
void graph_add_edge(Graph *g, size_t from, size_t to);
void graph_scan_repo(Graph *g, const char *repo_root); /* dependency_graph.c */
int engine_selftest(void);                                 /* tests/test_engine.c */
int engine_selftest_fuzz(int argc, char **argv);           /* tests/test_engine.c */
int engine_fingerprint_test(const char *repo_root, const char *test_rel_path,
                            StrBuf *json_out);             /* fingerprint.c */
int change_detector_git_files(const char *base, const char *head, StrBuf *out);

#endif /* NSE_CI_ENGINE_H */
