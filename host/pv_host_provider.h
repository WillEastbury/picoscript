/* pv_host_provider.h -- multi-target host binding surface for PicoScript.
 *
 * The VM (picovm.c) stays pure + deterministic. Host-injected hooks
 * (time, entropy, environment, …) go through an installable provider:
 *
 *   null   — frozen clock + seeded DRBG (CI / replay)
 *   win    — Windows hosted
 *   posix  — Linux / WSL / macOS hosted
 *   pios   — kernel IPC stubs (messages, not syscalls)
 *
 * Pure namespaces never route here. Capability bits (PV_CAP_*) still gate
 * dispatch inside the VM (INV-17).
 */
#ifndef PV_HOST_PROVIDER_H
#define PV_HOST_PROVIDER_H

#include "picovm.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Provider identity (for diagnostics). */
enum {
    PV_HOST_KIND_NULL = 0,
    PV_HOST_KIND_WIN = 1,
    PV_HOST_KIND_POSIX = 2,
    PV_HOST_KIND_PIOS = 3
};

typedef struct pv_host_provider {
    int kind;
    const char *name;
    uint32_t caps; /* PV_CAP_* this provider can satisfy (hint; VM still gates) */

    /* Time: UTC unix seconds (wall). In deterministic mode, frozen. */
    int64_t (*now_unix)(void *user);

    /* Entropy */
    uint32_t (*random_u32)(void *user);
    int (*random_bytes)(void *user, uint8_t *dst, uint32_t n); /* 0 ok, non-zero fail */

    /* Environment — write UTF-8 into out (NUL-terminated if room). Return length
     * excluding NUL, or -1 if unavailable. capacity includes space for NUL. */
    int (*os_version)(void *user, char *out, uint32_t capacity);
    int (*hostname)(void *user, char *out, uint32_t capacity);
    int (*timezone)(void *user, char *out, uint32_t capacity);
    int32_t (*cpu_count)(void *user);
    int64_t (*memory_total)(void *user); /* bytes; 0 if unknown */
    int64_t (*memory_free)(void *user);
    int32_t (*process_id)(void *user);
    int32_t (*thread_id)(void *user);
    int32_t (*elapsed_ms)(void *user); /* ms since provider install / process start */

    /* Optional hosted extension path for hook families that still need a
     * platform-aware implementation but don't justify their own global slot. */
    int (*hook)(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, void *user);

    void *user;
} pv_host_provider;

/* Install provider (or NULL to clear). Install also registers the VM hook
 * dispatch so DateTime.Now / Environment.* / Maths.Random / Crypto.RandomBytes
 * resolve through this provider. */
void pv_host_install(const pv_host_provider *provider);

/* Currently installed provider, or NULL. */
const pv_host_provider *pv_host_current(void);

/* Built-in providers. */
const pv_host_provider *pv_host_null(void);     /* deterministic */
const pv_host_provider *pv_host_platform(void); /* win or posix */
const pv_host_provider *pv_host_pios(void);     /* PIOS IPC stub */

/* Seed the null/deterministic provider (and Random.U32-compatible stream).
 * Call before or after install; affects null provider only. */
void pv_host_null_seed(uint64_t seed);
void pv_host_null_set_time(int64_t unix_seconds);

/* Hook dispatcher called from pv_default_host. Returns 1 if handled. */
int pv_host_provider_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

/* Shared hosted timer hook used by the built-in providers. */
int pv_host_provider_shared_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, void *user);

/* PIOS mailbox/FIFO-backed hook contract. The current C provider skeleton uses
 * the existing public hook ids and returns handled+unavailable until the kernel
 * service lanes are wired behind it.
 *
 * Families expected to route through this path on PIOS:
 *   - Crypto.RandomBytes
 *   - Net.Listen / Accept / Read / Write / Shutdown / PoolSize / Register
 *   - X509.*
 */
int pv_host_provider_pios_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, void *user);

/* Public span helper used by providers (implemented in picovm.c). */
int pv_span_from_bytes(pv_ctx *ctx, const void *data, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif /* PV_HOST_PROVIDER_H */
