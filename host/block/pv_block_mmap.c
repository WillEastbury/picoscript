/* File-backed block device — Windows + POSIX.
 * Primary path: pread/pwrite (ReadFile/WriteFile).
 * Zero-copy: full-file mmap when PV_BLOCK_MAP is set and map() is used.
 */
#include "pv_block.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

typedef struct {
#ifdef _WIN32
    HANDLE file;
    HANDLE map;
#else
    int fd;
#endif
    void *view;
    uint64_t map_size;
    int writable;
    int want_map;
} mmap_state;

static uint64_t align_up(uint64_t v, uint64_t a)
{
    if (a == 0) a = 4096;
    return (v + a - 1) & ~(a - 1);
}

#ifdef _WIN32

static int os_size(mmap_state *st, uint64_t *out)
{
    LARGE_INTEGER sz;
    if (!GetFileSizeEx(st->file, &sz)) return PV_BLOCK_ERR_IO;
    *out = (uint64_t)sz.QuadPart;
    return PV_BLOCK_OK;
}

static int os_set_size(mmap_state *st, uint64_t new_size)
{
    LARGE_INTEGER li;
    li.QuadPart = (LONGLONG)new_size;
    if (!SetFilePointerEx(st->file, li, NULL, FILE_BEGIN) || !SetEndOfFile(st->file))
        return PV_BLOCK_ERR_IO;
    FlushFileBuffers(st->file);
    return PV_BLOCK_OK;
}

static void os_drop_map(mmap_state *st)
{
    if (st->view) {
        UnmapViewOfFile(st->view);
        st->view = NULL;
    }
    if (st->map) {
        CloseHandle(st->map);
        st->map = NULL;
    }
    st->map_size = 0;
}

static int os_build_map(mmap_state *st, uint64_t size)
{
    DWORD protect = st->writable ? PAGE_READWRITE : PAGE_READONLY;
    DWORD access = st->writable ? (FILE_MAP_WRITE | FILE_MAP_READ) : FILE_MAP_READ;
    os_drop_map(st);
    if (size == 0) return PV_BLOCK_OK;
    st->map = CreateFileMappingA(st->file, NULL, protect, 0, 0, NULL);
    if (!st->map) return PV_BLOCK_ERR_IO;
    st->view = MapViewOfFile(st->map, access, 0, 0, 0);
    if (!st->view) {
        CloseHandle(st->map);
        st->map = NULL;
        return PV_BLOCK_ERR_IO;
    }
    st->map_size = size;
    return PV_BLOCK_OK;
}

static int os_read(mmap_state *st, uint64_t offset, void *buf, uint32_t len)
{
    LARGE_INTEGER li;
    DWORD got = 0;
    li.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(st->file, li, NULL, FILE_BEGIN)) return PV_BLOCK_ERR_IO;
    if (!ReadFile(st->file, buf, len, &got, NULL) || got != len) return PV_BLOCK_ERR_IO;
    return PV_BLOCK_OK;
}

static int os_write(mmap_state *st, uint64_t offset, const void *buf, uint32_t len)
{
    LARGE_INTEGER li;
    DWORD put = 0;
    li.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(st->file, li, NULL, FILE_BEGIN)) return PV_BLOCK_ERR_IO;
    if (!WriteFile(st->file, buf, len, &put, NULL) || put != len) return PV_BLOCK_ERR_IO;
    return PV_BLOCK_OK;
}

static int mmap_open(pv_block_dev *d, const char *path, uint32_t flags,
                     uint64_t initial_size)
{
    mmap_state *st;
    DWORD access = 0, share = FILE_SHARE_READ | FILE_SHARE_WRITE;
    DWORD create = OPEN_EXISTING, fflags = FILE_ATTRIBUTE_NORMAL;
    if (!path) return PV_BLOCK_ERR_INVAL;
    st = (mmap_state *)calloc(1, sizeof(*st));
    if (!st) return PV_BLOCK_ERR_NOMEM;
    if (flags & PV_BLOCK_WRITE) {
        access = GENERIC_READ | GENERIC_WRITE;
        st->writable = 1;
    } else {
        access = GENERIC_READ;
    }
    if (flags & PV_BLOCK_CREATE) create = OPEN_ALWAYS;
    if (flags & PV_BLOCK_TRUNCATE) create = CREATE_ALWAYS;
    if (flags & PV_BLOCK_SYNC) fflags |= FILE_FLAG_WRITE_THROUGH;
    st->file = CreateFileA(path, access, share, NULL, create, fflags, NULL);
    if (st->file == INVALID_HANDLE_VALUE) {
        free(st);
        return PV_BLOCK_ERR_IO;
    }
    st->want_map = (flags & PV_BLOCK_MAP) ? 1 : 0;
    d->os = st;
    d->block_size = 4096;
    d->flags = flags;
    d->open = 1;
    if (st->writable && initial_size > 0) {
        uint64_t cur = 0;
        if (os_size(st, &cur) != PV_BLOCK_OK || cur < initial_size) {
            if (os_set_size(st, initial_size) != PV_BLOCK_OK) {
                pv_block_close(d);
                return PV_BLOCK_ERR_IO;
            }
        }
    }
    if (os_size(st, &d->size_bytes) != PV_BLOCK_OK) {
        pv_block_close(d);
        return PV_BLOCK_ERR_IO;
    }
    if (st->want_map && d->size_bytes > 0) {
        /* Best-effort full map; read/write still work if this fails. */
        (void)os_build_map(st, d->size_bytes);
    }
    return PV_BLOCK_OK;
}

static void mmap_close(pv_block_dev *d)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st) return;
    os_drop_map(st);
    if (st->file && st->file != INVALID_HANDLE_VALUE) CloseHandle(st->file);
    free(st);
    d->os = NULL;
}

static int mmap_sync(pv_block_dev *d)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st) return PV_BLOCK_ERR_NOT_OPEN;
    if (st->view && !FlushViewOfFile(st->view, 0)) return PV_BLOCK_ERR_IO;
    if (!FlushFileBuffers(st->file)) return PV_BLOCK_ERR_IO;
    return PV_BLOCK_OK;
}

#else /* POSIX ----------------------------------------------------------- */

static int os_size(mmap_state *st, uint64_t *out)
{
    struct stat stbuf;
    if (fstat(st->fd, &stbuf) != 0) return PV_BLOCK_ERR_IO;
    *out = (uint64_t)stbuf.st_size;
    return PV_BLOCK_OK;
}

static int os_set_size(mmap_state *st, uint64_t new_size)
{
    if (ftruncate(st->fd, (off_t)new_size) != 0) return PV_BLOCK_ERR_IO;
    return PV_BLOCK_OK;
}

static void os_drop_map(mmap_state *st)
{
    if (st->view && st->map_size) {
        munmap(st->view, (size_t)st->map_size);
        st->view = NULL;
        st->map_size = 0;
    }
}

static int os_build_map(mmap_state *st, uint64_t size)
{
    int prot = PROT_READ | (st->writable ? PROT_WRITE : 0);
    os_drop_map(st);
    if (size == 0) return PV_BLOCK_OK;
    st->view = mmap(NULL, (size_t)size, prot, MAP_SHARED, st->fd, 0);
    if (st->view == MAP_FAILED) {
        st->view = NULL;
        return PV_BLOCK_ERR_IO;
    }
    st->map_size = size;
    return PV_BLOCK_OK;
}

static int os_read(mmap_state *st, uint64_t offset, void *buf, uint32_t len)
{
    ssize_t n = pread(st->fd, buf, len, (off_t)offset);
    return (n == (ssize_t)len) ? PV_BLOCK_OK : PV_BLOCK_ERR_IO;
}

static int os_write(mmap_state *st, uint64_t offset, const void *buf, uint32_t len)
{
    ssize_t n = pwrite(st->fd, buf, len, (off_t)offset);
    return (n == (ssize_t)len) ? PV_BLOCK_OK : PV_BLOCK_ERR_IO;
}

static int mmap_open(pv_block_dev *d, const char *path, uint32_t flags,
                     uint64_t initial_size)
{
    mmap_state *st;
    int oflags = 0;
    if (!path) return PV_BLOCK_ERR_INVAL;
    st = (mmap_state *)calloc(1, sizeof(*st));
    if (!st) return PV_BLOCK_ERR_NOMEM;
    if (flags & PV_BLOCK_WRITE) {
        oflags = O_RDWR;
        st->writable = 1;
    } else {
        oflags = O_RDONLY;
    }
    if (flags & PV_BLOCK_CREATE) oflags |= O_CREAT;
    if (flags & PV_BLOCK_TRUNCATE) oflags |= O_TRUNC;
#ifdef O_SYNC
    if (flags & PV_BLOCK_SYNC) oflags |= O_SYNC;
#endif
    st->fd = open(path, oflags, 0644);
    if (st->fd < 0) {
        free(st);
        return PV_BLOCK_ERR_IO;
    }
    st->want_map = (flags & PV_BLOCK_MAP) ? 1 : 0;
    d->os = st;
    d->block_size = 4096;
    d->flags = flags;
    d->open = 1;
    if (st->writable && initial_size > 0) {
        uint64_t cur = 0;
        if (os_size(st, &cur) != PV_BLOCK_OK || cur < initial_size) {
            if (os_set_size(st, initial_size) != PV_BLOCK_OK) {
                pv_block_close(d);
                return PV_BLOCK_ERR_IO;
            }
        }
    }
    if (os_size(st, &d->size_bytes) != PV_BLOCK_OK) {
        pv_block_close(d);
        return PV_BLOCK_ERR_IO;
    }
    if (st->want_map && d->size_bytes > 0)
        (void)os_build_map(st, d->size_bytes);
    return PV_BLOCK_OK;
}

static void mmap_close(pv_block_dev *d)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st) return;
    os_drop_map(st);
    if (st->fd >= 0) close(st->fd);
    free(st);
    d->os = NULL;
}

static int mmap_sync(pv_block_dev *d)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st) return PV_BLOCK_ERR_NOT_OPEN;
    if (st->view && st->map_size &&
        msync(st->view, (size_t)st->map_size, MS_SYNC) != 0)
        return PV_BLOCK_ERR_IO;
    if (fsync(st->fd) != 0) return PV_BLOCK_ERR_IO;
    return PV_BLOCK_OK;
}

#endif

static int mmap_read(pv_block_dev *d, uint64_t offset, void *buf, uint32_t len)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st || !buf) return PV_BLOCK_ERR_INVAL;
    if (offset + len > d->size_bytes) return PV_BLOCK_ERR_INVAL;
    if (st->view && offset + len <= st->map_size) {
        memcpy(buf, (const uint8_t *)st->view + offset, len);
        return PV_BLOCK_OK;
    }
    return os_read(st, offset, buf, len);
}

static int mmap_write(pv_block_dev *d, uint64_t offset, const void *buf, uint32_t len)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st || !buf || !st->writable) return PV_BLOCK_ERR_INVAL;
    if (offset + len > d->size_bytes) {
        uint64_t ns = align_up(offset + len, d->block_size ? d->block_size : 4096);
        os_drop_map(st);
        if (os_set_size(st, ns) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
        d->size_bytes = ns;
        if (st->want_map) (void)os_build_map(st, d->size_bytes);
    }
    if (st->view && offset + len <= st->map_size) {
        memcpy((uint8_t *)st->view + offset, buf, len);
        return PV_BLOCK_OK;
    }
    return os_write(st, offset, buf, len);
}

static int mmap_resize(pv_block_dev *d, uint64_t new_size)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st || !st->writable) return PV_BLOCK_ERR_INVAL;
    os_drop_map(st);
    if (os_set_size(st, new_size) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
    d->size_bytes = new_size;
    if (st->want_map && new_size > 0) (void)os_build_map(st, new_size);
    return PV_BLOCK_OK;
}

static uint64_t mmap_size(const pv_block_dev *d) { return d->size_bytes; }
static uint32_t mmap_bsize(const pv_block_dev *d)
{
    return d->block_size ? d->block_size : 4096u;
}

static int mmap_map(pv_block_dev *d, uint64_t offset, uint32_t len, int writable,
                    void **out_ptr)
{
    mmap_state *st = (mmap_state *)d->os;
    if (!st || !out_ptr) return PV_BLOCK_ERR_INVAL;
    if (writable && !st->writable) return PV_BLOCK_ERR_INVAL;
    if (offset + len > d->size_bytes) return PV_BLOCK_ERR_INVAL;
    if (!st->view) {
        if (os_build_map(st, d->size_bytes) != PV_BLOCK_OK) return PV_BLOCK_ERR_IO;
    }
    if (!st->view || offset + len > st->map_size) return PV_BLOCK_ERR_IO;
    *out_ptr = (uint8_t *)st->view + offset;
    return PV_BLOCK_OK;
}

static int mmap_unmap(pv_block_dev *d, void *ptr, uint32_t len)
{
    (void)d;
    (void)ptr;
    (void)len;
    return PV_BLOCK_OK;
}

static int mmap_read_blocks(pv_block_dev *d, uint64_t lba, void *buf, uint32_t nblocks)
{
    uint32_t bs = mmap_bsize(d);
    return mmap_read(d, lba * (uint64_t)bs, buf, nblocks * bs);
}

static int mmap_write_blocks(pv_block_dev *d, uint64_t lba, const void *buf,
                             uint32_t nblocks)
{
    uint32_t bs = mmap_bsize(d);
    return mmap_write(d, lba * (uint64_t)bs, buf, nblocks * bs);
}

static const pv_block_ops g_mmap_ops = {
    "mmap",
    mmap_open,
    mmap_close,
    mmap_read,
    mmap_write,
    mmap_sync,
    mmap_resize,
    mmap_size,
    mmap_map,
    mmap_unmap,
    mmap_bsize,
    mmap_read_blocks,
    mmap_write_blocks,
};

const pv_block_ops *pv_block_ops_mmap(void) { return &g_mmap_ops; }
