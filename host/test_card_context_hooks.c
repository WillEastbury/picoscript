#include "picovm.h"
#include "pico_hooks.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static int card_provider(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    (void)rs1; (void)rs2;
    if (hook != PV_HOOK_CARD_READ) return 0;
    ctx->regs[rd] = pv_span_from_bytes(ctx, "card-ok", 7);
    ctx->host_status = PV_TENSOR_OK;
    return 1;
}

static int context_provider(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    (void)rs1; (void)rs2;
    if (hook != PV_HOOK_CONTEXT_GETTRACEID) return 0;
    ctx->regs[rd] = pv_span_from_bytes(ctx, "trace-ok", 8);
    ctx->host_status = PV_TENSOR_OK;
    return 1;
}

int main(void)
{
    static uint8_t memory[65536];
    pv_ctx ctx;
    pv_init(&ctx);
    ctx.mem = memory;
    ctx.mem_size = sizeof(memory);
    ctx.caps = PV_CAP_ALL;
    pv_card_hook = card_provider;
    pv_context_hook = context_provider;
    int card = (int)pv_host2(&ctx, PV_HOOK_CARD_READ, 0, 0);
    int trace = (int)pv_host2(&ctx, PV_HOOK_CONTEXT_GETTRACEID, 0, 0);
    assert(card > 0 && ctx.span_len[card] == 7);
    assert(trace > 0 && ctx.span_len[trace] == 8);
    assert(memcmp(memory + ctx.span_ptr[card], "card-ok", 7) == 0);
    assert(memcmp(memory + ctx.span_ptr[trace], "trace-ok", 8) == 0);
    puts("PASS card/context provider hooks");
    return 0;
}
