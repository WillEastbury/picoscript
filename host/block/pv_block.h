/* pv_block.h — raw block / range I/O primitives (multi-target).
 *
 *   Windows / POSIX  → file-backed mmap (CreateFileMapping / mmap)
 *   PIOS             → WALFS block ops installed by the kernel (LBA R/W)
 *
 * This is *below* Storage.* cards. Card stores and picowal engines should
 * sit on top of pv_block_dev, not invent their own OS I/O.
 *
 * Governing rules:
 *   - One writer ownership for a mounted volume (caller enforces).
 *   - map() returns a host pointer valid until unmap/close (mmap) or
 *     until the next WALFS lease revoke (PIOS).
 *   - All multi-byte integers in this header are host-endian; on-disk
 *     formats define their own endianness.
 */
#ifndef PV_BLOCK_H
#define PV_BLOCK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* open flags */
#define PV_BLOCK_READ     1u
#define PV_BLOCK_WRITE    2u
#define PV_BLOCK_CREATE   4u   /* create file if missing (hosted only) */
#define PV_BLOCK_TRUNCATE 8u   /* resize to 0 on open (hosted only) */
#define PV_BLOCK_MAP      16u  /* prefer mmap for read/write when possible */
#define PV_BLOCK_SYNC     32u  /* O_SYNC / FILE_FLAG_WRITE_THROUGH when available */

/* status */
#define PV_BLOCK_OK            0
#define PV_BLOCK_ERR_IO       -1
#define PV_BLOCK_ERR_INVAL    -2
#define PV_BLOCK_ERR_NOMEM    -3
#define PV_BLOCK_ERR_NOSPACE  -4
#define PV_BLOCK_ERR_UNSUPPORTED -5
#define PV_BLOCK_ERR_NOT_OPEN -6

typedef struct pv_block_dev pv_block_dev;

typedef struct pv_block_ops {
    const char *name; /* "mmap" | "walfs" | "file" */
    int (*open)(pv_block_dev *d, const char *path_or_id, uint32_t flags,
                uint64_t initial_size);
    void (*close)(pv_block_dev *d);
    int (*read)(pv_block_dev *d, uint64_t offset, void *buf, uint32_t len);
    int (*write)(pv_block_dev *d, uint64_t offset, const void *buf, uint32_t len);
    int (*sync)(pv_block_dev *d);
    int (*resize)(pv_block_dev *d, uint64_t new_size);
    uint64_t (*size)(const pv_block_dev *d);
    /* Zero-copy view. writable=0 → read-only mapping. */
    int (*map)(pv_block_dev *d, uint64_t offset, uint32_t len, int writable,
               void **out_ptr);
    int (*unmap)(pv_block_dev *d, void *ptr, uint32_t len);
    /* Block geometry (WALFS typically 512; mmap files often 4096 logical). */
    uint32_t (*block_size)(const pv_block_dev *d);
    /* Optional: LBA R/W for true block devices (WALFS). Offset = lba * block_size. */
    int (*read_blocks)(pv_block_dev *d, uint64_t lba, void *buf, uint32_t nblocks);
    int (*write_blocks)(pv_block_dev *d, uint64_t lba, const void *buf, uint32_t nblocks);
} pv_block_ops;

struct pv_block_dev {
    const pv_block_ops *ops;
    void *os;              /* backend private */
    uint32_t flags;
    uint32_t block_size;   /* bytes; 0 until open */
    uint64_t size_bytes;   /* 0 until open */
    int open;
};

/* ---- factory ------------------------------------------------------------ */

/* Hosted default: Windows or POSIX mmap-backed file. */
const pv_block_ops *pv_block_ops_mmap(void);

/* Buffered fread/fwrite fallback (no mmap). */
const pv_block_ops *pv_block_ops_file(void);

/* PIOS WALFS adapter (uses installed pv_walfs_backend). */
const pv_block_ops *pv_block_ops_walfs(void);

/* RP2350 adapter: caller owns callbacks and scratch; the backend never
 * allocates, including for unaligned flash/SD range I/O. */
typedef struct pv_rp2350_backend {
    void *ctx;
    uint32_t block_size;
    uint64_t block_count;
    int (*read_blocks)(void *ctx, uint64_t lba, void *buf, uint32_t nblocks);
    int (*write_blocks)(void *ctx, uint64_t lba, const void *buf, uint32_t nblocks);
    int (*sync)(void *ctx);
    uint8_t *scratch;
    uint32_t scratch_size;
} pv_rp2350_backend;
void pv_rp2350_install(const pv_rp2350_backend *backend);
const pv_rp2350_backend *pv_rp2350_current(void);
const pv_block_ops *pv_block_ops_rp2350(void);

/* Pick mmap on Win/POSIX, walfs when PV_BLOCK_USE_WALFS is defined / installed. */
const pv_block_ops *pv_block_ops_default(void);

/* Convenience */
int pv_block_open(pv_block_dev *d, const pv_block_ops *ops,
                  const char *path_or_id, uint32_t flags, uint64_t initial_size);
void pv_block_close(pv_block_dev *d);
int pv_block_read(pv_block_dev *d, uint64_t offset, void *buf, uint32_t len);
int pv_block_write(pv_block_dev *d, uint64_t offset, const void *buf, uint32_t len);
int pv_block_sync(pv_block_dev *d);
int pv_block_resize(pv_block_dev *d, uint64_t new_size);
uint64_t pv_block_size(const pv_block_dev *d);
uint32_t pv_block_block_size(const pv_block_dev *d);
int pv_block_map(pv_block_dev *d, uint64_t offset, uint32_t len, int writable,
                 void **out_ptr);
int pv_block_unmap(pv_block_dev *d, void *ptr, uint32_t len);
int pv_block_read_blocks(pv_block_dev *d, uint64_t lba, void *buf, uint32_t nblocks);
int pv_block_write_blocks(pv_block_dev *d, uint64_t lba, const void *buf, uint32_t nblocks);

/* ---- WALFS backend (installed by PIOS kernel or a host simulator) ------- */

typedef struct pv_walfs_backend {
    void *ctx;
    uint32_t block_size; /* typically 512 */
    /* LBA interface */
    int (*read_blocks)(void *ctx, uint64_t lba, void *buf, uint32_t nblocks);
    int (*write_blocks)(void *ctx, uint64_t lba, const void *buf, uint32_t nblocks);
    int (*sync)(void *ctx);
    uint64_t (*block_count)(void *ctx);
    /* Optional range map (kernel may return a leased pointer). */
    int (*map_range)(void *ctx, uint64_t offset, uint32_t len, int writable,
                     void **out_ptr);
    int (*unmap_range)(void *ctx, void *ptr, uint32_t len);
    uint8_t *scratch;       /* caller-owned, needed for unaligned range I/O */
    uint32_t scratch_size;
} pv_walfs_backend;

/* Install / clear the global WALFS backend used by pv_block_ops_walfs(). */
void pv_walfs_install(const pv_walfs_backend *backend);
const pv_walfs_backend *pv_walfs_current(void);

/* In-process WALFS simulator for host tests: file of N fixed blocks. */
int pv_walfs_sim_open(const char *path, uint32_t block_size, uint64_t nblocks,
                      uint32_t flags);
void pv_walfs_sim_close(void);

/* ---- picowal portable engine bridge ------------------------------------ */

/* Fill a pw_db_io_t (picowal/portable) from an open block device.
 * The io.context points at the pv_block_dev; lifetime must outlive the db. */
struct pw_db_io; /* forward if header not included */
typedef struct {
    void *context;
    int (*read_at)(void *context, uint64_t offset, void *out, uint32_t len);
    int (*write_at)(void *context, uint64_t offset, const void *data, uint32_t len);
    int (*sync)(void *context);
    int (*resize)(void *context, uint64_t size);
    uint64_t (*size)(void *context);
} pv_block_io_bridge;

void pv_block_io_bridge_init(pv_block_dev *d, pv_block_io_bridge *out);

#ifdef __cplusplus
}
#endif

#endif /* PV_BLOCK_H */
