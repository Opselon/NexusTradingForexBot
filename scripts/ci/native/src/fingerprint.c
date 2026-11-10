/* fingerprint.c — content-addressed test fingerprints (Algorithm 5 basis)
 * and resource detection (cgroup v2/v1 aware). */
#include "nse_ci_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string.h>

int engine_fingerprint_test(const char *repo_root, const char *test_rel_path,
                            StrBuf *json_out) {
    char path[2048];
    snprintf(path, sizeof(path), "%s/%s", repo_root, test_rel_path);
    StrBuf content;
    sb_init(&content);
    if (!read_file_all(path, &content)) {
        sb_free(&content);
        return -1;
    }
    char file_hash[65];
    sha256_hex((const uint8_t *)(content.data ? content.data : ""), content.len,
               file_hash);
    sb_free(&content);

    /* Dependency-lock hash participates in the fingerprint (mission rule:
     * dependency changes invalidate reuse). */
    char lock_path[2048];
    snprintf(lock_path, sizeof(lock_path), "%s/requirements.lock", repo_root);
    StrBuf lock;
    sb_init(&lock);
    char lock_hash[65] = "missing";
    if (read_file_all(lock_path, &lock) && lock.data) {
        sha256_hex((const uint8_t *)lock.data, lock.len, lock_hash);
    }
    sb_free(&lock);

    ResourceInfo res;
    resource_detect(&res);

    sb_append(json_out, "{\"test_id\":");
    json_write_escaped(json_out, test_rel_path);
    sb_append(json_out, ",\"file_sha256\":\"");
    sb_append(json_out, file_hash);
    sb_append(json_out, "\",\"dependency_lock_sha256\":\"");
    sb_append(json_out, lock_hash);
    sb_append(json_out, "\",\"python_version\":\"3.11\"");
    sb_append(json_out, ",\"schema\":\"nse-fingerprint-v1\",\"cpu\":");
    sb_append_i64(json_out, res.cpu_effective);
    sb_append(json_out, "}");
    return 0;
}

/* ---------------- resource detection ---------------- */

void resource_detect(ResourceInfo *out) {
    out->cpu_effective = 0;
    out->mem_total_mb = -1;
    out->mem_limit_mb = -1;

    /* CPU: os.cpu_count() is wrong under container quotas; read cgroup v2
     * cpu.max first, then v1 cfs_quota_us/cfs_period_us. */
    long cpus = 0;
    FILE *f = fopen("/sys/fs/cgroup/cpu.max", "r");
    if (f) {
        char quota[64];
        long period = 100000;
        if (fgets(quota, sizeof(quota), f)) {
            char *sp = strchr(quota, ' ');
            if (sp) period = atol(sp + 1);
            if (strncmp(quota, "max", 3) != 0) {
                long q = atol(quota);
                if (q > 0 && period > 0) cpus = (q + period - 1) / period;
            }
        }
        fclose(f);
    } else {
        FILE *fq = fopen("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r");
        FILE *fp = fopen("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r");
        if (fq && fp) {
            long q = 0, per = 0;
            if (fscanf(fq, "%ld", &q) == 1 && fscanf(fp, "%ld", &per) == 1 &&
                q > 0 && per > 0) {
                cpus = (q + per - 1) / per;
            }
        }
        if (fq) fclose(fq);
        if (fp) fclose(fp);
    }

    long os_cpus = nse_sysconf_cpus();
    if (cpus <= 0 || (os_cpus > 0 && cpus > os_cpus)) cpus = os_cpus;
    out->cpu_effective = cpus > 0 ? cpus : 1;

    /* Memory: cgroup v2 memory.max, then v1 memory.limit_in_bytes. */
    FILE *m = fopen("/sys/fs/cgroup/memory.max", "r");
    if (m) {
        char buf[64];
        if (fgets(buf, sizeof(buf), m)) {
            if (strncmp(buf, "max", 3) != 0) out->mem_limit_mb = atol(buf) / (1024 * 1024);
        }
        fclose(m);
    } else {
        FILE *m1 = fopen("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r");
        if (m1) {
            char buf[64];
            if (fgets(buf, sizeof(buf), m1)) {
                long long v = atoll(buf);
                /* values > ~physical are "unlimited" on v1 */
                if (v > 0 && v < (1LL << 50)) out->mem_limit_mb = (long)(v / (1024 * 1024));
            }
            fclose(m1);
        }
    }
}
