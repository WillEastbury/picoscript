/* Smoke: mmap R/W + WALFS sim LBA R/W. */
#include "pv_block.h"

#include <stdio.h>
#include <string.h>

#define CHECK(x)                                                               \
    do {                                                                       \
        if (!(x)) {                                                            \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x);       \
            return 1;                                                          \
        }                                                                      \
    } while (0)

static uint8_t rp_image[8 * 512];
static uint8_t rp_scratch[512];
static int rp_read(void *ctx, uint64_t lba, void *buf, uint32_t nblocks)
{
    (void)ctx;
    if (lba + nblocks > 8) return -1;
    memcpy(buf, rp_image + (size_t)lba * 512, (size_t)nblocks * 512);
    return 0;
}
static int rp_write(void *ctx, uint64_t lba, const void *buf, uint32_t nblocks)
{
    (void)ctx;
    if (lba + nblocks > 8) return -1;
    memcpy(rp_image + (size_t)lba * 512, buf, (size_t)nblocks * 512);
    return 0;
}
static int rp_sync(void *ctx) { (void)ctx; return 0; }

int main(void)
{
    pv_block_dev d;
    uint8_t buf[4096];
    void *view = NULL;
    const char *path = "test_block_mmap.bin";
    const char *wpath = "test_block_walfs.bin";

    /* ---- mmap ---- */
    CHECK(pv_block_open(&d, pv_block_ops_mmap(), path,
                        PV_BLOCK_READ | PV_BLOCK_WRITE | PV_BLOCK_CREATE |
                            PV_BLOCK_TRUNCATE | PV_BLOCK_MAP,
                        8192) == PV_BLOCK_OK);
    CHECK(pv_block_size(&d) == 8192);
    CHECK(pv_block_block_size(&d) == 4096);

    memset(buf, 0xA5, 64);
    CHECK(pv_block_write(&d, 100, buf, 64) == PV_BLOCK_OK);
    memset(buf, 0, 64);
    CHECK(pv_block_read(&d, 100, buf, 64) == PV_BLOCK_OK);
    CHECK(buf[0] == 0xA5 && buf[63] == 0xA5);

    CHECK(pv_block_map(&d, 100, 64, 0, &view) == PV_BLOCK_OK);
    CHECK(view != NULL);
    CHECK(((uint8_t *)view)[0] == 0xA5);
    CHECK(pv_block_unmap(&d, view, 64) == PV_BLOCK_OK);

    memset(buf, 0x5A, 4096);
    CHECK(pv_block_write_blocks(&d, 1, buf, 1) == PV_BLOCK_OK);
    CHECK(pv_block_sync(&d) == PV_BLOCK_OK);
    pv_block_close(&d);
    printf("PASS mmap block\n");

    /* ---- WALFS simulator ---- */
    CHECK(pv_walfs_sim_open(wpath, 512, 16,
                            PV_BLOCK_READ | PV_BLOCK_WRITE | PV_BLOCK_CREATE |
                                PV_BLOCK_TRUNCATE) == PV_BLOCK_OK);
    CHECK(pv_block_open(&d, pv_block_ops_walfs(), "vol0",
                        PV_BLOCK_READ | PV_BLOCK_WRITE, 0) == PV_BLOCK_OK);
    CHECK(pv_block_block_size(&d) == 512);
    CHECK(pv_block_size(&d) == 512ull * 16);

    memset(buf, 0x3C, 512);
    CHECK(pv_block_write_blocks(&d, 2, buf, 1) == PV_BLOCK_OK);
    memset(buf, 0, 512);
    CHECK(pv_block_read_blocks(&d, 2, buf, 1) == PV_BLOCK_OK);
    CHECK(buf[0] == 0x3C && buf[511] == 0x3C);

    /* unaligned range write across LBA boundary */
    memset(buf, 0x11, 64);
    CHECK(pv_block_write(&d, 500, buf, 64) == PV_BLOCK_OK);
    memset(buf, 0, 64);
    CHECK(pv_block_read(&d, 500, buf, 64) == PV_BLOCK_OK);
    CHECK(buf[0] == 0x11 && buf[63] == 0x11);

    CHECK(pv_block_sync(&d) == PV_BLOCK_OK);
    pv_block_close(&d);
    pv_walfs_sim_close();
    printf("PASS walfs sim block\n");

    /* ---- RP2350 callback adapter: no heap, unaligned RMW via scratch ---- */
    {
        pv_rp2350_backend rp = {0};
        rp.block_size = 512; rp.block_count = 8;
        rp.read_blocks = rp_read; rp.write_blocks = rp_write; rp.sync = rp_sync;
        rp.scratch = rp_scratch; rp.scratch_size = sizeof(rp_scratch);
        pv_rp2350_install(&rp);
        CHECK(pv_block_open(&d, pv_block_ops_rp2350(), "flash0",
                            PV_BLOCK_READ | PV_BLOCK_WRITE, 0) == PV_BLOCK_OK);
        memset(buf, 0x7E, 40);
        CHECK(pv_block_write(&d, 500, buf, 40) == PV_BLOCK_OK);
        memset(buf, 0, 40);
        CHECK(pv_block_read(&d, 500, buf, 40) == PV_BLOCK_OK);
        CHECK(buf[0] == 0x7E && buf[39] == 0x7E);
        CHECK(pv_block_sync(&d) == PV_BLOCK_OK);
        pv_block_close(&d);
        pv_rp2350_install(NULL);
        printf("PASS rp2350 callback block\n");
    }

    printf("PASS raw block primitives\n");
    return 0;
}
