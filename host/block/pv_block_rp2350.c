/* RP2350 flash/SD adapter. Pico SDK code supplies callbacks; this layer stays
 * SDK-independent so it can be compiled and unit-tested on hosted systems. */
#include "pv_block.h"

#include <string.h>

static const pv_rp2350_backend *g_rp;

void pv_rp2350_install(const pv_rp2350_backend *backend) { g_rp = backend; }
const pv_rp2350_backend *pv_rp2350_current(void) { return g_rp; }

static int rp_open(pv_block_dev *d, const char *id, uint32_t flags, uint64_t initial)
{
    (void)id; (void)initial;
    if (!g_rp || !g_rp->read_blocks || !g_rp->block_size || !g_rp->block_count)
        return PV_BLOCK_ERR_UNSUPPORTED;
    if ((flags & PV_BLOCK_WRITE) && !g_rp->write_blocks) return PV_BLOCK_ERR_UNSUPPORTED;
    d->os = (void *)g_rp;
    d->flags = flags;
    d->block_size = g_rp->block_size;
    d->size_bytes = g_rp->block_count * (uint64_t)g_rp->block_size;
    d->open = 1;
    return PV_BLOCK_OK;
}
static void rp_close(pv_block_dev *d) { d->os = NULL; }
static const pv_rp2350_backend *rp_be(const pv_block_dev *d)
{ return (const pv_rp2350_backend *)d->os; }
static int rp_read_blocks(pv_block_dev *d, uint64_t lba, void *buf, uint32_t n)
{
    const pv_rp2350_backend *b = rp_be(d);
    if (!b || lba > b->block_count || n > b->block_count - lba) return PV_BLOCK_ERR_INVAL;
    return b->read_blocks(b->ctx, lba, buf, n) == 0 ? PV_BLOCK_OK : PV_BLOCK_ERR_IO;
}
static int rp_write_blocks(pv_block_dev *d, uint64_t lba, const void *buf, uint32_t n)
{
    const pv_rp2350_backend *b = rp_be(d);
    if (!b || !b->write_blocks) return PV_BLOCK_ERR_UNSUPPORTED;
    if (lba > b->block_count || n > b->block_count - lba) return PV_BLOCK_ERR_NOSPACE;
    return b->write_blocks(b->ctx, lba, buf, n) == 0 ? PV_BLOCK_OK : PV_BLOCK_ERR_IO;
}
static int rp_range(pv_block_dev *d, uint64_t off, void *buf, uint32_t len, int write)
{
    const pv_rp2350_backend *b = rp_be(d);
    uint8_t *p = (uint8_t *)buf;
    uint32_t bs, at, take;
    uint64_t lba;
    int rc;
    if (!b || off > d->size_bytes || len > d->size_bytes - off)
        return write ? PV_BLOCK_ERR_NOSPACE : PV_BLOCK_ERR_INVAL;
    bs = b->block_size;
    if ((off % bs) == 0 && (len % bs) == 0)
        return write ? rp_write_blocks(d, off / bs, p, len / bs)
                     : rp_read_blocks(d, off / bs, p, len / bs);
    if (!b->scratch || b->scratch_size < bs) return PV_BLOCK_ERR_NOMEM;
    while (len) {
        lba = off / bs; at = (uint32_t)(off % bs); take = bs - at;
        if (take > len) take = len;
        rc = rp_read_blocks(d, lba, b->scratch, 1); if (rc) return rc;
        if (write) {
            memcpy(b->scratch + at, p, take);
            rc = rp_write_blocks(d, lba, b->scratch, 1); if (rc) return rc;
        } else memcpy(p, b->scratch + at, take);
        off += take; p += take; len -= take;
    }
    return PV_BLOCK_OK;
}
static int rp_read(pv_block_dev *d, uint64_t o, void *p, uint32_t n) { return rp_range(d,o,p,n,0); }
static int rp_write(pv_block_dev *d, uint64_t o, const void *p, uint32_t n) { return rp_range(d,o,(void *)p,n,1); }
static int rp_sync(pv_block_dev *d) { const pv_rp2350_backend *b=rp_be(d); return (!b||!b->sync||b->sync(b->ctx)==0)?PV_BLOCK_OK:PV_BLOCK_ERR_IO; }
static int rp_resize(pv_block_dev *d, uint64_t n) { (void)d; (void)n; return PV_BLOCK_ERR_UNSUPPORTED; }
static uint64_t rp_size(const pv_block_dev *d) { return d->size_bytes; }
static int rp_map(pv_block_dev *d,uint64_t o,uint32_t n,int w,void **p){(void)d;(void)o;(void)n;(void)w;(void)p;return PV_BLOCK_ERR_UNSUPPORTED;}
static int rp_unmap(pv_block_dev *d,void *p,uint32_t n){(void)d;(void)p;(void)n;return PV_BLOCK_ERR_UNSUPPORTED;}
static uint32_t rp_bsize(const pv_block_dev *d) { return d->block_size; }
static const pv_block_ops g_rp_ops={"rp2350",rp_open,rp_close,rp_read,rp_write,rp_sync,rp_resize,rp_size,rp_map,rp_unmap,rp_bsize,rp_read_blocks,rp_write_blocks};
const pv_block_ops *pv_block_ops_rp2350(void) { return &g_rp_ops; }
