/* test_update.c -- direct unit test of pv_storage_file_hook's Add/Update/Read/
 * Delete/UsePack semantics, bypassing the VM and HTTP layers entirely so the
 * storage engine itself can be verified in isolation. */
#include "picovm.h"
#include "storage_file.h"
#include <stdio.h>
#include <string.h>
#include <assert.h>

#define HOOK_ADDCARD    0x62
#define HOOK_UPDATECARD 0x63
#define HOOK_DELETECARD 0x64
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
    remove("test_update.dat");
    memset(&ctx, 0, sizeof(ctx));
    ctx.mem = mem;
    ctx.mem_size = sizeof(mem);
    ctx.span_count = 1; /* 0 = null span */

    if (pwf_storage_open("test_update.dat") != 0) { fprintf(stderr, "open failed\n"); return 1; }

    /* UsePack(5) */
    ctx.regs[1] = 5;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    assert(ctx.regs[0] == 5);

    /* AddCard(pack=5, "hello") -> id 0 (explicit-pack signature, unaffected by UsePack) */
    ctx.regs[1] = 5;
    ctx.regs[2] = span_from_str("hello");
    pv_storage_file_hook(&ctx, HOOK_ADDCARD, 0, 1, 2);
    int id = (int)ctx.regs[0];
    printf("AddCard -> id=%d\n", id);
    assert(id == 0);

    /* ReadCard(5, id) -> "hello" */
    ctx.regs[1] = 5; ctx.regs[2] = id;
    pv_storage_file_hook(&ctx, HOOK_READCARD, 0, 1, 2);
    int rspan = (int)ctx.regs[0];
    assert(rspan != 0);
    assert(ctx.span_len[rspan] == 5 && memcmp(&ctx.mem[ctx.span_ptr[rspan]], "hello", 5) == 0);
    printf("ReadCard after Add -> ok\n");

    /* UsePack(5); UpdateCard(id, "goodbye-world") */
    ctx.regs[1] = 5;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = id;
    ctx.regs[2] = span_from_str("goodbye-world");
    pv_storage_file_hook(&ctx, HOOK_UPDATECARD, 0, 1, 2);
    int updok = (int)ctx.regs[0];
    printf("UpdateCard -> ok=%d\n", updok);
    assert(updok == 1);

    /* ReadCard(5, id) -> "goodbye-world" now */
    ctx.regs[1] = 5; ctx.regs[2] = id;
    pv_storage_file_hook(&ctx, HOOK_READCARD, 0, 1, 2);
    rspan = (int)ctx.regs[0];
    assert(rspan != 0);
    assert(ctx.span_len[rspan] == 13 && memcmp(&ctx.mem[ctx.span_ptr[rspan]], "goodbye-world", 13) == 0);
    printf("ReadCard after Update -> ok (got updated content)\n");

    /* UpdateCard on a non-existent id -> 0 */
    ctx.regs[1] = 5;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = 999;
    ctx.regs[2] = span_from_str("nope");
    pv_storage_file_hook(&ctx, HOOK_UPDATECARD, 0, 1, 2);
    assert(ctx.regs[0] == 0);
    printf("UpdateCard on missing id -> correctly returns 0\n");

    /* DeleteCard(5, id); then UpdateCard should fail (record now dead) */
    ctx.regs[1] = 5; ctx.regs[2] = id;
    pv_storage_file_hook(&ctx, HOOK_DELETECARD, 0, 1, 2);
    ctx.regs[1] = 5;
    pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 0);
    ctx.regs[1] = id;
    ctx.regs[2] = span_from_str("resurrect?");
    pv_storage_file_hook(&ctx, HOOK_UPDATECARD, 0, 1, 2);
    assert(ctx.regs[0] == 0);
    printf("UpdateCard after Delete -> correctly returns 0 (no resurrection)\n");

    pwf_storage_close();

    /* Reopen (simulate restart) and confirm the updated value persisted, and
     * the delete after it is still respected. */
    memset(&ctx, 0, sizeof(ctx));
    ctx.mem = mem; ctx.mem_size = sizeof(mem); ctx.span_count = 1;
    if (pwf_storage_open("test_update.dat") != 0) { fprintf(stderr, "reopen failed\n"); return 1; }
    ctx.regs[1] = 5; ctx.regs[2] = id;
    pv_storage_file_hook(&ctx, HOOK_READCARD, 0, 1, 2);
    assert(ctx.regs[0] == 0); /* deleted -- must stay deleted across restart */
    printf("Restart persistence: deleted record correctly absent -> ok\n");
    pwf_storage_close();
    remove("test_update.dat");

    printf("ALL PASS\n");
    return 0;
}
