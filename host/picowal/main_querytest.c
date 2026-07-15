/* main_querytest.c -- quick smoke test wiring query_router.c's compiled
 * handler into the native pool + file-backed storage, to prove the
 * List/Query engine works end-to-end over real HTTP (not just via direct
 * hook calls in the unit tests). Not part of the production binary. */
#include "picovm.h"
#include "picovm_pool.h"
#include "storage_file.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int64_t pico_main(pv_ctx *ctx);

static int64_t dispatch(pv_ctx *ctx) { return pico_main(ctx); }

int main(int argc, char **argv) {
    int port = 8095;
    static pv_pool pool;
    if (argc > 1) port = atoi(argv[1]);

    if (pwf_storage_open("query_router_test.dat") != 0) {
        fprintf(stderr, "failed to open storage file\n");
        return 1;
    }
    pv_storage_hook = pv_storage_file_hook;

    if (pv_pool_init(&pool, port, 2, dispatch) != 0) {
        fprintf(stderr, "server init failed\n");
        pwf_storage_close();
        return 1;
    }
    printf("query-router-test: port=%d\n", port);
    pv_pool_run(&pool);
    pv_pool_stop(&pool);
    pwf_storage_close();
    return 0;
}
