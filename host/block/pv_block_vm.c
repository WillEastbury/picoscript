#include "pv_block_vm.h"

#include <string.h>

static uint64_t pair32(int32_t lo, int32_t hi)
{
    return (uint64_t)(uint32_t)lo | ((uint64_t)(uint32_t)hi << 32);
}

static int finish_status(pv_ctx *ctx, int rd, int status)
{
    ctx->block_status = status;
    ctx->host_status = status == PV_BLOCK_OK ? 0 : 1;
    ctx->regs[rd] = status;
    return 1;
}

static int reserve_span(pv_ctx *ctx, uint32_t len, uint8_t **out)
{
    uint32_t ptr;
    int h;
    if (!ctx || ctx->no_alloc || !ctx->mem || ctx->span_count >= PV_MAX_SPANS ||
        (uint64_t)ctx->arena_top + len > (uint64_t)ctx->mem_size) return 0;
    ptr = ctx->arena_top;
    if (out) *out = ctx->mem + ptr;
    h = ctx->span_count++;
    ctx->span_ptr[h] = ptr;
    ctx->span_len[h] = (int32_t)len;
    ctx->arena_top += len;
    return h;
}

void pv_block_vm_install(void) { pv_block_hook = pv_block_vm_dispatch; }

void pv_block_vm_bind(pv_ctx *ctx, pv_block_dev *device)
{
    if (!ctx) return;
    ctx->block_device = device;
    ctx->block_offset = 0;
    ctx->block_lba = 0;
    ctx->block_status = device && device->open ? PV_BLOCK_OK : PV_BLOCK_ERR_NOT_OPEN;
}

int pv_block_vm_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    pv_block_dev *d;
    uint8_t *transfer;
    uint32_t count, bytes, bs;
    int status, h;
    if (!ctx || hook < PV_HOOK_BLOCK_READY || hook > PV_HOOK_BLOCK_STATUS) return 0;
    d = (pv_block_dev *)ctx->block_device;
    if (hook == PV_HOOK_BLOCK_READY) { ctx->regs[rd] = d && d->open; return 1; }
    if (hook == PV_HOOK_BLOCK_STATUS) { ctx->regs[rd] = ctx->block_status; return 1; }
    if (!d || !d->open) return finish_status(ctx, rd, PV_BLOCK_ERR_NOT_OPEN);
    switch (hook) {
    case PV_HOOK_BLOCK_BLOCKSIZE: ctx->regs[rd] = (int32_t)pv_block_block_size(d); return 1;
    case PV_HOOK_BLOCK_SIZELOW: ctx->regs[rd] = (int32_t)(uint32_t)pv_block_size(d); return 1;
    case PV_HOOK_BLOCK_SIZEHIGH: ctx->regs[rd] = (int32_t)(uint32_t)(pv_block_size(d) >> 32); return 1;
    case PV_HOOK_BLOCK_SETOFFSET: ctx->block_offset = pair32(ctx->regs[rs1], ctx->regs[rs2]); ctx->block_status = 0; return 1;
    case PV_HOOK_BLOCK_SETLBA: ctx->block_lba = pair32(ctx->regs[rs1], ctx->regs[rs2]); ctx->block_status = 0; return 1;
    case PV_HOOK_BLOCK_READ:
        count = (uint32_t)ctx->regs[rs1];
        if (count > PV_BLOCK_VM_MAX_TRANSFER) return finish_status(ctx, rd, PV_BLOCK_ERR_INVAL);
        h = reserve_span(ctx, count, &transfer);
        if (!h) return finish_status(ctx, rd, PV_BLOCK_ERR_NOMEM);
        status = pv_block_read(d, ctx->block_offset, transfer, count);
        if (status == PV_BLOCK_OK) ctx->regs[rd] = h;
        else { ctx->span_count--; ctx->arena_top -= count; ctx->regs[rd] = 0; }
        ctx->block_status = status; ctx->host_status = status ? 1 : 0; return 1;
    case PV_HOOK_BLOCK_WRITE:
        h = ctx->regs[rs1];
        if (h <= 0 || h >= ctx->span_count || ctx->span_len[h] < 0 || !ctx->mem ||
            (uint32_t)ctx->span_len[h] > PV_BLOCK_VM_MAX_TRANSFER ||
            (uint64_t)ctx->span_ptr[h] + (uint32_t)ctx->span_len[h] > (uint64_t)ctx->mem_size)
            return finish_status(ctx, rd, PV_BLOCK_ERR_INVAL);
        bytes = (uint32_t)ctx->span_len[h];
        return finish_status(ctx, rd, pv_block_write(d, ctx->block_offset,
            ctx->mem + ctx->span_ptr[h], bytes));
    case PV_HOOK_BLOCK_SYNC: return finish_status(ctx, rd, pv_block_sync(d));
    case PV_HOOK_BLOCK_RESIZE: return finish_status(ctx, rd,
        pv_block_resize(d, pair32(ctx->regs[rs1], ctx->regs[rs2])));
    case PV_HOOK_BLOCK_READBLOCKS:
        count = (uint32_t)ctx->regs[rs1]; bs = pv_block_block_size(d);
        if (bs == 0 || count > PV_BLOCK_VM_MAX_TRANSFER / bs)
            return finish_status(ctx, rd, PV_BLOCK_ERR_INVAL);
        bytes = count * bs; h = reserve_span(ctx, bytes, &transfer);
        if (!h) return finish_status(ctx, rd, PV_BLOCK_ERR_NOMEM);
        status = pv_block_read_blocks(d, ctx->block_lba, transfer, count);
        if (status == PV_BLOCK_OK) ctx->regs[rd] = h;
        else { ctx->span_count--; ctx->arena_top -= bytes; ctx->regs[rd] = 0; }
        ctx->block_status = status; ctx->host_status = status ? 1 : 0; return 1;
    case PV_HOOK_BLOCK_WRITEBLOCKS:
        h = ctx->regs[rs1]; bs = pv_block_block_size(d);
        if (h <= 0 || h >= ctx->span_count || ctx->span_len[h] < 0 || !ctx->mem || bs == 0 ||
            (uint32_t)ctx->span_len[h] > PV_BLOCK_VM_MAX_TRANSFER || ((uint32_t)ctx->span_len[h] % bs))
            return finish_status(ctx, rd, PV_BLOCK_ERR_INVAL);
        bytes = (uint32_t)ctx->span_len[h];
        if ((uint64_t)ctx->span_ptr[h] + bytes > (uint64_t)ctx->mem_size)
            return finish_status(ctx, rd, PV_BLOCK_ERR_INVAL);
        return finish_status(ctx, rd, pv_block_write_blocks(d, ctx->block_lba,
            ctx->mem + ctx->span_ptr[h], bytes / bs));
    default: return 0;
    }
}
