#include "picovm.h"
#include "pico_hooks.h"

#include <stdio.h>

#define CHECK(x) do { if (!(x)) { \
  fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

static int tensor_provider(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    (void)rs1; (void)rs2;
    if (hook == PV_HOOK_TENSOR_HASACCEL) { ctx->regs[rd] = 1; return 1; }
    return 0;
}

static int compute_provider(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    (void)rs1; (void)rs2;
    switch (hook) {
        case PV_HOOK_TENSOR_MAP: ctx->regs[rd] = 77; return 1;
        case PV_HOOK_TENSOR_VIEW: ctx->regs[rd] = 78; return 1;
        case PV_HOOK_CATQ_OPTIMIZE: ctx->regs[rd] = 88; return 1;
        case PV_HOOK_BITLINEAR_MATVECCATQ: ctx->regs[rd] = 89; return 1;
        case PV_HOOK_MOE_FORWARD: ctx->regs[rd] = 90; return 1;
        case PV_HOOK_ASYNC_SUBMIT: ctx->regs[rd] = 91; return 1;
        case PV_HOOK_ASYNC_WAIT: ctx->regs[rd] = 1; return 1;
        case PV_HOOK_ASYNC_RESULT: ctx->regs[rd] = 92; return 1;
        case PV_HOOK_SHARD_LOAD: ctx->regs[rd] = 93; return 1;
        case PV_HOOK_SHARD_SAVE: ctx->regs[rd] = 1; return 1;
    }
    return 0;
}

int main(void)
{
    static unsigned char mem[256 * 1024];
    pv_ctx ctx;

    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = (long)sizeof(mem);
    ctx.caps = PV_CAP_ALL;
    ctx.cap_ceiling = PV_CAP_ALL;
    pv_tensor_hook = tensor_provider;
    pv_compute_hook = compute_provider;

    CHECK((int)pv_host2(&ctx, PV_HOOK_TENSOR_HASACCEL, 0, 0) == 1);
    CHECK((int)pv_host2(&ctx, PV_HOOK_TENSOR_MAP, 1, 2) == 77);
    CHECK((int)pv_host2(&ctx, PV_HOOK_TENSOR_VIEW, 77, 3) == 78);
    CHECK((int)pv_host2(&ctx, PV_HOOK_CATQ_OPTIMIZE, 5, 77) == 88);
    CHECK((int)pv_host2(&ctx, PV_HOOK_BITLINEAR_MATVECCATQ, 88, 6) == 89);
    CHECK((int)pv_host2(&ctx, PV_HOOK_MOE_FORWARD, 7, 8) == 90);
    CHECK((int)pv_host2(&ctx, PV_HOOK_ASYNC_SUBMIT, 9, 10) == 91);
    CHECK((int)pv_host2(&ctx, PV_HOOK_ASYNC_WAIT, 91, 1000) == 1);
    CHECK((int)pv_host2(&ctx, PV_HOOK_ASYNC_RESULT, 91, 0) == 92);
    CHECK((int)pv_host2(&ctx, PV_HOOK_SHARD_LOAD, 11, 12) == 93);
    CHECK((int)pv_host2(&ctx, PV_HOOK_SHARD_SAVE, 93, 13) == 1);

    puts("PASS compute hook seams");
    return 0;
}
