/* WALFS block backend — PIOS kernel installs real LBA ops; host tests use sim. */
#include "pv_block.h"

#include <stdio.h>
#include <string.h>

static const pv_walfs_backend *g_walfs;

void pv_walfs_install(const pv_walfs_backend *backend) { g_walfs = backend; }
const pv_walfs_backend *pv_walfs_current(void) { return g_walfs; }

/* ---- device ops over installed backend -------------------------------- */

static int walfs_open(pv_block_dev *d, const char *path_or_id, uint32_t flags,
                      uint64_t initial_size)
{
    const pv_walfs_backend *be = g_walfs;
    (void)path_or_id;
    (void)initial_size;
    if (!be || !be->read_blocks || !be->block_count) return PV_BLOCK_ERR_UNSUPPORTED;
    if ((flags & PV_BLOCK_WRITE) && !be->write_blocks) return PV_BLOCK_ERR_UNSUPPORTED;
    d->os = (void *)be;
    d->block_size = be->block_size ? be->block_size : 512u;
    d->size_bytes = be->block_count(be->ctx) * (uint64_t)d->block_size;
    d->flags = flags;
    d->open = 1;
    return PV_BLOCK_OK;
}

static void walfs_close(pv_block_dev *d)
{
    d->os = NULL;
}

static int walfs_read_blocks(pv_block_dev *d, uint64_t lba, void *buf, uint32_t nblocks)
{
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    if (!be || !be->read_blocks) return PV_BLOCK_ERR_UNSUPPORTED;
    return be->read_blocks(be->ctx, lba, buf, nblocks) == 0 ? PV_BLOCK_OK
                                                            : PV_BLOCK_ERR_IO;
}

static int walfs_write_blocks(pv_block_dev *d, uint64_t lba, const void *buf,
                              uint32_t nblocks)
{
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    if (!be || !be->write_blocks) return PV_BLOCK_ERR_UNSUPPORTED;
    return be->write_blocks(be->ctx, lba, buf, nblocks) == 0 ? PV_BLOCK_OK
                                                             : PV_BLOCK_ERR_IO;
}

static int walfs_read(pv_block_dev *d, uint64_t offset, void *buf, uint32_t len)
{
    uint32_t bs = d->block_size ? d->block_size : 512u;
    uint64_t lba = offset / bs;
    uint32_t off = (uint32_t)(offset % bs);
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    uint8_t *tmp;
    uint32_t take;
    if (offset > d->size_bytes || len > d->size_bytes - offset) return PV_BLOCK_ERR_INVAL;
    if (off == 0 && (len % bs) == 0)
        return walfs_read_blocks(d, lba, buf, len / bs);
    if (!be || !be->scratch || be->scratch_size < bs) return PV_BLOCK_ERR_NOMEM;
    tmp = be->scratch;
    while (len) {
        take = bs - off; if (take > len) take = len;
        if (walfs_read_blocks(d, lba, tmp, 1) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
        memcpy(buf, tmp + off, take);
        buf = (uint8_t *)buf + take; len -= take; lba++; off = 0;
    }
    return PV_BLOCK_OK;
}

static int walfs_write(pv_block_dev *d, uint64_t offset, const void *buf, uint32_t len)
{
    uint32_t bs = d->block_size ? d->block_size : 512u;
    uint64_t lba = offset / bs;
    uint32_t off = (uint32_t)(offset % bs);
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    uint8_t *tmp;
    uint32_t take;
    if (!(d->flags & PV_BLOCK_WRITE)) return PV_BLOCK_ERR_INVAL;
    if (offset > d->size_bytes || len > d->size_bytes - offset) return PV_BLOCK_ERR_NOSPACE;
    if (off == 0 && (len % bs) == 0)
        return walfs_write_blocks(d, lba, buf, len / bs);
    if (!be || !be->scratch || be->scratch_size < bs) return PV_BLOCK_ERR_NOMEM;
    tmp = be->scratch;
    while (len) {
        take = bs - off; if (take > len) take = len;
        if (walfs_read_blocks(d, lba, tmp, 1) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
        memcpy(tmp + off, buf, take);
        if (walfs_write_blocks(d, lba, tmp, 1) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
        buf = (const uint8_t *)buf + take; len -= take; lba++; off = 0;
    }
    return PV_BLOCK_OK;
}

static int walfs_sync(pv_block_dev *d)
{
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    if (!be || !be->sync) return PV_BLOCK_OK;
    return be->sync(be->ctx) == 0 ? PV_BLOCK_OK : PV_BLOCK_ERR_IO;
}

static int walfs_resize(pv_block_dev *d, uint64_t new_size)
{
    (void)d;
    (void)new_size;
    return PV_BLOCK_ERR_UNSUPPORTED; /* WALFS geometry fixed by kernel */
}

static uint64_t walfs_size(const pv_block_dev *d) { return d->size_bytes; }
static uint32_t walfs_bsize(const pv_block_dev *d)
{
    return d->block_size ? d->block_size : 512u;
}

static int walfs_map(pv_block_dev *d, uint64_t offset, uint32_t len, int writable,
                     void **out_ptr)
{
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    if (!be || !be->map_range) return PV_BLOCK_ERR_UNSUPPORTED;
    return be->map_range(be->ctx, offset, len, writable, out_ptr) == 0
               ? PV_BLOCK_OK
               : PV_BLOCK_ERR_IO;
}

static int walfs_unmap(pv_block_dev *d, void *ptr, uint32_t len)
{
    const pv_walfs_backend *be = (const pv_walfs_backend *)d->os;
    if (!be || !be->unmap_range) return PV_BLOCK_OK;
    return be->unmap_range(be->ctx, ptr, len) == 0 ? PV_BLOCK_OK
                                                           : PV_BLOCK_ERR_IO;
}

static const pv_block_ops g_walfs_ops = {
    "walfs",
    walfs_open,
    walfs_close,
    walfs_read,
    walfs_write,
    walfs_sync,
    walfs_resize,
    walfs_size,
    walfs_map,
    walfs_unmap,
    walfs_bsize,
    walfs_read_blocks,
    walfs_write_blocks,
};

const pv_block_ops *pv_block_ops_walfs(void) { return &g_walfs_ops; }

/* ---- host-side WALFS simulator (file of fixed blocks) ----------------- */

typedef struct {
    FILE *f;
    uint32_t block_size;
    uint64_t nblocks;
    uint8_t *cache; /* optional whole-image cache for map */
    int writable;
} walfs_sim;

static walfs_sim g_sim;
static pv_walfs_backend g_sim_be;
static uint8_t g_sim_scratch[4096];

static int sim_read(void *ctx, uint64_t lba, void *buf, uint32_t nblocks)
{
    walfs_sim *s = (walfs_sim *)ctx;
    uint64_t off;
    if (!s || !s->f || lba + nblocks > s->nblocks) return -1;
    off = lba * (uint64_t)s->block_size;
    if (fseek(s->f, (long)off, SEEK_SET) != 0) return -1;
    if (fread(buf, s->block_size, nblocks, s->f) != nblocks) return -1;
    return 0;
}

static int sim_write(void *ctx, uint64_t lba, const void *buf, uint32_t nblocks)
{
    walfs_sim *s = (walfs_sim *)ctx;
    uint64_t off;
    if (!s || !s->f || !s->writable || lba + nblocks > s->nblocks) return -1;
    off = lba * (uint64_t)s->block_size;
    if (fseek(s->f, (long)off, SEEK_SET) != 0) return -1;
    if (fwrite(buf, s->block_size, nblocks, s->f) != nblocks) return -1;
    return 0;
}

static int sim_sync(void *ctx)
{
    walfs_sim *s = (walfs_sim *)ctx;
    if (!s || !s->f) return -1;
    return fflush(s->f) == 0 ? 0 : -1;
}

static uint64_t sim_count(void *ctx)
{
    walfs_sim *s = (walfs_sim *)ctx;
    return s ? s->nblocks : 0;
}

int pv_walfs_sim_open(const char *path, uint32_t block_size, uint64_t nblocks,
                      uint32_t flags)
{
    uint64_t bytes;
    if (!path || block_size == 0 || nblocks == 0) return PV_BLOCK_ERR_INVAL;
    pv_walfs_sim_close();
    g_sim.block_size = block_size;
    g_sim.nblocks = nblocks;
    g_sim.writable = (flags & PV_BLOCK_WRITE) ? 1 : 0;
    bytes = nblocks * (uint64_t)block_size;
    g_sim.f = fopen(path, (flags & PV_BLOCK_CREATE) || (flags & PV_BLOCK_TRUNCATE)
                              ? "w+b"
                              : (g_sim.writable ? "r+b" : "rb"));
    if (!g_sim.f && (flags & PV_BLOCK_CREATE))
        g_sim.f = fopen(path, "w+b");
    if (!g_sim.f) return PV_BLOCK_ERR_IO;
    if ((flags & PV_BLOCK_CREATE) || (flags & PV_BLOCK_TRUNCATE)) {
        if (fseek(g_sim.f, (long)(bytes - 1), SEEK_SET) != 0) return PV_BLOCK_ERR_IO;
        if (fputc(0, g_sim.f) == EOF) return PV_BLOCK_ERR_IO;
        fflush(g_sim.f);
    }
    g_sim_be.ctx = &g_sim;
    g_sim_be.block_size = block_size;
    g_sim_be.read_blocks = sim_read;
    g_sim_be.write_blocks = g_sim.writable ? sim_write : NULL;
    g_sim_be.sync = sim_sync;
    g_sim_be.block_count = sim_count;
    g_sim_be.map_range = NULL;
    g_sim_be.unmap_range = NULL;
    g_sim_be.scratch = g_sim_scratch;
    g_sim_be.scratch_size = sizeof(g_sim_scratch);
    pv_walfs_install(&g_sim_be);
    return PV_BLOCK_OK;
}

void pv_walfs_sim_close(void)
{
    if (g_sim.f) {
        fclose(g_sim.f);
        g_sim.f = NULL;
    }
    if (g_walfs == &g_sim_be) pv_walfs_install(NULL);
    memset(&g_sim, 0, sizeof(g_sim));
}

/* File ops stub name for API completeness — use mmap. */
const pv_block_ops *pv_block_ops_file(void) { return pv_block_ops_mmap(); }
