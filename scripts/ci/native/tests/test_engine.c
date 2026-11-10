/* test_engine.c — thin entry: selftest runner + builtin fuzz driver.
 * Real assertions live in src/selftest.c (shared with nse-ci-engine). */
#include "nse_ci_engine.h"

#include <string.h>

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--fuzz") == 0) {
        return engine_selftest_fuzz(argc - 2, argv + 2);
    }
    return engine_selftest();
}
