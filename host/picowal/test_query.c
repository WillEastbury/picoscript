/* test_query.c -- unit test of Storage.QueryCard/QueryResult (list + simple
 * field=value filter) in host/picowal/storage_file.c. */
#include "picovm.h"
#include "storage_file.h"
#include <stdio.h>
#include <string.h>
#include <assert.h>

#define HOOK_ADDCARD     0x62
#define HOOK_DELETECARD  0x64
#define HOOK_QUERYCARD   0x67
#define HOOK_USEPACK     0x68
#define HOOK_QUERYRESULT 0x6E

static pv_ctx ctx;
static uint8_t mem[65536];

/* Mirrors picovm_pool.c's pv_worker_reset() -- a real server resets this
 * per-connection state before running each request's handler. This test
 * calls query()/add() many times against one long-lived ctx (unlike a real
 * server, which gets a fresh reset every request), so it must do the same
 * reset explicitly at each logical "request" boundary; skipping this
 * reproduces (and was how this file's own tests caught) a real bug in
 * pv_worker_reset itself: map_nmaps/me_count/map_pool_top are a per-ctx-
 * LIFETIME budget in picovm.c (Map.Free never lets pv_new_active_map reuse
 * a freed slot), so a worker that skipped resetting them would permanently
 * lose the ability to Map.New/Json.Parse after only PV_MAX_MAPS (16) uses
 * -- now fixed in picovm_pool.c's real pv_worker_reset, mirrored here. */
static void reset_between_requests(void) {
    ctx.span_count = 1;
    ctx.arena_top = 0x8000;
    ctx.map_nmaps = 1;
    ctx.map_active = 0;
    ctx.me_count = 0;
    ctx.map_pool_top = 0;
}

static int span_from_str(const char *s) {
    int len = (int)strlen(s);
    int h = ctx.span_count;
    for (int i = 0; i < len; i++) ctx.mem[ctx.arena_top + i] = (uint8_t)s[i];
    ctx.span_ptr[h] = ctx.arena_top;
    ctx.span_len[h] = len;
    ctx.span_count++;
    ctx.arena_top += len;
    return h;
}

static int add(int32_t pack, const char *json) {
    reset_between_requests();
    ctx.regs[1] = pack; ctx.regs[2] = span_from_str(json);
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    return (int)ctx.regs[0];
}

static int32_t query(int32_t pack, const char *q) {
    reset_between_requests();
    ctx.regs[1] = pack;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = span_from_str(q);
    pv_storage_file_hook(&ctx, HOOK_QUERYCARD, 0, 1, 0);
    return ctx.regs[0];
}

static int32_t result_at(int i) {
    ctx.regs[1] = i;
    pv_storage_file_hook(&ctx, HOOK_QUERYRESULT, 0, 1, 0);
    return ctx.regs[0];
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    remove("test_query.dat");
    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = sizeof(mem);
    if (pwf_storage_open("test_query.dat") != 0) { fprintf(stderr, "open failed\n"); return 1; }

    int id1 = add(20, "{\"qty\":3,\"name\":\"widget\"}");
    int id2 = add(20, "{\"qty\":7,\"name\":\"gadget\"}");
    int id3 = add(20, "{\"qty\":7,\"name\":\"gizmo\"}");
    add(21, "{\"qty\":100,\"name\":\"other-pack\"}"); /* different pack, must not leak in */

    /* List (empty query) -> all 3 live records in pack 20. */
    int32_t n = query(20, "");
    printf("list pack 20 -> count=%d\n", n);
    assert(n == 3);

    /* Filter on an int field. */
    n = query(20, "qty=7");
    printf("query qty=7 -> count=%d\n", n);
    assert(n == 2);
    {
        int32_t r0 = result_at(0), r1 = result_at(1);
        int got_id2 = (r0 == id2 || r1 == id2), got_id3 = (r0 == id3 || r1 == id3);
        assert(got_id2 && got_id3);
    }

    /* Filter on a string field. */
    n = query(20, "name=widget");
    printf("query name=widget -> count=%d\n", n);
    assert(n == 1);
    assert(result_at(0) == id1);

    /* Filter with no matches. */
    n = query(20, "qty=999");
    printf("query qty=999 (no match) -> count=%d\n", n);
    assert(n == 0);

    /* Multi-clause AND filter: only gizmo has both qty=7 AND name=gizmo. */
    n = query(20, "qty=7&name=gizmo");
    printf("query qty=7&name=gizmo -> count=%d\n", n);
    assert(n == 1 && result_at(0) == id3);

    /* Multi-clause AND filter with no overall match (qty=7 but name=widget
     * belongs to different records) -> 0 results even though each clause
     * alone would match something. */
    n = query(20, "qty=7&name=widget");
    printf("query qty=7&name=widget (no combined match) -> count=%d\n", n);
    assert(n == 0);

    /* Deleted records are excluded from both list and filtered queries. */
    reset_between_requests();
    ctx.regs[1] = 20; ctx.regs[2] = id2;
    pv_storage_file_hook(&ctx, HOOK_DELETECARD, 0, 1, 2);
    n = query(20, "");
    printf("list after delete -> count=%d\n", n);
    assert(n == 2);
    n = query(20, "qty=7");
    assert(n == 1 && result_at(0) == id3);
    printf("filtered query after delete correctly excludes it -> ok\n");

    /* Map-exhaustion regression guard: run MANY more filtered queries as
     * separate "requests" (each via query(), which calls
     * reset_between_requests() -- mirroring the REAL fix, in
     * picovm_pool.c's pv_worker_reset(), that resets map_nmaps/me_count/
     * map_pool_top between requests). Without that reset, this loop
     * reproduces the actual bug found while building this test: map_nmaps
     * is a per-ctx-LIFETIME budget in picovm.c (Map.Free never lets
     * pv_new_active_map reuse a freed slot), so any worker that skipped
     * resetting it would permanently lose the ability to Json.Parse after
     * only PV_MAX_MAPS (16) uses -- 30 further filtered queries here is far
     * past that ceiling, proving the reset keeps things working. */
    {
        int k, ok_count = 0;
        for (k = 0; k < 30; k++) {
            int32_t nn = query(20, "qty=7");
            if (nn == 1 && result_at(0) == id3) ok_count++;
        }
        printf("map-exhaustion guard: %d/30 repeated queries still correct\n", ok_count);
        assert(ok_count == 30);
    }

    pwf_storage_close();
    remove("test_query.dat");
    printf("ALL PASS\n");
    return 0;
}
