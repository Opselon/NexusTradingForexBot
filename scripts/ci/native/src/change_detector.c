/* change_detector.c — lane classification mirroring classify_changes.py,
 * plus `git diff --name-only base...head` acquisition via a read-only popen.
 * The git invocation is bounded: no user input reaches the shell (base/head
 * are validated SHA-like tokens before use).
 */
#include "nse_ci_engine.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
/* MSVC spells the pipe open/close _popen/_pclose. */
#define popen _popen
#define pclose _pclose
#endif

static bool is_doc_file(const char *p) {
    if (nse_starts_with(p, "docs/") || nse_starts_with(p, "agents/")) {
        return nse_ends_with(p, ".md") || nse_ends_with(p, ".markdown") ||
               nse_ends_with(p, ".rst") || nse_ends_with(p, ".txt");
    }
    return strcmp(p, "README.md") == 0 || strcmp(p, "PROJECT_GRAPH.md") == 0 ||
           strcmp(p, "AGENTS.md") == 0;
}

unsigned classify_path(const char *raw) {
    char path[1024];
    snprintf(path, sizeof(path), "%s", raw);
    normalize_path(path);
    unsigned lanes = 0;

    if (nse_starts_with(path, ".github/") || nse_starts_with(path, "scripts/ci/") ||
        strcmp(path, "beforePush.ps1") == 0 || strcmp(path, "beforePush.sh") == 0) {
        lanes |= LANE_CI;
    }
    if (strcmp(path, "Dockerfile") == 0 || strcmp(path, "docker-compose.yml") == 0 ||
        nse_starts_with(path, "docker/") || nse_starts_with(path, ".dockerignore") ||
        nse_starts_with(path, ".devcontainer/")) {
        lanes |= LANE_DOCKER;
    }
    if (strcmp(path, "pyproject.toml") == 0 || strcmp(path, "requirements.txt") == 0 ||
        strcmp(path, "uv.lock") == 0 || strcmp(path, "package.json") == 0 ||
        strcmp(path, "package-lock.json") == 0 ||
        strcmp(path, "Directory.Packages.props") == 0) {
        lanes |= LANE_DEPS;
        lanes |= LANE_PYTHON;
    }
    if (nse_starts_with(path, "Web/") || nse_starts_with(path, "tests/js/")) {
        lanes |= LANE_WEB;
        lanes |= LANE_JS;
    }
    if (nse_starts_with(path, "installer/") || nse_starts_with(path, "release/") ||
        nse_starts_with(path, "src/nexus_scalp/release/") ||
        strcmp(path, "NexusTradingForexBot.iss") == 0 ||
        nse_starts_with(path, "scripts/build/")) {
        lanes |= LANE_RELEASE;
        if (nse_ends_with(path, ".ps1") || nse_ends_with(path, ".sh")) lanes |= LANE_SCRIPTS;
    }
    if (nse_starts_with(path, "scripts/") &&
        (nse_ends_with(path, ".ps1") || nse_ends_with(path, ".sh") || nse_ends_with(path, ".py")) &&
        !nse_starts_with(path, "scripts/ci/") && !nse_starts_with(path, "scripts/build/")) {
        lanes |= LANE_SCRIPTS;
    }
    if (nse_starts_with(path, "src/") ||
        (nse_starts_with(path, "tests/") && !nse_starts_with(path, "tests/js/")) ||
        nse_starts_with(path, "configs/") || strcmp(path, "pyproject.toml") == 0 ||
        strcmp(path, "requirements.txt") == 0 || nse_starts_with(path, "data/") ||
        strcmp(path, "main.py") == 0 ||
        (nse_ends_with(path, ".py") && !nse_starts_with(path, "scripts/ci/") &&
         !nse_starts_with(path, "scripts/build/"))) {
        lanes |= LANE_PYTHON;
    }
    if (is_doc_file(path)) lanes |= LANE_DOCS;
    return lanes;
}

static bool sha_like(const char *s) {
    if (!s || !*s) return false;
    for (const char *p = s; *p; p++) {
        if (!isxdigit((unsigned char)*p)) return false;
    }
    return strlen(s) <= 64;
}

int change_detector_git_files(const char *base, const char *head, StrBuf *out) {
    if (!sha_like(base) || !sha_like(head)) return -1;
    char cmd[512];
#if defined(_WIN32)
    /* cmd.exe has no /dev/null; NUL is the null device. */
    snprintf(cmd, sizeof(cmd), "git diff --name-only -z %s...%s 2>NUL", base, head);
#else
    /* No 2>/dev/null: git's stderr must reach the caller. The rc=3
       failure on 2026-11-09 was invisible for exactly this reason — the
       engine swallowed git's "fatal: no merge base found" (the head was
       the shallow-grafted merge commit) and all CI saw was "git diff
       failed". stderr never pollutes stdout, which is the -z file list. */
    snprintf(cmd, sizeof(cmd), "git diff --name-only -z %s...%s", base, head);
#endif
    FILE *f = popen(cmd, "r");
    if (!f) return -1;
    char chunk[4096];
    size_t n;
    while ((n = fread(chunk, 1, sizeof(chunk), f)) > 0) {
        sb_append_n(out, chunk, n);
    }
    int rc = pclose(f);
    return rc == 0 ? 0 : -1;
}
