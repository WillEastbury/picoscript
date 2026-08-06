#include <stdint.h>
#include <stdio.h>
#include <string.h>

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

static void put_i32be(uint8_t *p, int32_t value)
{
    uint32_t v = (uint32_t)value;
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static int32_t get_i32be(const uint8_t *p)
{
    return (int32_t)(((uint32_t)p[0] << 24) |
                     ((uint32_t)p[1] << 16) |
                     ((uint32_t)p[2] << 8) |
                     (uint32_t)p[3]);
}

int main(void)
{
    static uint8_t mem[65536];
    pv_ctx ctx;
    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = sizeof(mem);

    ctx.regs[1] = 2;
    ctx.regs[2] = 4;
    pv_default_host(&ctx, PV_HOOK_TENSOR_SETSHAPE, 0, 1, 2, 0);
    CHECK(ctx.regs[0] == 1 && ctx.tensor_rows == 2 && ctx.tensor_cols == 4,
          "Tensor.SetShape stores rows/cols");

    mem[0x1000] = 1; mem[0x1001] = 2; mem[0x1002] = 3; mem[0x1003] = 4;
    mem[0x1100] = 5; mem[0x1101] = 6; mem[0x1102] = 7; mem[0x1103] = 8;
    ctx.regs[1] = add_span(&ctx, 0x1000, 4);
    ctx.regs[2] = add_span(&ctx, 0x1100, 4);
    pv_default_host(&ctx, PV_HOOK_TENSOR_DOTI8, 0, 1, 2, 0);
    CHECK(ctx.regs[0] == 70, "Tensor.DotI8 computes signed dot product");

    for (int i = 0; i < 8; i++)
        mem[0x1200 + i] = (uint8_t)(i + 1);
    ctx.regs[1] = add_span(&ctx, 0x1200, 8);
    ctx.regs[2] = add_span(&ctx, 0x1100, 4);
    pv_default_host(&ctx, PV_HOOK_TENSOR_MATVECI8, 0, 1, 2, 0);
    int matvec = ctx.regs[0];
    CHECK(matvec > 0 && ctx.span_len[matvec] == 8,
          "Tensor.MatVecI8 returns two packed i32 rows");
    CHECK(get_i32be(mem + ctx.span_ptr[matvec]) == 70 &&
          get_i32be(mem + ctx.span_ptr[matvec] + 4) == 174,
          "Tensor.MatVecI8 values match");

    put_i32be(mem + 0x1300, 256);
    put_i32be(mem + 0x1304, -512);
    put_i32be(mem + 0x1308, 768);
    put_i32be(mem + 0x1400, 256);
    put_i32be(mem + 0x1404, 256);
    put_i32be(mem + 0x1408, -256);
    ctx.regs[1] = add_span(&ctx, 0x1300, 12);
    ctx.regs[2] = add_span(&ctx, 0x1400, 12);
    pv_default_host(&ctx, PV_HOOK_TENSOR_ADDI32, 0, 1, 2, 0);
    int add = ctx.regs[0];
    CHECK(get_i32be(mem + ctx.span_ptr[add]) == 512 &&
          get_i32be(mem + ctx.span_ptr[add] + 4) == -256 &&
          get_i32be(mem + ctx.span_ptr[add] + 8) == 512,
          "Tensor.AddI32 returns packed i32 output");

    ctx.regs[1] = add;
    pv_default_host(&ctx, PV_HOOK_TENSOR_RELUI32, 0, 1, 0, 0);
    int relu = ctx.regs[0];
    CHECK(get_i32be(mem + ctx.span_ptr[relu]) == 512 &&
          get_i32be(mem + ctx.span_ptr[relu] + 4) == 0 &&
          get_i32be(mem + ctx.span_ptr[relu] + 8) == 512,
          "Tensor.ReluI32 clamps negatives");

    pv_default_host(&ctx, PV_HOOK_TENSOR_HASACCEL, 0, 0, 0, 0);
    CHECK(ctx.regs[0] == 0, "Tensor.HasAccel is false without native hook");

    /* Two bitmap rows [1,0,-1,1] and [-1,1,0,1], two vectors. */
    mem[0x1500] = 0x02; mem[0x1501] = 0x04;
    mem[0x1502] = 0x04; mem[0x1503] = 0x01;
    mem[0x1510] = 2; mem[0x1511] = 3; mem[0x1512] = 4; mem[0x1513] = 5;
    mem[0x1514] = 1; mem[0x1515] = 2; mem[0x1516] = 3; mem[0x1517] = 4;
    ctx.regs[1] = 2; ctx.regs[2] = 4;
    pv_default_host(&ctx, PV_HOOK_BITLINEAR_SETSHAPE, 0, 1, 2, 0);
    ctx.regs[1] = add_span(&ctx, 0x1500, 4);
    ctx.regs[2] = add_span(&ctx, 0x1510, 8);
    pv_default_host(&ctx, PV_HOOK_BITLINEAR_MATMULBITMAPBATCH, 0, 1, 2, 0);
    int bitmap_batch = ctx.regs[0];
    CHECK(bitmap_batch > 0 && ctx.span_len[bitmap_batch] == 16,
          "BitLinear.MatMulBitmapBatch returns rows*batch i32");
    CHECK(get_i32be(mem + ctx.span_ptr[bitmap_batch]) == 3 &&
          get_i32be(mem + ctx.span_ptr[bitmap_batch] + 4) == 6 &&
          get_i32be(mem + ctx.span_ptr[bitmap_batch] + 8) == 2 &&
          get_i32be(mem + ctx.span_ptr[bitmap_batch] + 12) == 5,
          "BitLinear.MatMulBitmapBatch values match");

    if (failures) {
        printf("test_tensor: %d failure(s)\n", failures);
        return 1;
    }
    puts("test_tensor: ALL PASS");
    return 0;
}
