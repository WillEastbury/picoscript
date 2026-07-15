/* main_app.c -- native unified-engine HTTP server: file-backed Storage.*
 * (storage_file.c: schema store + validation + CRUD + query/list, see its
 * header comment) + the generalized app_router (app_router.eng -> .c via
 * lower_to_c), running on the cross-platform thread-pool runtime
 * (picovm_pool.c). Builds unmodified on Windows/Linux/macOS.
 *
 * URL shape (deliberately mirrors picoweb's /wal/* convention so client code
 * -- e.g. the WebIDE's live-server mode -- can target either backend with
 * the same request shapes, modulo the /wal/ prefix which picoweb adds and
 * this binary does not):
 *   GET/PUT   /schema/{pack}          -- Storage.Get/SetSchemaForPack
 *   POST      /query/{pack}           body=query text ("" or "field=value")
 *   GET       /list/{pack}            -- all live records in the pack
 *   POST      /{pack}                 body=JSON        -- create (id auto-assigned)
 *   GET       /{pack}/{record}        -- read
 *   PUT       /{pack}/{record}        body=JSON        -- update
 *   DELETE    /{pack}/{record}        -- delete
 *   OPTIONS   (any path)              -- CORS preflight
 * All responses carry Access-Control-Allow-Origin: * (this is a local
 * developer tool, not a public multi-tenant server -- see docs before
 * exposing beyond localhost).
 */
#include "picovm.h"
#include "picovm_pool.h"
#include "storage_file.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int64_t pico_main(pv_ctx *ctx);

static int64_t dispatch(pv_ctx *ctx) { return pico_main(ctx); }

int main(int argc, char **argv) {
    int port = 8090;
    int workers = 4;
    const char *data_path = "picowal_app.dat";
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
    printf("picowal-app: port=%d workers=%d store=%s\n", port, workers, data_path);
    pv_pool_run(&pool);
    pv_pool_stop(&pool);
    pwf_storage_close();
    return 0;
}
