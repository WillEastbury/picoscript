/* test_schema_validate.c -- unit test of schema store + Add/UpdateCard
 * validation in host/picowal/storage_file.c, bypassing the VM's bytecode
 * layer and HTTP layer entirely (calls pv_storage_file_hook directly). */
#include "picovm.h"
#include "storage_file.h"
#include <stdio.h>
#include <string.h>
#include <assert.h>

#define HOOK_GETSCHEMAFORPACK 0x60
#define HOOK_SETSCHEMAFORPACK 0x61
#define HOOK_ADDCARD    0x62
#define HOOK_UPDATECARD 0x63
#define HOOK_READCARD   0x66
#define HOOK_USEPACK    0x68

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

int main(void) {
    remove("test_schema.dat");
    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = sizeof(mem);

    if (pwf_storage_open("test_schema.dat") != 0) { fprintf(stderr, "open failed\n"); return 1; }

    /* Pack 10 has no schema yet -- Add/Update should be permissive. */
    ctx.regs[1] = 10; ctx.regs[2] = span_from_str("{\"whatever\":\"goes\"}");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    assert((int)ctx.regs[0] >= 0);
    printf("no schema bound -> AddCard permissive: ok (id=%d)\n", (int)ctx.regs[0]);

    /* Bind a schema to pack 10: qty:int (required), note:str (required). */
    ctx.regs[1] = 10;
    ctx.regs[2] = span_from_str("{\"fields\":[{\"name\":\"qty\",\"type\":\"int\"},{\"name\":\"note\",\"type\":\"str\"}]}");
    pv_storage_file_hook(&ctx, HOOK_SETSCHEMAFORPACK, 0, 1, 2);
    assert(ctx.regs[0] == 1);

    /* GetSchemaForPack round-trips. */
    ctx.regs[1] = 10;
    pv_storage_file_hook(&ctx, HOOK_GETSCHEMAFORPACK, 0, 1, 0);
    int sspan = (int)ctx.regs[0];
    assert(sspan != 0 && ctx.span_len[sspan] > 0);
    printf("GetSchemaForPack round-trip -> ok\n");

    /* Valid payload: qty is a number, note is a string -> accepted. */
    ctx.regs[1] = 10;
    ctx.regs[2] = span_from_str("{\"qty\":3,\"note\":\"widget\"}");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    int id_ok = (int)ctx.regs[0];
    assert(id_ok >= 0);
    printf("valid payload -> AddCard accepted (id=%d)\n", id_ok);

    /* Missing required field (note) -> rejected (-2). */
    ctx.regs[1] = 10;
    ctx.regs[2] = span_from_str("{\"qty\":3}");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    assert((int)ctx.regs[0] == -2);
    printf("missing required field -> AddCard rejected: ok\n");

    /* Wrong type (qty as a string instead of a number) -> rejected. */
    ctx.regs[1] = 10;
    ctx.regs[2] = span_from_str("{\"qty\":\"three\",\"note\":\"widget\"}");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    assert((int)ctx.regs[0] == -2);
    printf("wrong type (qty as string) -> AddCard rejected: ok\n");

    /* Wrong type the other way (note as a number instead of a string). */
    ctx.regs[1] = 10;
    ctx.regs[2] = span_from_str("{\"qty\":3,\"note\":42}");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    assert((int)ctx.regs[0] == -2);
    printf("wrong type (note as number) -> AddCard rejected: ok\n");

    /* UpdateCard is validated too: valid update accepted. */
    ctx.regs[1] = 10;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = id_ok;
    ctx.regs[2] = span_from_str("{\"qty\":9,\"note\":\"updated\"}");
    pv_storage_file_hook(&ctx, HOOK_UPDATECARD, 0, 1, 2);
    assert((int)ctx.regs[0] == 1);
    printf("valid UpdateCard -> accepted: ok\n");

    /* Confirm the update actually landed. */
    ctx.regs[1] = 10; ctx.regs[2] = id_ok;
    pv_storage_file_hook(&ctx, HOOK_READCARD, 0, 1, 2);
    int rspan = (int)ctx.regs[0];
    assert(rspan != 0);
    printf("read after valid update -> [%.*s]\n", ctx.span_len[rspan], &ctx.mem[ctx.span_ptr[rspan]]);

    /* Invalid UpdateCard (missing field) is rejected and the old value survives. */
    ctx.regs[1] = 10;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = id_ok;
    ctx.regs[2] = span_from_str("{\"qty\":1}");
    pv_storage_file_hook(&ctx, HOOK_UPDATECARD, 0, 1, 2);
    assert((int)ctx.regs[0] == 0);
    printf("invalid UpdateCard (missing field) -> rejected: ok\n");

    ctx.regs[1] = 10; ctx.regs[2] = id_ok;
    pv_storage_file_hook(&ctx, HOOK_READCARD, 0, 1, 2);
    rspan = (int)ctx.regs[0];
    /* still the last VALID update ("updated"), not the rejected one */
    char got[64]; int gl = ctx.span_len[rspan];
    memcpy(got, &ctx.mem[ctx.span_ptr[rspan]], (size_t)gl); got[gl] = 0;
    assert(strstr(got, "updated") != NULL);
    printf("rejected update did not overwrite prior valid data -> ok\n");

    pwf_storage_close();
    remove("test_schema.dat");
    printf("ALL PASS\n");
    return 0;
}
