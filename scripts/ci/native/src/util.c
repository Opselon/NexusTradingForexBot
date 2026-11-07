/* util.c — arena allocator, string buffer, checked IO, SHA-256.
 *
 * Allocation discipline: every allocator aborts on OOM (a CI engine cannot
 * meaningfully continue without memory). Arena frees everything at once.
 */
#include "nse_ci_engine.h"

#if defined(_WIN32)
#include <io.h>
#define access _access
#define F_OK 0
#else
#include <unistd.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

bool nse_starts_with(const char *p, const char *pre) {
    return strncmp(p, pre, strlen(pre)) == 0;
}

bool nse_ends_with(const char *p, const char *suf) {
    size_t lp = strlen(p), ls = strlen(suf);
    return lp >= ls && strcmp(p + lp - ls, suf) == 0;
}

long nse_sysconf_cpus(void) {
#ifdef _SC_NPROCESSORS_ONLN
    return sysconf(_SC_NPROCESSORS_ONLN);
#else
    return 1;
#endif
}

/* ---------------- Arena ---------------- */

void arena_init(Arena *a, size_t block_size) {
    a->head = NULL;
    a->block_size = block_size ? block_size : 1u << 16;
    a->total_allocated = 0;
}

void *arena_alloc(Arena *a, size_t n) {
    if (n == 0) n = 1;
    n = (n + 15u) & ~(size_t)15u; /* 16-byte alignment */
    if (n > a->block_size) {
        /* oversized block gets its own allocation */
        ArenaBlock *b = (ArenaBlock *)malloc(sizeof(ArenaBlock) + n);
        if (!b) die("out of memory (oversized arena block)");
        b->next = a->head;
        b->cap = n;
        b->used = n;
        a->head = b;
        a->total_allocated += n;
        return (char *)(b + 1);
    }
    if (!a->head || a->head->used + n > a->head->cap) {
        ArenaBlock *b = (ArenaBlock *)malloc(sizeof(ArenaBlock) + a->block_size);
        if (!b) die("out of memory (arena block)");
        b->next = a->head;
        b->cap = a->block_size;
        b->used = 0;
        a->head = b;
    }
    void *p = (char *)(a->head + 1) + a->head->used;
    a->head->used += n;
    a->total_allocated += n;
    return p;
}

char *arena_strndup(Arena *a, const char *s, size_t n) {
    char *p = (char *)arena_alloc(a, n + 1);
    memcpy(p, s, n);
    p[n] = '\0';
    return p;
}

char *arena_strdup(Arena *a, const char *s) {
    return arena_strndup(a, s, strlen(s));
}

void arena_free(Arena *a) {
    ArenaBlock *b = a->head;
    while (b) {
        ArenaBlock *next = b->next;
        free(b);
        b = next;
    }
    a->head = NULL;
    a->total_allocated = 0;
}

/* ---------------- StrBuf ---------------- */

void sb_init(StrBuf *sb) {
    sb->data = NULL;
    sb->len = 0;
    sb->cap = 0;
    sb->oom = false;
}

static void sb_grow(StrBuf *sb, size_t need) {
    if (sb->len + need + 1 <= sb->cap) return;
    size_t ncap = sb->cap ? sb->cap : 256;
    while (ncap < sb->len + need + 1) {
        if (ncap > (SIZE_MAX / 2)) { sb->oom = true; return; }
        ncap *= 2;
    }
    char *nd = (char *)realloc(sb->data, ncap);
    if (!nd) { sb->oom = true; return; }
    sb->data = nd;
    sb->cap = ncap;
}

void sb_append_n(StrBuf *sb, const char *s, size_t n) {
    if (sb->oom) return;
    sb_grow(sb, n);
    if (sb->oom) return;
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

void sb_append(StrBuf *sb, const char *s) { sb_append_n(sb, s, strlen(s)); }

void sb_append_ch(StrBuf *sb, char c) { sb_append_n(sb, &c, 1); }

void sb_append_i64(StrBuf *sb, long long v) {
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%lld", v);
    sb_append(sb, tmp);
}

void sb_free(StrBuf *sb) {
    free(sb->data);
    sb->data = NULL;
    sb->len = sb->cap = 0;
}

/* ---------------- checked IO ---------------- */

void die(const char *msg) {
    fprintf(stderr, "nse-ci-engine: fatal: %s\n", msg);
    exit(2);
}

bool read_file_all(const char *path, StrBuf *out) {
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    char chunk[65536];
    size_t n;
    while ((n = fread(chunk, 1, sizeof(chunk), f)) > 0) {
        sb_append_n(out, chunk, n);
    }
    bool ok = ferror(f) == 0;
    fclose(f);
    return ok;
}

void normalize_path(char *path) {
    size_t n = strlen(path);
    for (size_t i = 0; i < n; i++) {
        if (path[i] == '\\') path[i] = '/';
    }
    if (n >= 2 && path[0] == '.' && path[1] == '/') {
        memmove(path, path + 2, n - 1);
    }
}

/* ---------------- SHA-256 (FIPS 180-4) ---------------- */

static const uint32_t K[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu,
    0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u,
    0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u,
    0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u,
    0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
    0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
    0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u,
    0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u, 0x1e376c08u,
    0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu,
    0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

static uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

void sha256_init(Sha256 *ctx) {
    ctx->state[0] = 0x6a09e667u; ctx->state[1] = 0xbb67ae85u;
    ctx->state[2] = 0x3c6ef372u; ctx->state[3] = 0xa54ff53au;
    ctx->state[4] = 0x510e527fu; ctx->state[5] = 0x9b05688cu;
    ctx->state[6] = 0x1f83d9abu; ctx->state[7] = 0x5be0cd19u;
    ctx->bitlen = 0;
    ctx->buflen = 0;
}

static void sha256_block(Sha256 *ctx, const uint8_t *p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)p[i * 4] << 24) | ((uint32_t)p[i * 4 + 1] << 16) |
               ((uint32_t)p[i * 4 + 2] << 8) | (uint32_t)p[i * 4 + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t t1 = h + S1 + ch + K[i] + w[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

void sha256_update(Sha256 *ctx, const uint8_t *data, size_t len) {
    ctx->bitlen += (uint64_t)len * 8u;
    while (len > 0) {
        size_t take = 64 - ctx->buflen;
        if (take > len) take = len;
        memcpy(ctx->buf + ctx->buflen, data, take);
        ctx->buflen += take;
        data += take;
        len -= take;
        if (ctx->buflen == 64) {
            sha256_block(ctx, ctx->buf);
            ctx->buflen = 0;
        }
    }
}

void sha256_final(Sha256 *ctx, uint8_t out[32]) {
    uint64_t bitlen = ctx->bitlen;
    uint8_t pad = 0x80;
    sha256_update(ctx, &pad, 1);
    uint8_t z = 0;
    while (ctx->buflen != 56) sha256_update(ctx, &z, 1);
    uint8_t lenb[8];
    for (int i = 0; i < 8; i++) lenb[i] = (uint8_t)(bitlen >> (56 - i * 8));
    /* append length WITHOUT counting the padding just fed */
    memcpy(ctx->buf + ctx->buflen, lenb, 8);
    ctx->buflen += 8;
    if (ctx->buflen == 64) { sha256_block(ctx, ctx->buf); ctx->buflen = 0; }
    for (int i = 0; i < 8; i++) {
        out[i * 4] = (uint8_t)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (uint8_t)ctx->state[i];
    }
    (void)bitlen;
}

void sha256_hex(const uint8_t *data, size_t len, char out[65]) {
    Sha256 c;
    sha256_init(&c);
    sha256_update(&c, data, len);
    uint8_t d[32];
    sha256_final(&c, d);
    static const char *hex = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = hex[d[i] >> 4];
        out[i * 2 + 1] = hex[d[i] & 0xf];
    }
    out[64] = '\0';
}
