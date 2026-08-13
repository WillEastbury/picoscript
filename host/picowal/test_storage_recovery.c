/* Recovery tests for the hosted PicoWAL format-v2 append log. */
#include "picovm.h"
#include "storage_file.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

#define HOOK_USEPACK    0x68
#define HOOK_PUTCARD    0x1A5
#define HOOK_READEXACT  0x1A6
#define HOOK_EXISTS     0x1A8

/* storage_file.c references these helpers in schema/query paths. This focused
 * executable does not exercise those paths, so lightweight link stubs keep the
 * recovery test independent from the complete VM. */
int64_t pv_host2(pv_ctx *ctx, int hook, int64_t a, int64_t b) {
    (void)ctx; (void)hook; (void)a; (void)b; return 0;
}
int pv_map_value_kind(pv_ctx *ctx, int key_span_handle) {
    (void)ctx; (void)key_span_handle; return -1;
}

static pv_ctx ctx;
static uint8_t memory[65536];

static void reset_ctx(void) {
    memset(&ctx, 0, sizeof(ctx));
    memset(memory, 0, sizeof(memory));
    ctx.mem = memory;
    ctx.mem_size = sizeof(memory);
    ctx.arena_top = 1024;
    ctx.span_count = 1;
}

static int span_from_bytes(const char *text) {
    int h = ctx.span_count++;
    int n = (int)strlen(text);
    memcpy(ctx.mem + ctx.arena_top, text, (size_t)n);
    ctx.span_ptr[h] = ctx.arena_top;
    ctx.span_len[h] = n;
    ctx.arena_top += (uint32_t)n;
    return h;
}

static void use_pack(int pack) {
    ctx.regs[1] = pack;
    assert(pv_storage_file_hook(&ctx, HOOK_USEPACK, 0, 1, 2));
}

static void put(int id, const char *text) {
    ctx.regs[1] = id;
    ctx.regs[2] = span_from_bytes(text);
    assert(pv_storage_file_hook(&ctx, HOOK_PUTCARD, 0, 1, 2));
    assert(ctx.regs[0] == 0);
}

static int exists(int id) {
    ctx.regs[1] = id;
    assert(pv_storage_file_hook(&ctx, HOOK_EXISTS, 0, 1, 2));
    return ctx.regs[0];
}

static void expect_value(int id, const char *text) {
    ctx.regs[1] = id;
    assert(pv_storage_file_hook(&ctx, HOOK_READEXACT, 0, 1, 2));
    int h = ctx.regs[0];
    size_t n = strlen(text);
    assert(h > 0 && ctx.span_len[h] == (int32_t)n);
    assert(memcmp(ctx.mem + ctx.span_ptr[h], text, n) == 0);
}

static uint64_t file_size(FILE *f) {
    assert(fseek(f, 0, SEEK_END) == 0);
    return (uint64_t)ftell(f);
}

int main(void) {
    const char *path = "test_storage_recovery.dat";
    remove(path);
    /* An existing zero-byte file is the same format-if-empty case as a path
     * that does not exist. */
    FILE *f = fopen(path, "wb");
    assert(f);
    fclose(f);
    reset_ctx();
    assert(pwf_storage_open(path) == 0);
    use_pack(9);
    put(10, "alpha");
    uint64_t alpha_end = pwf_storage_recovery_offset();
    put(11, "beta");
    pwf_storage_close();

    /* Flip the final payload byte. Recovery rejects the complete bad-CRC
     * record, retains the preceding card, and repairs the physical tail. */
    f = fopen(path, "r+b");
    assert(f);
    assert(fseek(f, -1, SEEK_END) == 0);
    int byte = fgetc(f);
    assert(byte != EOF && fseek(f, -1, SEEK_CUR) == 0);
    fputc(byte ^ 0x5a, f);
    fclose(f);

    reset_ctx();
    assert(pwf_storage_open(path) == 0);
    use_pack(9);
    assert(exists(10) == 1);
    assert(exists(11) == 0);
    expect_value(10, "alpha");
    assert(pwf_storage_recovery_offset() == alpha_end);
    pwf_storage_close();

    f = fopen(path, "ab");
    assert(f);
    { const uint8_t torn[7] = {9, 0, 0, 0, 12, 0, 0};
      assert(fwrite(torn, 1, sizeof(torn), f) == sizeof(torn)); }
    fclose(f);

    reset_ctx();
    assert(pwf_storage_open(path) == 0);
    use_pack(9);
    assert(exists(10) == 1);
    assert(exists(12) == 0);
    pwf_storage_close();

    f = fopen(path, "rb");
    assert(f);
    assert(file_size(f) == alpha_end);
    fclose(f);
    remove(path);
    puts("hosted PicoWAL CRC/torn-tail recovery: PASS");
    return 0;
}
