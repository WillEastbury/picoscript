/* Windows hosted provider — wall clock, BCrypt/ RtlGenRandom, env facts. */
#ifdef _WIN32

#include "pv_host_provider.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <string.h>

#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "advapi32.lib")

static LARGE_INTEGER g_qpc0;
static LARGE_INTEGER g_qpf;
static int g_qpc_ready;

static void ensure_qpc(void)
{
    if (g_qpc_ready) return;
    QueryPerformanceFrequency(&g_qpf);
    QueryPerformanceCounter(&g_qpc0);
    g_qpc_ready = 1;
}

static int64_t win_now(void *user)
{
    FILETIME ft;
    ULARGE_INTEGER u;
    (void)user;
    GetSystemTimeAsFileTime(&ft);
    u.LowPart = ft.dwLowDateTime;
    u.HighPart = ft.dwHighDateTime;
    /* FILETIME is 100ns since 1601-01-01; Unix epoch offset: 11644473600 s */
    return (int64_t)((u.QuadPart / 10000000ULL) - 11644473600ULL);
}

static uint32_t win_rand_u32(void *user)
{
    uint32_t v = 0;
    (void)user;
    if (BCryptGenRandom(NULL, (PUCHAR)&v, sizeof(v),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        /* fallback: mix time */
        v = (uint32_t)(win_now(0) ^ GetTickCount());
    }
    return v;
}

static int win_rand_bytes(void *user, uint8_t *dst, uint32_t n)
{
    (void)user;
    if (BCryptGenRandom(NULL, dst, n, BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        return -1;
    return 0;
}

static int win_os(void *user, char *out, uint32_t capacity)
{
    OSVERSIONINFOA vi;
    (void)user;
    memset(&vi, 0, sizeof(vi));
    vi.dwOSVersionInfoSize = sizeof(vi);
    /* GetVersionEx is deprecated but sufficient for a coarse string. */
#pragma warning(push)
#pragma warning(disable : 4996)
    if (!GetVersionExA(&vi))
        return snprintf(out, capacity, "Windows") > 0
                   ? (int)strlen(out)
                   : -1;
#pragma warning(pop)
    return snprintf(out, capacity, "Windows %u.%u",
                    (unsigned)vi.dwMajorVersion,
                    (unsigned)vi.dwMinorVersion);
}

static int win_hostname(void *user, char *out, uint32_t capacity)
{
    DWORD n = capacity;
    (void)user;
    if (!out || capacity == 0) return -1;
    if (!GetComputerNameA(out, &n)) return -1;
    return (int)strlen(out);
}

static int win_timezone(void *user, char *out, uint32_t capacity)
{
    TIME_ZONE_INFORMATION tzi;
    char buf[128];
    int i;
    (void)user;
    if (GetTimeZoneInformation(&tzi) == TIME_ZONE_ID_INVALID) {
        return snprintf(out, capacity, "UTC");
    }
    /* Convert WCHAR standard name to ASCII-ish. */
    for (i = 0; i < 63 && tzi.StandardName[i]; i++)
        buf[i] = (char)(tzi.StandardName[i] & 0x7f);
    buf[i] = 0;
    if (!buf[0]) return snprintf(out, capacity, "UTC");
    return snprintf(out, capacity, "%s", buf);
}

static int32_t win_cpu(void *user)
{
    SYSTEM_INFO si;
    (void)user;
    GetSystemInfo(&si);
    return (int32_t)si.dwNumberOfProcessors;
}

static int64_t win_mem_total(void *user)
{
    MEMORYSTATUSEX ms;
    (void)user;
    ms.dwLength = sizeof(ms);
    if (!GlobalMemoryStatusEx(&ms)) return 0;
    return (int64_t)ms.ullTotalPhys;
}

static int64_t win_mem_free(void *user)
{
    MEMORYSTATUSEX ms;
    (void)user;
    ms.dwLength = sizeof(ms);
    if (!GlobalMemoryStatusEx(&ms)) return 0;
    return (int64_t)ms.ullAvailPhys;
}

static int32_t win_pid(void *user)
{
    (void)user;
    return (int32_t)GetCurrentProcessId();
}

static int32_t win_tid(void *user)
{
    (void)user;
    return (int32_t)GetCurrentThreadId();
}

static int32_t win_elapsed(void *user)
{
    LARGE_INTEGER now;
    (void)user;
    ensure_qpc();
    QueryPerformanceCounter(&now);
    if (g_qpf.QuadPart == 0) return 0;
    return (int32_t)(((now.QuadPart - g_qpc0.QuadPart) * 1000) / g_qpf.QuadPart);
}

static const pv_host_provider g_win = {
    PV_HOST_KIND_WIN,
    "win",
    PV_CAP_TIME | PV_CAP_RANDOM | PV_CAP_ENV,
    win_now,
    win_rand_u32,
    win_rand_bytes,
    win_os,
    win_hostname,
    win_timezone,
    win_cpu,
    win_mem_total,
    win_mem_free,
    win_pid,
    win_tid,
    win_elapsed,
    pv_host_provider_shared_hook,
    0
};

const pv_host_provider *pv_host_platform(void)
{
    ensure_qpc();
    return &g_win;
}

#endif /* _WIN32 */
