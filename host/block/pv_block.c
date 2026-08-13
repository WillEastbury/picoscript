/* Shared convenience wrappers + default ops selection. */
#include "pv_block.h"

#include <string.h>

int pv_block_open(pv_block_dev *d, const pv_block_ops *ops,
                  const char *path_or_id, uint32_t flags, uint64_t initial_size)
{
    if (!d || !ops || !ops->open) return PV_BLOCK_ERR_INVAL;
    memset(d, 0, sizeof(*d));
    d->ops = ops;
    d->flags = flags;
    return ops->open(d, path_or_id, flags, initial_size);
}

void pv_block_close(pv_block_dev *d)
{
    if (!d || !d->ops || !d->open) return;
    if (d->ops->close) d->ops->close(d);
    d->open = 0;
    d->os = 0;
}

int pv_block_read(pv_block_dev *d, uint64_t offset, void *buf, uint32_t len)
{
    if (!d || !d->open || !d->ops || !d->ops->read) return PV_BLOCK_ERR_NOT_OPEN;
    return d->ops->read(d, offset, buf, len);
}

int pv_block_write(pv_block_dev *d, uint64_t offset, const void *buf, uint32_t len)
{
    if (!d || !d->open || !d->ops || !d->ops->write) return PV_BLOCK_ERR_NOT_OPEN;
    return d->ops->write(d, offset, buf, len);
}

int pv_block_sync(pv_block_dev *d)
{
    if (!d || !d->open || !d->ops) return PV_BLOCK_ERR_NOT_OPEN;
    if (!d->ops->sync) return PV_BLOCK_OK;
    return d->ops->sync(d);
}

int pv_block_resize(pv_block_dev *d, uint64_t new_size)
{
    if (!d || !d->open || !d->ops || !d->ops->resize) return PV_BLOCK_ERR_UNSUPPORTED;
    return d->ops->resize(d, new_size);
}

uint64_t pv_block_size(const pv_block_dev *d)
{
    if (!d || !d->open) return 0;
    if (d->ops && d->ops->size) return d->ops->size(d);
    return d->size_bytes;
}

uint32_t pv_block_block_size(const pv_block_dev *d)
{
    if (!d || !d->open) return 0;
    if (d->ops && d->ops->block_size) return d->ops->block_size(d);
    return d->block_size ? d->block_size : 4096u;
}

int pv_block_map(pv_block_dev *d, uint64_t offset, uint32_t len, int writable,
                 void **out_ptr)
{
    if (!d || !d->open || !d->ops) return PV_BLOCK_ERR_NOT_OPEN;
    if (!d->ops->map) return PV_BLOCK_ERR_UNSUPPORTED;
    return d->ops->map(d, offset, len, writable, out_ptr);
}

int pv_block_unmap(pv_block_dev *d, void *ptr, uint32_t len)
{
    if (!d || !d->open || !d->ops) return PV_BLOCK_ERR_NOT_OPEN;
    if (!d->ops->unmap) return PV_BLOCK_ERR_UNSUPPORTED;
    return d->ops->unmap(d, ptr, len);
}

int pv_block_read_blocks(pv_block_dev *d, uint64_t lba, void *buf, uint32_t nblocks)
{
    uint32_t bs;
    if (!d || !d->open) return PV_BLOCK_ERR_NOT_OPEN;
    if (d->ops && d->ops->read_blocks)
        return d->ops->read_blocks(d, lba, buf, nblocks);
    bs = pv_block_block_size(d);
    return pv_block_read(d, lba * (uint64_t)bs, buf, nblocks * bs);
}

int pv_block_write_blocks(pv_block_dev *d, uint64_t lba, const void *buf,
                          uint32_t nblocks)
{
    uint32_t bs;
    if (!d || !d->open) return PV_BLOCK_ERR_NOT_OPEN;
    if (d->ops && d->ops->write_blocks)
        return d->ops->write_blocks(d, lba, buf, nblocks);
    bs = pv_block_block_size(d);
    return pv_block_write(d, lba * (uint64_t)bs, buf, nblocks * bs);
}

const pv_block_ops *pv_block_ops_default(void)
{
#if defined(PV_BLOCK_USE_RP2350)
    if (pv_rp2350_current()) return pv_block_ops_rp2350();
#elif defined(PV_BLOCK_USE_WALFS)
    if (pv_walfs_current()) return pv_block_ops_walfs();
#endif
    return pv_block_ops_mmap();
}

/* Bridge for picowal-style engines (bool-like int returns). */
static int bridge_read(void *ctx, uint64_t offset, void *out, uint32_t len)
{
    return pv_block_read((pv_block_dev *)ctx, offset, out, len) == PV_BLOCK_OK ? 1 : 0;
}
static int bridge_write(void *ctx, uint64_t offset, const void *data, uint32_t len)
{
    return pv_block_write((pv_block_dev *)ctx, offset, data, len) == PV_BLOCK_OK ? 1 : 0;
}
static int bridge_sync(void *ctx)
{
    return pv_block_sync((pv_block_dev *)ctx) == PV_BLOCK_OK ? 1 : 0;
}
static int bridge_resize(void *ctx, uint64_t size)
{
    return pv_block_resize((pv_block_dev *)ctx, size) == PV_BLOCK_OK ? 1 : 0;
}
static uint64_t bridge_size(void *ctx)
{
    return pv_block_size((const pv_block_dev *)ctx);
}

void pv_block_io_bridge_init(pv_block_dev *d, pv_block_io_bridge *out)
{
    if (!out) return;
    out->context = d;
    out->read_at = bridge_read;
    out->write_at = bridge_write;
    out->sync = bridge_sync;
    out->resize = bridge_resize;
    out->size = bridge_size;
}
