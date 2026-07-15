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
    ctx.regs[1] = pack; ctx.regs[2] = span_from_str(json);
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    return (int)ctx.regs[0];
}

static int32_t query(int32_t pack, const char *q) {
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

    /* Deleted records are excluded from both list and filtered queries. */
    ctx.regs[1] = 20; ctx.regs[2] = id2;
    pv_storage_file_hook(&ctx, HOOK_DELETECARD, 0, 1, 2);
    n = query(20, "");
    printf("list after delete -> count=%d\n", n);
    assert(n == 2);
    n = query(20, "qty=7");
    assert(n == 1 && result_at(0) == id3);
    printf("filtered query after delete correctly excludes it -> ok\n");

    pwf_storage_close();
    remove("test_query.dat");
    printf("ALL PASS\n");
    return 0;
}
