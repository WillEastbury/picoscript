/* PIOS host provider.
 *
 * Hosted Windows/Linux builds keep the documented skeleton behavior.
 * Actual PIOS builds bind to the kernel/user-core services already present in
 * the system: random.c, socket.c, x509.c.
 *
 * Do NOT call POSIX/Win32 APIs from a freestanding PIOS worker.
 */
#include "pv_host_provider.h"

#include "pico_hooks.h"

#include <string.h>

#if defined(PIOS_PLATFORM) || defined(PIOS_USER_EL0)
#define PV_PIOS_ACTIVE 1
#include "random.h"
#include "socket.h"
#include "x509.h"
#else
#define PV_PIOS_ACTIVE 0
#endif

static int pios_unavailable_hook(pv_ctx *ctx, int rd, int status)
{
    if (!ctx) return 0;
    ctx->regs[rd] = 0;
    ctx->host_status = status;
    return 1;
}

#if PV_PIOS_ACTIVE
static int pios_span_view(pv_ctx *ctx, int handle, const uint8_t **ptr, uint32_t *len)
{
    uint32_t p;
    int32_t n;
    if (!ctx || !ptr || !len || !ctx->mem || handle <= 0 || handle >= ctx->span_count)
        return 0;
    p = ctx->span_ptr[handle];
    n = ctx->span_len[handle];
    if (n < 0 || p > (uint32_t)ctx->mem_size || (uint32_t)n > (uint32_t)ctx->mem_size - p)
        return 0;
    *ptr = ctx->mem + p;
    *len = (uint32_t)n;
    return 1;
}

static void pios_span_cstr_copy(pv_ctx *ctx, int handle, char *out, uint32_t capacity)
{
    const uint8_t *ptr;
    uint32_t len;
    uint32_t n;
    if (!out || capacity == 0) return;
    out[0] = 0;
    if (!pios_span_view(ctx, handle, &ptr, &len)) return;
    n = len;
    if (n >= capacity) n = capacity - 1;
    if (n) memcpy(out, ptr, n);
    out[n] = 0;
}

static int pios_handle_net(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    struct sockaddr_in addr;
    uint8_t buf[1024];
    const uint8_t *src;
    uint32_t src_len;
    int fd;
    int rc;
    if (!ctx) return 0;

    switch (hook) {
    case PV_HOOK_NET_LISTEN:
        fd = sock_socket(SOCK_STREAM);
        if (fd < 0) return pios_unavailable_hook(ctx, rd, 1);
        memset(&addr, 0, sizeof(addr));
        addr.ip = 0;
        addr.port = (uint16_t)(ctx->regs[rs1] & 0xffff);
        rc = sock_bind(fd, &addr);
        if (rc == SOCK_OK) rc = sock_listen(fd, (uint32_t)ctx->regs[rs2]);
        if (rc != SOCK_OK) {
            sock_close(fd);
            return pios_unavailable_hook(ctx, rd, 1);
        }
        ctx->regs[rd] = fd;
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_NET_ACCEPT:
        rc = sock_accept(ctx->regs[rs1], 0);
        if (rc < 0) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = rc;
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_NET_READ:
    case PV_HOOK_NET_RECVSPAN:
        src_len = (uint32_t)ctx->regs[rs2];
        if (src_len == 0 || src_len > sizeof(buf)) src_len = (uint32_t)sizeof(buf);
        rc = sock_recv(ctx->regs[rs1], buf, src_len);
        if (rc < 0) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = pv_span_from_bytes(ctx, buf, (uint32_t)rc);
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_NET_WRITE:
        if (!pios_span_view(ctx, ctx->regs[rs2], &src, &src_len)) {
            ctx->regs[rd] = 0;
            ctx->host_status = 2;
            return 1;
        }
        rc = sock_send(ctx->regs[rs1], src, src_len);
        if (rc < 0) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = rc;
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_NET_SHUTDOWN:
        rc = sock_close(ctx->regs[rs1]);
        ctx->regs[rd] = (rc == SOCK_OK) ? 1 : 0;
        ctx->host_status = (rc == SOCK_OK) ? 0 : 1;
        return 1;

    case PV_HOOK_NET_POOLSIZE:
        ctx->regs[rd] = (int32_t)socket_udp_active_count();
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_NET_REGISTER:
        fd = sock_socket(SOCK_DGRAM);
        if (fd < 0) return pios_unavailable_hook(ctx, rd, 1);
        memset(&addr, 0, sizeof(addr));
        addr.ip = 0;
        addr.port = (uint16_t)(ctx->regs[rs1] & 0xffff);
        rc = sock_bind(fd, &addr);
        if (rc != SOCK_OK) {
            sock_close(fd);
            return pios_unavailable_hook(ctx, rd, 1);
        }
        ctx->regs[rd] = fd;
        ctx->host_status = 0;
        return 1;

    default:
        return pios_unavailable_hook(ctx, rd, 3);
    }
}

static int pios_handle_x509(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    uint32_t len = 0;
    const uint8_t *der;
    const uint8_t *csr;
    struct x509_status st;
    char cn[64];
    (void)rs2;
    if (!ctx) return 0;

    switch (hook) {
    case PV_HOOK_X509_FETCHCERTIFICATE:
        der = x509_certificate_der(&len);
        if (!der || len == 0) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = pv_span_from_bytes(ctx, der, len);
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_X509_GENERATECSR:
        pios_span_cstr_copy(ctx, ctx->regs[rs1], cn, (uint32_t)sizeof(cn));
        if (!x509_generate_csr(cn[0] ? cn : 0)) return pios_unavailable_hook(ctx, rd, 1);
        csr = x509_csr_der(&len);
        if (!csr || len == 0) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = pv_span_from_bytes(ctx, csr, len);
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_X509_GETCERTINFO:
        x509_status(&st);
        if (!st.initialized) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = pv_span_from_bytes(ctx, st.subject, (uint32_t)strnlen(st.subject, sizeof(st.subject)));
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_X509_ISCERTVALID:
        x509_status(&st);
        ctx->regs[rd] = (st.has_cert && st.der_len > 0) ? 1 : 0;
        ctx->host_status = 0;
        return 1;

    case PV_HOOK_X509_GETKEYHANDLE:
        x509_status(&st);
        ctx->regs[rd] = (int32_t)st.key_fingerprint;
        ctx->host_status = st.has_key ? 0 : 1;
        return 1;

    default:
        return pios_unavailable_hook(ctx, rd, 3);
    }
}
#endif

int pv_host_provider_pios_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, void *user)
{
    (void)user;
    if (!ctx) return 0;

    /* Timers are already handled by the shared hosted hook. */
    if (pv_host_provider_shared_hook(ctx, hook, rd, rs1, rs2, user))
        return 1;

#if PV_PIOS_ACTIVE
    if (hook == PV_HOOK_CRYPTO_RANDOMBYTES) {
        int32_t n = ctx->regs[rs1];
        uint8_t tmp[512];
        uint32_t count;
        if (n <= 0) {
            ctx->regs[rd] = 0;
            ctx->host_status = 2;
            return 1;
        }
        count = (uint32_t)n;
        if (count > sizeof(tmp)) count = (uint32_t)sizeof(tmp);
        if (!crypto_random_bytes(tmp, count)) return pios_unavailable_hook(ctx, rd, 1);
        ctx->regs[rd] = pv_span_from_bytes(ctx, tmp, count);
        ctx->host_status = 0;
        return 1;
    }

    if ((hook >= PV_HOOK_NET_LISTEN && hook <= PV_HOOK_NET_REGISTER) ||
        (hook >= PV_HOOK_NET_CONNECT && hook <= PV_HOOK_NET_RECVSPAN))
        return pios_handle_net(ctx, hook, rd, rs1, rs2);

    if (hook >= PV_HOOK_X509_FETCHCERTIFICATE && hook <= PV_HOOK_X509_GETKEYHANDLE)
        return pios_handle_x509(ctx, hook, rd, rs1, rs2);
#else
    /* Kernel RNG / DRBG lane. */
    if (hook == PV_HOOK_CRYPTO_RANDOMBYTES)
        return pios_unavailable_hook(ctx, rd, 1);

    /* Kernel socket / CORE_NET FIFO lane. */
    if ((hook >= PV_HOOK_NET_LISTEN && hook <= PV_HOOK_NET_REGISTER) ||
        (hook >= PV_HOOK_NET_CONNECT && hook <= PV_HOOK_NET_RECVSPAN))
        return pios_unavailable_hook(ctx, rd, 1);

    /* Kernel X509 / key store lane. */
    if (hook >= PV_HOOK_X509_FETCHCERTIFICATE && hook <= PV_HOOK_X509_GETKEYHANDLE)
        return pios_unavailable_hook(ctx, rd, 1);
#endif

    return 0;
}

static int64_t pios_now(void *user)
{
    (void)user;
    /* TODO: mailbox TIME.NowUnix */
    return 0;
}

static uint32_t pios_rand_u32(void *user)
{
    uint32_t v = 0;
#if PV_PIOS_ACTIVE
    (void)user;
    if (!crypto_random_bytes(&v, (uint32_t)sizeof(v))) return 0;
    return v;
#else
    (void)user;
    return 0;
#endif
}

static int pios_rand_bytes(void *user, uint8_t *dst, uint32_t n)
{
    (void)user;
#if PV_PIOS_ACTIVE
    if (!dst) return -1;
    return crypto_random_bytes(dst, n) ? 0 : -1;
#else
    memset(dst, 0, n);
    /* TODO: kernel CSPRNG / deterministic DRBG under seed */
    return -1;
#endif
}

static int pios_unavail(void *user, char *out, uint32_t capacity)
{
    (void)user;
    if (!out || capacity == 0) return -1;
    out[0] = 0;
    return -1;
}

static int32_t pios_zero(void *user)
{
    (void)user;
    return 0;
}

static int64_t pios_zero64(void *user)
{
    (void)user;
    return 0;
}

static const pv_host_provider g_pios = {
    PV_HOST_KIND_PIOS,
    "pios",
    PV_CAP_TIME | PV_CAP_RANDOM | PV_CAP_ENV,
    pios_now,
    pios_rand_u32,
    pios_rand_bytes,
    pios_unavail,
    pios_unavail,
    pios_unavail,
    pios_zero,
    pios_zero64,
    pios_zero64,
    pios_zero,
    pios_zero,
    pios_zero,
    pv_host_provider_pios_hook,
    0
};

const pv_host_provider *pv_host_pios(void)
{
    return &g_pios;
}
