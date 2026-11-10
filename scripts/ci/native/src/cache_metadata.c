/* cache_metadata.c — shared (Layer-B) cache provenance records.
 * Emits and STRICTLY validates the compatibility tuple that guards cache
 * restores: os, arch, python, uv, lock hash, schema. A mismatch on ANY
 * dimension invalidates the entry (mission: never restore incompatible
 * environments). */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <string.h>

void cache_metadata_emit(StrBuf *sb, const char *os, const char *arch,
                         const char *pyver, const char *uvver,
                         const char *lock_hash, const char *schema) {
    sb_append(sb, "{\"os\":");
    json_write_escaped(sb, os);
    sb_append(sb, ",\"arch\":");
    json_write_escaped(sb, arch);
    sb_append(sb, ",\"python\":");
    json_write_escaped(sb, pyver);
    sb_append(sb, ",\"uv\":");
    json_write_escaped(sb, uvver);
    sb_append(sb, ",\"lock_sha256\":");
    json_write_escaped(sb, lock_hash);
    sb_append(sb, ",\"schema\":");
    json_write_escaped(sb, schema);
    sb_append(sb, ",\"schema_version\":1}");
}

static bool str_field(const JsonValue *o, const char *key, char *out, size_t out_len) {
    const JsonValue *v = json_obj_get(o, key);
    if (!v || v->type != JSON_STRING || v->str[0] == '\0') return false;
    snprintf(out, out_len, "%s", v->str);
    return true;
}

int cache_metadata_validate(const char *json_text, char *err, size_t err_len) {
    Arena a;
    arena_init(&a, 1u << 14);
    JsonParser p;
    p.arena = &a;
    JsonValue *v = json_parse(&p, json_text, strlen(json_text));
    int rc = 0;
    if (!v) {
        snprintf(err, err_len, "invalid JSON: %s", p.err);
        rc = -1;
    } else if (v->type != JSON_OBJECT) {
        snprintf(err, err_len, "metadata must be an object");
        rc = -1;
    } else {
        static const char *dims[] = { "os", "arch", "python", "uv",
                                      "lock_sha256", "schema" };
        char tmp[256];
        for (size_t i = 0; i < sizeof(dims) / sizeof(dims[0]); i++) {
            if (!str_field(v, dims[i], tmp, sizeof(tmp))) {
                snprintf(err, err_len, "missing/empty dimension: %s", dims[i]);
                rc = -1;
                break;
            }
        }
        if (rc == 0) {
            const JsonValue *sv = json_obj_get(v, "schema_version");
            if (!sv || sv->type != JSON_NUMBER || sv->num != 1.0) {
                snprintf(err, err_len, "unsupported schema_version");
                rc = -1;
            }
        }
    }
    arena_free(&a);
    return rc;
}
