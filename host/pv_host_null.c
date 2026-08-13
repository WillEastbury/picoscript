/* Deterministic host provider — frozen clock + xorshift64* DRBG (INV-15). */
#include "pv_host_provider.h"

#include <string.h>

static uint64_t g_seed = 0x2545F4914F6CDD1DULL;
static int64_t g_time = 1700000000; /* fixed: 2023-11-14T22:13:20Z */
static int32_t g_elapsed;

static uint64_t xorshift64(void)
{
    uint64_t x = g_seed;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    g_seed = x ? x : 0x2545F4914F6CDD1DULL;
    return g_seed;
}

void pv_host_null_seed(uint64_t seed)
{
    g_seed = seed ? seed : 0x2545F4914F6CDD1DULL;
    g_elapsed = 0;
}

void pv_host_null_set_time(int64_t unix_seconds)
{
    g_time = unix_seconds;
}

static int64_t null_now(void *user)
{
    (void)user;
    return g_time;
}

static uint32_t null_rand_u32(void *user)
{
    (void)user;
    return (uint32_t)(xorshift64() & 0xffffffffu);
}

static int null_rand_bytes(void *user, uint8_t *dst, uint32_t n)
{
    uint32_t i = 0;
    (void)user;
    while (i < n) {
        uint32_t w = null_rand_u32(0);
        uint32_t j;
        for (j = 0; j < 4 && i < n; j++, i++)
            dst[i] = (uint8_t)(w >> (j * 8));
    }
    return 0;
}

static int copy_lit(char *out, uint32_t capacity, const char *lit)
{
    size_t n = strlen(lit);
    if (!out || capacity == 0) return -1;
    if (n >= capacity) n = capacity - 1;
    memcpy(out, lit, n);
    out[n] = 0;
    return (int)n;
}

static int null_os(void *u, char *o, uint32_t c)
{
    (void)u;
    return copy_lit(o, c, "null");
}
static int null_host(void *u, char *o, uint32_t c)
{
    (void)u;
    return copy_lit(o, c, "localhost");
}
static int null_tz(void *u, char *o, uint32_t c)
{
    (void)u;
    return copy_lit(o, c, "UTC");
}
static int32_t null_cpu(void *u)
{
    (void)u;
    return 1;
}
static int64_t null_mem(void *u)
{
    (void)u;
    return 0;
}
static int32_t null_pid(void *u)
{
    (void)u;
    return 1;
}
static int32_t null_tid(void *u)
{
    (void)u;
    return 1;
}
static int32_t null_elapsed(void *u)
{
    (void)u;
    return g_elapsed++;
}

static const pv_host_provider g_null = {
    PV_HOST_KIND_NULL,
    "null",
    PV_CAP_TIME | PV_CAP_RANDOM | PV_CAP_ENV,
    null_now,
    null_rand_u32,
    null_rand_bytes,
    null_os,
    null_host,
    null_tz,
    null_cpu,
    null_mem,
    null_mem,
    null_pid,
    null_tid,
    null_elapsed,
    pv_host_provider_shared_hook,
    0
};

const pv_host_provider *pv_host_null(void)
{
    return &g_null;
}
