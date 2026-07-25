#include "picovm_net.h"
#include "pico_hooks.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET pv_socket_t;
#define PV_INVALID_SOCKET INVALID_SOCKET
#define pv_close_socket closesocket
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int pv_socket_t;
#define PV_INVALID_SOCKET (-1)
#define pv_close_socket close
#endif

#define PV_NET_MAX_SOCKETS 64

static pv_socket_t pv_net_sockets[PV_NET_MAX_SOCKETS];
static uint8_t pv_net_used[PV_NET_MAX_SOCKETS];
static int pv_net_ready;

static int pv_net_put(pv_socket_t sock)
{
    int i;
    for (i = 1; i < PV_NET_MAX_SOCKETS; i++) {
        if (!pv_net_used[i]) {
            pv_net_used[i] = 1;
            pv_net_sockets[i] = sock;
            return i;
        }
    }
    return 0;
}

static pv_socket_t pv_net_get(int handle)
{
    if (handle <= 0 || handle >= PV_NET_MAX_SOCKETS || !pv_net_used[handle])
        return PV_INVALID_SOCKET;
    return pv_net_sockets[handle];
}

static int pv_net_span(pv_ctx *ctx, int handle, const uint8_t **ptr, int32_t *len)
{
    uint32_t p;
    int32_t n;
    if (!ctx || !ctx->mem || !ptr || !len ||
        handle <= 0 || handle >= ctx->span_count)
        return 0;
    p = ctx->span_ptr[handle];
    n = ctx->span_len[handle];
    if (n < 0 || p > (uint32_t)ctx->mem_size ||
        (uint32_t)n > (uint32_t)ctx->mem_size - p)
        return 0;
    *ptr = ctx->mem + p;
    *len = n;
    return 1;
}

static int pv_net_finish_span(pv_ctx *ctx, uint32_t base, int32_t len)
{
    int handle;
    if (!ctx || len < 0 || ctx->span_count >= PV_MAX_SPANS)
        return 0;
    handle = ctx->span_count++;
    ctx->span_ptr[handle] = base;
    ctx->span_len[handle] = len;
    ctx->arena_top = base + (uint32_t)len;
    return handle;
}

static int pv_net_endpoint(pv_ctx *ctx, int handle, char *out, int cap)
{
    const uint8_t *ptr;
    int32_t len;
    int n;
    if (!pv_net_span(ctx, handle, &ptr, &len) || cap <= 0)
        return 0;
    n = len < cap - 1 ? len : cap - 1;
    memcpy(out, ptr, (size_t)n);
    out[n] = '\0';
    return n;
}

int pv_net_install_socket_provider(void)
{
#ifdef _WIN32
    WSADATA data;
    if (!pv_net_ready && WSAStartup(MAKEWORD(2, 2), &data) != 0)
        return 0;
#endif
    pv_net_ready = 1;
    pv_net_hook = pv_net_socket_hook;
    return 1;
}

void pv_net_socket_cleanup(void)
{
    int i;
    for (i = 1; i < PV_NET_MAX_SOCKETS; i++) {
        if (pv_net_used[i]) {
            pv_close_socket(pv_net_sockets[i]);
            pv_net_used[i] = 0;
        }
    }
#ifdef _WIN32
    if (pv_net_ready) WSACleanup();
#endif
    pv_net_ready = 0;
}

int pv_net_socket_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    int a = ctx->regs[rs1];
    int b = ctx->regs[rs2];
    pv_socket_t sock;

    if (hook == PV_HOOK_NET_LISTEN) {
        struct sockaddr_in addr;
        int yes = 1;
        sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock == PV_INVALID_SOCKET) goto fail_scalar;
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, (const char *)&yes, sizeof(yes));
        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port = htons((uint16_t)(a < 0 ? 0 : a));
        if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0 ||
            listen(sock, b > 0 ? b : 16) != 0) {
            pv_close_socket(sock);
            goto fail_scalar;
        }
        ctx->regs[rd] = pv_net_put(sock);
        ctx->host_status = ctx->regs[rd] ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_NET_ACCEPT) {
        pv_socket_t listener = pv_net_get(a);
        if (listener == PV_INVALID_SOCKET) goto fail_scalar;
        sock = accept(listener, 0, 0);
        if (sock == PV_INVALID_SOCKET) goto fail_scalar;
        ctx->regs[rd] = pv_net_put(sock);
        ctx->host_status = ctx->regs[rd] ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_NET_CONNECT) {
        char endpoint[256];
        char port[16];
        struct addrinfo hints, *result = 0, *it;
        if (!pv_net_endpoint(ctx, a, endpoint, sizeof(endpoint))) goto fail_scalar;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;
#ifdef _WIN32
        _snprintf(port, sizeof(port), "%d", b);
#else
        snprintf(port, sizeof(port), "%d", b);
#endif
        if (getaddrinfo(endpoint, port, &hints, &result) != 0) goto fail_scalar;
        sock = PV_INVALID_SOCKET;
        for (it = result; it; it = it->ai_next) {
            sock = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
            if (sock == PV_INVALID_SOCKET) continue;
            if (connect(sock, it->ai_addr, (int)it->ai_addrlen) == 0) break;
            pv_close_socket(sock);
            sock = PV_INVALID_SOCKET;
        }
        freeaddrinfo(result);
        if (sock == PV_INVALID_SOCKET) goto fail_scalar;
        ctx->regs[rd] = pv_net_put(sock);
        ctx->host_status = ctx->regs[rd] ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_NET_READ || hook == PV_HOOK_NET_RECVSPAN) {
        int32_t max_bytes;
        int got;
        uint32_t base;
        sock = pv_net_get(a);
        if (sock == PV_INVALID_SOCKET || !ctx->mem || ctx->no_alloc) goto fail_span;
        base = ctx->arena_top;
        if (base > (uint32_t)ctx->mem_size) goto fail_span;
        max_bytes = b > 0 ? b : 65536;
        if ((uint32_t)max_bytes > (uint32_t)ctx->mem_size - base)
            max_bytes = (int32_t)((uint32_t)ctx->mem_size - base);
        got = recv(sock, (char *)(ctx->mem + base), max_bytes, 0);
        if (got < 0) goto fail_span;
        ctx->regs[rd] = pv_net_finish_span(ctx, base, got);
        ctx->host_status = ctx->regs[rd] ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_NET_WRITE || hook == PV_HOOK_NET_SENDSPAN) {
        const uint8_t *ptr;
        int32_t len;
        int sent = 0;
        sock = pv_net_get(a);
        if (sock == PV_INVALID_SOCKET || !pv_net_span(ctx, b, &ptr, &len))
            goto fail_scalar;
        while (sent < len) {
            int n = send(sock, (const char *)ptr + sent, len - sent, 0);
            if (n <= 0) goto fail_scalar;
            sent += n;
        }
        ctx->regs[rd] = sent;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_NET_SHUTDOWN) {
        sock = pv_net_get(a);
        if (sock == PV_INVALID_SOCKET) goto fail_scalar;
#ifdef _WIN32
        shutdown(sock, SD_BOTH);
#else
        shutdown(sock, SHUT_RDWR);
#endif
        pv_close_socket(sock);
        pv_net_used[a] = 0;
        ctx->regs[rd] = 1;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_NET_POOLSIZE || hook == PV_HOOK_NET_REGISTER) {
        ctx->regs[rd] = 1;
        ctx->host_status = 0;
        return 1;
    }
    return 0;

fail_span:
    ctx->regs[rd] = pv_net_finish_span(ctx, ctx->arena_top, 0);
    ctx->host_status = 1;
    return 1;
fail_scalar:
    ctx->regs[rd] = 0;
    ctx->host_status = 1;
    return 1;
}
