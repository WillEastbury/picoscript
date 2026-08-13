/* POSIX hosted provider — Linux / WSL / macOS. */
#ifndef _WIN32

#include "pv_host_provider.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#if defined(__linux__)
#include <sys/sysinfo.h>
#endif
#include <sys/types.h>
#include <pthread.h>

static struct timespec g_start;
static int g_start_ready;

static void ensure_start(void)
{
    if (g_start_ready) return;
    clock_gettime(CLOCK_MONOTONIC, &g_start);
    g_start_ready = 1;
}

static int64_t posix_now(void *user)
{
    struct timespec ts;
    (void)user;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) return 0;
    return (int64_t)ts.tv_sec;
}

static uint32_t posix_rand_u32(void *user)
{
    uint32_t v = 0;
    (void)user;
#if defined(__linux__)
    {
        FILE *f = fopen("/dev/urandom", "rb");
        if (f) {
            size_t n = fread(&v, 1, sizeof(v), f);
            fclose(f);
            if (n == sizeof(v)) return v;
        }
    }
#endif
    /* Fallback: mix time */
    v = (uint32_t)(posix_now(0) ^ (uint32_t)getpid());
    return v;
}

static int posix_rand_bytes(void *user, uint8_t *dst, uint32_t n)
{
    FILE *f;
    size_t got;
    (void)user;
    f = fopen("/dev/urandom", "rb");
    if (!f) return -1;
    got = fread(dst, 1, n, f);
    fclose(f);
    return got == n ? 0 : -1;
}

static int posix_os(void *user, char *out, uint32_t capacity)
{
    (void)user;
#if defined(__linux__)
    return snprintf(out, capacity, "Linux");
#elif defined(__APPLE__)
    return snprintf(out, capacity, "Darwin");
#else
    return snprintf(out, capacity, "POSIX");
#endif
}

static int posix_hostname(void *user, char *out, uint32_t capacity)
{
    (void)user;
    if (!out || capacity == 0) return -1;
    if (gethostname(out, capacity) != 0) return -1;
    out[capacity - 1] = 0;
    return (int)strlen(out);
}

static int posix_timezone(void *user, char *out, uint32_t capacity)
{
    time_t t = time(NULL);
    struct tm tm;
    (void)user;
#if defined(_POSIX_THREAD_SAFE_FUNCTIONS)
    localtime_r(&t, &tm);
#else
    {
        struct tm *p = localtime(&t);
        if (!p) return snprintf(out, capacity, "UTC");
        tm = *p;
    }
#endif
    if (tm.tm_zone && tm.tm_zone[0])
        return snprintf(out, capacity, "%s", tm.tm_zone);
    return snprintf(out, capacity, "UTC");
}

static int32_t posix_cpu(void *user)
{
    long n;
    (void)user;
    n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (int32_t)n : 1;
}

static int64_t posix_mem_total(void *user)
{
    (void)user;
#if defined(__linux__)
    {
        struct sysinfo si;
        if (sysinfo(&si) == 0)
            return (int64_t)si.totalram * (int64_t)si.mem_unit;
    }
#endif
    {
        long pages = sysconf(_SC_PHYS_PAGES);
        long psize = sysconf(_SC_PAGESIZE);
        if (pages > 0 && psize > 0)
            return (int64_t)pages * (int64_t)psize;
    }
    return 0;
}

static int64_t posix_mem_free(void *user)
{
    (void)user;
#if defined(__linux__)
    {
        struct sysinfo si;
        if (sysinfo(&si) == 0)
            return (int64_t)si.freeram * (int64_t)si.mem_unit;
    }
#endif
    {
        long pages = sysconf(_SC_AVPHYS_PAGES);
        long psize = sysconf(_SC_PAGESIZE);
        if (pages > 0 && psize > 0)
            return (int64_t)pages * (int64_t)psize;
    }
    return 0;
}

static int32_t posix_pid(void *user)
{
    (void)user;
    return (int32_t)getpid();
}

static int32_t posix_tid(void *user)
{
    (void)user;
#if defined(__linux__)
    return (int32_t)gettid();
#else
    return (int32_t)(uintptr_t)pthread_self();
#endif
}

static int32_t posix_elapsed(void *user)
{
    struct timespec now;
    int64_t ms;
    (void)user;
    ensure_start();
    clock_gettime(CLOCK_MONOTONIC, &now);
    ms = (int64_t)(now.tv_sec - g_start.tv_sec) * 1000 +
         (int64_t)(now.tv_nsec - g_start.tv_nsec) / 1000000;
    return (int32_t)ms;
}

static const pv_host_provider g_posix = {
    PV_HOST_KIND_POSIX,
    "posix",
    PV_CAP_TIME | PV_CAP_RANDOM | PV_CAP_ENV,
    posix_now,
    posix_rand_u32,
    posix_rand_bytes,
    posix_os,
    posix_hostname,
    posix_timezone,
    posix_cpu,
    posix_mem_total,
    posix_mem_free,
    posix_pid,
    posix_tid,
    posix_elapsed,
    pv_host_provider_shared_hook,
    0
};

const pv_host_provider *pv_host_platform(void)
{
    ensure_start();
    return &g_posix;
}

#endif /* !_WIN32 */
