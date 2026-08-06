#include <stdint.h>
#include <stdio.h>

#include "picovm.h"
#include "pico_hooks.h"

static int failures;

#define CHECK(cond, text) do { \
    if (cond) printf("[PASS] %s\n", text); \
    else { printf("[FAIL] %s\n", text); failures++; } \
} while (0)

static int add_span(pv_ctx *ctx, uint32_t ptr, int32_t len)
{
    int handle = ctx->span_count++;
    ctx->span_ptr[handle] = ptr;
    ctx->span_len[handle] = len;
    return handle;
}

int main(void)
{
    static uint8_t mem[65536];
    pv_ctx ctx;
    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = sizeof(mem);

    ctx.regs[1] = 4;
    ctx.regs[2] = 2;
    pv_default_host(&ctx, PV_HOOK_MEDIA_SETSHAPE, 0, 1, 2, 0);
    CHECK(ctx.regs[0] == 1, "Media.SetShape accepts 4x2");

    const uint8_t gray[8] = {10, 12, 15, 20, 100, 90, 80, 70};
    const uint8_t pred[8] = {8, 10, 14, 18, 95, 95, 75, 75};
    for (int i = 0; i < 8; i++) {
        mem[0x1000 + i] = gray[i];
        mem[0x1100 + i] = pred[i];
    }
    ctx.regs[1] = add_span(&ctx, 0x1000, 8);
    pv_default_host(&ctx, PV_HOOK_MEDIA_GRAYDELTAENCODE, 0, 1, 0, 0);
    int delta = ctx.regs[0];
    ctx.regs[1] = delta;
    pv_default_host(&ctx, PV_HOOK_MEDIA_GRAYDELTADECODE, 0, 1, 0, 0);
    int decoded = ctx.regs[0];
    int same = decoded > 0;
    for (int i = 0; i < 8 && same; i++)
        same = mem[ctx.span_ptr[decoded] + i] == gray[i];
    CHECK(same, "grayscale delta encode/decode round-trips");

    int gh = add_span(&ctx, 0x1000, 8);
    int ph = add_span(&ctx, 0x1100, 8);
    ctx.regs[1] = gh;
    ctx.regs[2] = ph;
    pv_default_host(&ctx, PV_HOOK_MEDIA_H264RESIDUAL, 0, 1, 2, 0);
    int residual = ctx.regs[0];
    ctx.regs[1] = residual;
    pv_default_host(&ctx, PV_HOOK_MEDIA_H264RESTORE, 0, 1, 2, 0);
    int restored = ctx.regs[0];
    same = restored > 0;
    for (int i = 0; i < 8 && same; i++)
        same = mem[ctx.span_ptr[restored] + i] == gray[i];
    CHECK(same, "H264-style residual/restore round-trips");

    ctx.regs[1] = gh;
    ctx.regs[2] = ph;
    pv_default_host(&ctx, PV_HOOK_MEDIA_GRAYXORRESIDUAL, 0, 1, 2, 0);
    int xor_residual = ctx.regs[0];
    ctx.regs[1] = xor_residual;
    pv_default_host(&ctx, PV_HOOK_MEDIA_GRAYXORRESTORE, 0, 1, 2, 0);
    int xor_restored = ctx.regs[0];
    same = xor_restored > 0;
    for (int i = 0; i < 8 && same; i++)
        same = mem[ctx.span_ptr[xor_restored] + i] == gray[i];
    CHECK(same, "grayscale XOR residual/restore round-trips");

    pv_default_host(&ctx, PV_HOOK_MEDIA_HASACCEL, 0, 0, 0, 0);
    CHECK(ctx.regs[0] == 0, "Media.HasAccel false without native hook");
    pv_default_host(&ctx, PV_HOOK_MEDIA_HASHEVC, 0, 0, 0, 0);
    CHECK(ctx.regs[0] == 0, "Media.HasHevc false without decoder driver");
    return failures ? 1 : 0;
}
