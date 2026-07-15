/* main.c -- portable PicoWAL-compatible host server.
 * File-backed Storage.* (see storage_file.c) + PicoScript-compiled router
 * (router.eng -> router.c) running on the cross-platform thread-pool HTTP
 * runtime (picovm_pool.c). Builds unmodified on Windows/Linux/macOS.
 */
#include "picovm.h"
#include "picovm_pool.h"
#include "storage_file.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int64_t handler_router(pv_ctx *ctx);

static int64_t dispatch(pv_ctx *ctx) {
    return handler_router(ctx);
}

int main(int argc, char **argv) {
    int port = 8090;
    int workers = 4;
    const char *data_path = "picowal_host.dat";
    static pv_pool pool; /* PV_POOL_SLOTS * PV_POOL_ARENA_SIZE is multi-MB: BSS, not stack */

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) workers = atoi(argv[++i]);
        else if (strcmp(argv[i], "--data") == 0 && i + 1 < argc) data_path = argv[++i];
    }

    if (pwf_storage_open(data_path) != 0) {
        fprintf(stderr, "failed to open storage file: %s\n", data_path);
        return 1;
    }
    pv_storage_hook = pv_storage_file_hook;

    if (pv_pool_init(&pool, port, workers, dispatch) != 0) {
        fprintf(stderr, "server init failed\n");
        pwf_storage_close();
        return 1;
    }
    printf("picowal-host: port=%d workers=%d store=%s\n", port, workers, data_path);
    pv_pool_run(&pool);
    pv_pool_stop(&pool);
    pwf_storage_close();
    return 0;
}
