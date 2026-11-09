/* json_output.c — canonical JSON writer + strict depth-bounded JSON parser.
 *
 * Writer: emits the plan with EXACTLY the same canonical form as
 * `json.dumps(obj, sort_keys=True, separators=(",", ":"))` in the Python
 * planner, so digests are directly comparable (equivalence evidence).
 * The caller is responsible for emitting keys in sorted order.
 *
 * Parser: strict recursive descent — duplicate keys, trailing garbage,
 * depth > 64, and raw control characters are all REJECTED. Fuzzed in
 * selftest-fuzz. Arena-owned values; no escaping of ownership rules.
 */
#include "nse_ci_engine.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------- writer helpers ---------------- */

void json_write_escaped(StrBuf *sb, const char *s) {
    sb_append_ch(sb, '"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"': sb_append(sb, "\\\""); break;
            case '\\': sb_append(sb, "\\\\"); break;
            case '\b': sb_append(sb, "\\b"); break;
            case '\f': sb_append(sb, "\\f"); break;
            case '\n': sb_append(sb, "\\n"); break;
            case '\r': sb_append(sb, "\\r"); break;
            case '\t': sb_append(sb, "\\t"); break;
            default:
                if (*p < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", *p);
                    sb_append(sb, buf);
                } else if (*p < 0x80) {
                    sb_append_ch(sb, (char)*p);
                } else {
                    /* Non-ASCII: match Python json.dumps(ensure_ascii=True)
                     * exactly — emit \uXXXX (surrogate pair above the BMP).
                     * Raw UTF-8 here made the C digest differ from
                     * Python's re-dump, so any plan with a non-ASCII
                     * string (an em-dash in a fallback reason or note)
                     * failed validate_plan with "plan digest mismatch". */
                    uint32_t cp = 0;
                    size_t adv = 1;
                    if ((*p & 0xE0) == 0xC0 && (p[1] & 0xC0) == 0x80) {
                        cp = ((uint32_t)(*p & 0x1F) << 6) | (uint32_t)(p[1] & 0x3F);
                        adv = 2;
                    } else if ((*p & 0xF0) == 0xE0 && (p[1] & 0xC0) == 0x80 &&
                               (p[2] & 0xC0) == 0x80) {
                        cp = ((uint32_t)(*p & 0x0F) << 12) |
                             ((uint32_t)(p[1] & 0x3F) << 6) | (uint32_t)(p[2] & 0x3F);
                        adv = 3;
                    } else if ((*p & 0xF8) == 0xF0 && (p[1] & 0xC0) == 0x80 &&
                               (p[2] & 0xC0) == 0x80 && (p[3] & 0xC0) == 0x80) {
                        cp = ((uint32_t)(*p & 0x07) << 18) |
                             ((uint32_t)(p[1] & 0x3F) << 12) |
                             ((uint32_t)(p[2] & 0x3F) << 6) | (uint32_t)(p[3] & 0x3F);
                        adv = 4;
                    }
                    if (cp == 0) {
                        /* Invalid UTF-8 lead byte: escape the byte itself.
                         * (unsigned char) matters: a byte >= 0x80 is a
                         * negative char on this platform, and %04x of a
                         * negative int prints 8 hex digits ("ffffff80"),
                         * which truncated the escape in an 8-byte buffer. */
                        char buf[16];
                        snprintf(buf, sizeof(buf), "\\u%04x", (unsigned char)*p);
                        sb_append(sb, buf);
                    } else if (cp < 0x10000) {
                        char buf[8];
                        snprintf(buf, sizeof(buf), "\\u%04x", cp);
                        sb_append(sb, buf);
                    } else {
                        uint32_t v = cp - 0x10000;
                        char buf[16];
                        snprintf(buf, sizeof(buf), "\\u%04x\\u%04x",
                                 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF));
                        sb_append(sb, buf);
                    }
                    p += adv - 1;
                }
        }
    }
    sb_append_ch(sb, '"');
}

void json_write_str_array(StrBuf *sb, char *const *items, size_t count) {
    sb_append_ch(sb, '[');
    for (size_t i = 0; i < count; i++) {
        if (i) sb_append_ch(sb, ',');
        json_write_escaped(sb, items[i]);
    }
    sb_append_ch(sb, ']');
}

/* ---------------- strict parser ---------------- */

static JsonValue *parse_value(JsonParser *p);

static void pfail(JsonParser *p, const char *msg) {
    if (!p->failed) {
        p->failed = true;
        snprintf(p->err, sizeof(p->err), "%s (at byte %zu)", msg, p->pos);
    }
}

static void skip_ws(JsonParser *p) {
    while (p->pos < p->len) {
        char c = p->src[p->pos];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->pos++;
        else break;
    }
}

static int hex4(JsonParser *p, uint32_t *out) {
    if (p->pos + 4 > p->len) { pfail(p, "bad \\u escape"); return -1; }
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p->src[p->pos + i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (uint32_t)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (uint32_t)(c - 'A' + 10);
        else { pfail(p, "bad \\u digit"); return -1; }
    }
    p->pos += 4;
    *out = v;
    return 0;
}

static void utf8_emit(StrBuf *sb, uint32_t cp) {
    if (cp < 0x80) sb_append_ch(sb, (char)cp);
    else if (cp < 0x800) {
        sb_append_ch(sb, (char)(0xC0 | (cp >> 6)));
        sb_append_ch(sb, (char)(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        sb_append_ch(sb, (char)(0xE0 | (cp >> 12)));
        sb_append_ch(sb, (char)(0x80 | ((cp >> 6) & 0x3F)));
        sb_append_ch(sb, (char)(0x80 | (cp & 0x3F)));
    } else {
        sb_append_ch(sb, (char)(0xF0 | (cp >> 18)));
        sb_append_ch(sb, (char)(0x80 | ((cp >> 12) & 0x3F)));
        sb_append_ch(sb, (char)(0x80 | ((cp >> 6) & 0x3F)));
        sb_append_ch(sb, (char)(0x80 | (cp & 0x3F)));
    }
}

static char *parse_string_raw(JsonParser *p) {
    if (p->pos >= p->len || p->src[p->pos] != '"') {
        pfail(p, "expected string");
        return NULL;
    }
    p->pos++;
    StrBuf sb;
    sb_init(&sb);
    while (p->pos < p->len) {
        char c = p->src[p->pos++];
        if ((unsigned char)c < 0x20) { pfail(p, "raw control char in string"); goto fail; }
        if (c == '"') {
            if (sb.oom) { pfail(p, "oom in string"); goto fail; }
            char *out = arena_strdup(p->arena, sb.data ? sb.data : "");
            sb_free(&sb);
            return out;
        }
        if (c == '\\') {
            if (p->pos >= p->len) { pfail(p, "dangling escape"); goto fail; }
            char e = p->src[p->pos++];
            switch (e) {
                case '"': sb_append_ch(&sb, '"'); break;
                case '\\': sb_append_ch(&sb, '\\'); break;
                case '/': sb_append_ch(&sb, '/'); break;
                case 'b': sb_append_ch(&sb, '\b'); break;
                case 'f': sb_append_ch(&sb, '\f'); break;
                case 'n': sb_append_ch(&sb, '\n'); break;
                case 'r': sb_append_ch(&sb, '\r'); break;
                case 't': sb_append_ch(&sb, '\t'); break;
                case 'u': {
                    uint32_t cp;
                    if (hex4(p, &cp) != 0) goto fail;
                    if (cp >= 0xD800 && cp <= 0xDBFF) {
                        /* high surrogate: require low surrogate next */
                        if (p->pos + 1 < p->len && p->src[p->pos] == '\\' &&
                            p->src[p->pos + 1] == 'u') {
                            p->pos += 2;
                            uint32_t lo;
                            if (hex4(p, &lo) != 0) goto fail;
                            if (lo < 0xDC00 || lo > 0xDFFF) {
                                pfail(p, "invalid surrogate pair");
                                goto fail;
                            }
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        } else {
                            pfail(p, "lone high surrogate");
                            goto fail;
                        }
                    } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                        pfail(p, "lone low surrogate");
                        goto fail;
                    }
                    utf8_emit(&sb, cp);
                    break;
                }
                default: pfail(p, "bad escape char"); goto fail;
            }
        } else {
            sb_append_ch(&sb, c);
        }
    }
    pfail(p, "unterminated string");
fail:
    sb_free(&sb);
    return NULL;
}

static JsonValue *new_value(JsonParser *p, JsonType t) {
    JsonValue *v = (JsonValue *)arena_alloc(p->arena, sizeof(JsonValue));
    v->type = t;
    v->b = false;
    v->num = 0;
    v->str = NULL;
    v->items = NULL;
    v->keys = NULL;
    v->count = 0;
    return v;
}

static JsonValue *parse_value(JsonParser *p) {
    if (p->failed) return NULL;
    if (++p->depth > 64) { pfail(p, "nesting too deep"); return NULL; }
    skip_ws(p);
    if (p->pos >= p->len) { pfail(p, "unexpected end"); return NULL; }
    char c = p->src[p->pos];
    JsonValue *v = NULL;
    if (c == '{') {
        p->pos++;
        v = new_value(p, JSON_OBJECT);
        size_t cap = 0;
        skip_ws(p);
        if (p->pos < p->len && p->src[p->pos] == '}') { p->pos++; p->depth--; return v; }
        while (p->pos < p->len) {
            char *key = parse_string_raw(p);
            if (!key) { v = NULL; goto done; }
            skip_ws(p);
            if (p->pos >= p->len || p->src[p->pos] != ':') { pfail(p, "expected ':'"); v = NULL; goto done; }
            p->pos++;
            JsonValue *val = parse_value(p);
            if (!val) { v = NULL; goto done; }
            /* duplicate keys rejected */
            for (size_t i = 0; i < v->count; i++) {
                if (strcmp(v->keys[i], key) == 0) { pfail(p, "duplicate key"); v = NULL; goto done; }
            }
            if (v->count == cap) {
                size_t ncap = cap ? cap * 2 : 8;
                JsonValue **ni = (JsonValue **)arena_alloc(p->arena, ncap * sizeof(*ni));
                char **nk = (char **)arena_alloc(p->arena, ncap * sizeof(*nk));
                if (v->count) {
                    memcpy(ni, v->items, v->count * sizeof(*ni));
                    memcpy(nk, v->keys, v->count * sizeof(*nk));
                }
                v->items = ni;
                v->keys = nk;
                cap = ncap;
            }
            v->keys[v->count] = key;
            v->items[v->count] = val;
            v->count++;
            skip_ws(p);
            if (p->pos < p->len && p->src[p->pos] == ',') { p->pos++; skip_ws(p); continue; }
            if (p->pos < p->len && p->src[p->pos] == '}') { p->pos++; break; }
            pfail(p, "expected ',' or '}'");
            v = NULL;
            goto done;
        }
    } else if (c == '[') {
        p->pos++;
        v = new_value(p, JSON_ARRAY);
        size_t cap = 0;
        skip_ws(p);
        if (p->pos < p->len && p->src[p->pos] == ']') { p->pos++; p->depth--; return v; }
        while (p->pos < p->len) {
            JsonValue *val = parse_value(p);
            if (!val) { v = NULL; goto done; }
            if (v->count == cap) {
                size_t ncap = cap ? cap * 2 : 8;
                JsonValue **ni = (JsonValue **)arena_alloc(p->arena, ncap * sizeof(*ni));
                if (v->count) memcpy(ni, v->items, v->count * sizeof(*ni));
                v->items = ni;
                cap = ncap;
            }
            v->items[v->count++] = val;
            skip_ws(p);
            if (p->pos < p->len && p->src[p->pos] == ',') { p->pos++; continue; }
            if (p->pos < p->len && p->src[p->pos] == ']') { p->pos++; break; }
            pfail(p, "expected ',' or ']'");
            v = NULL;
            goto done;
        }
    } else if (c == '"') {
        v = new_value(p, JSON_STRING);
        v->str = parse_string_raw(p);
        if (!v->str) v = NULL;
    } else if (c == 't') {
        if (p->pos + 4 <= p->len && memcmp(p->src + p->pos, "true", 4) == 0) {
            p->pos += 4;
            v = new_value(p, JSON_BOOL);
            v->b = true;
        } else pfail(p, "bad literal");
    } else if (c == 'f') {
        if (p->pos + 5 <= p->len && memcmp(p->src + p->pos, "false", 5) == 0) {
            p->pos += 5;
            v = new_value(p, JSON_BOOL);
        } else pfail(p, "bad literal");
    } else if (c == 'n') {
        if (p->pos + 4 <= p->len && memcmp(p->src + p->pos, "null", 4) == 0) {
            p->pos += 4;
            v = new_value(p, JSON_NULL);
        } else pfail(p, "bad literal");
    } else if (c == '-' || (c >= '0' && c <= '9')) {
        char buf[64];
        size_t start = p->pos;
        size_t i = 0;
        while (p->pos < p->len && i < sizeof(buf) - 1) {
            char d = p->src[p->pos];
            if ((d >= '0' && d <= '9') || d == '-' || d == '+' || d == '.' ||
                d == 'e' || d == 'E') {
                buf[i++] = d;
                p->pos++;
            } else break;
        }
        buf[i] = '\0';
        v = new_value(p, JSON_NUMBER);
        v->num = strtod(buf, NULL);
        (void)start;
    } else {
        pfail(p, "unexpected character");
    }

done:
    p->depth--;
    return v;
}

JsonValue *json_parse(JsonParser *p, const char *src, size_t len) {
    p->arena = p->arena;
    p->src = src;
    p->pos = 0;
    p->len = len;
    p->depth = 0;
    p->failed = false;
    p->err[0] = '\0';
    JsonValue *v = parse_value(p);
    if (!v) return NULL;
    skip_ws(p);
    if (p->pos != p->len) {
        pfail(p, "trailing garbage after JSON");
        return NULL;
    }
    return v;
}

const JsonValue *json_obj_get(const JsonValue *obj, const char *key) {
    if (!obj || obj->type != JSON_OBJECT) return NULL;
    for (size_t i = 0; i < obj->count; i++) {
        if (strcmp(obj->keys[i], key) == 0) return obj->items[i];
    }
    return NULL;
}
