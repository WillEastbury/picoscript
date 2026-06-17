/* picovm_pool.c -- pre-allocated worker pool for PicoScript handlers.
 *
 * Hosted builds use OS sockets + worker threads; PIOS gets a small holster that
 * documents where mailbox/WFI primitives plug in. The hot path never mallocs:
 * each worker owns one pv_ctx plus a 512 KB arena that is rewound per request.
 */
#include "picovm_pool.h"

#if defined(PIOS)

/* PIOS holster: the kernel owns accepts, posts the accepted endpoint into the
 * worker mailbox, and the worker WFI-sleeps until woken. No pthreads/Win32 here;
 * real PIOS builds replace these stubs with mailbox + IPC FIFO glue. */
int pv_pool_init(pv_pool *pool, int port, int workers, pv_handler_fn handler)
{
    int i;
    if (!pool || !handler) return -1;
    if (workers <= 0) workers = PV_POOL_DEFAULT;
    if (workers > PV_POOL_MAX) workers = PV_POOL_MAX;
    pool->server_fd = PV_SOCKET_INVALID;
    pool->port = port;
    pool->worker_count = workers;
    pool->running = 1;
    pool->handler = handler;
    for (i = 0; i < workers; i++) {
        pv_worker *w = &pool->workers[i];
        pv_init(&w->ctx);
        w->ctx.mem = w->arena;
        w->ctx.mem_size = PV_POOL_ARENA_SIZE;
        w->owner = pool;
        w->conn_fd = PV_SOCKET_INVALID;
        w->busy = 0;
        w->stop = 0;
        w->mailbox_token = 0;
    }
    return 0;
}

void pv_pool_run(pv_pool *pool) { (void)pool; }
void pv_pool_stop(pv_pool *pool)
{
    int i;
    if (!pool) return;
    pool->running = 0;
    for (i = 0; i < pool->worker_count; i++) pool->workers[i].stop = 1;
}
void pv_worker_reset(pv_worker *w)
{
    if (!w) return;
    w->ctx.arena_top = 0x8000;
    w->ctx.span_count = 1;
    w->ctx.w_count = 1;
    w->ctx.r_count = 1;
    w->ctx.out_len = 0;
    w->ctx.http_status = -1;
    w->ctx.http_type = 0;
    w->ctx.halted = 0;
    w->ctx.waiting = 0;
    w->ctx.fault = 0;
    w->ctx.fault_pc = 0;
    w->ctx.fault_detail = 0;
    w->busy = 0;
    w->conn_fd = PV_SOCKET_INVALID;
}

pv_socket_t pv_socket_listen(int port) { (void)port; return PV_SOCKET_INVALID; }
pv_socket_t pv_socket_accept(pv_socket_t fd) { (void)fd; return PV_SOCKET_INVALID; }
int pv_socket_read(pv_socket_t fd, void *buf, int len) { (void)fd; (void)buf; (void)len; return -1; }
int pv_socket_write(pv_socket_t fd, const void *buf, int len) { (void)fd; (void)buf; (void)len; return -1; }
void pv_socket_close(pv_socket_t fd) { (void)fd; }
int pv_send_http_response(pv_socket_t conn_fd, pv_ctx *ctx) { (void)conn_fd; (void)ctx; return -1; }

#elif defined(__STDC_HOSTED__) && __STDC_HOSTED__

#include <errno.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#define PV_SLEEP_MS(ms) Sleep((DWORD)(ms))
static int pv_socket_boot(void)
{
    static int ready = 0;
    WSADATA wsa;
    if (ready) return 0;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return -1;
    ready = 1;
    return 0;
}
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#define PV_SLEEP_MS(ms) usleep((unsigned int)((ms) * 1000u))
static int pv_socket_boot(void) { return 0; }
#endif

static const char *pv_http_reason(int status)
{
    switch (status) {
        case 200: return "OK";
        case 201: return "Created";
        case 204: return "No Content";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 500: return "Internal Server Error";
        default:  return "OK";
    }
}

static const char *pv_http_type_name(int marker)
{
    switch (marker) {
        case 0xA000: return "text/html";
        case 0xA001: return "text/plain";
        case 0xA002: return "application/json";
        case 0xA003: return "text/css";
        case 0xA004: return "text/javascript";
        case 0xA005: return "image/png";
        case 0xA006: return "image/jpeg";
        case 0xA007: return "application/octet-stream";
        default:     return "application/octet-stream";
    }
}

static void pv_worker_bind_ctx(pv_worker *w)
{
    pv_init(&w->ctx);
    w->ctx.mem = w->arena;
    w->ctx.mem_size = PV_POOL_ARENA_SIZE;
    w->ctx.host = pv_default_host;
}

void pv_worker_reset(pv_worker *w)
{
    int i;
    if (!w) return;
    for (i = 0; i < PV_NUM_REGS; i++) w->ctx.regs[i] = 0;
    w->ctx.call_sp = 0;
    w->ctx.retval = 0;
    w->ctx.steps = 0;
    w->ctx.halted = 0;
    w->ctx.waiting = 0;
    w->ctx.fault = 0;
    w->ctx.fault_pc = 0;
    w->ctx.fault_detail = 0;
    w->ctx.out_len = 0;
    w->ctx.http_status = -1;
    w->ctx.http_type = 0;
    w->ctx.host_status = 0;
    w->ctx.span_count = 1;
    w->ctx.w_count = 1;
    w->ctx.r_count = 1;
    w->ctx.arena_top = 0x8000;
    w->conn_fd = PV_SOCKET_INVALID;
    w->busy = 0;
}

pv_socket_t pv_socket_listen(int port)
{
    pv_socket_t fd;
    struct sockaddr_in addr;
    int yes = 1;
    if (pv_socket_boot() != 0) return PV_SOCKET_INVALID;
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == PV_SOCKET_INVALID) return PV_SOCKET_INVALID;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);
#ifdef _WIN32
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, (const char *)&yes, sizeof(yes));
#else
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
#endif
    if (bind(fd, (struct sockaddr *)&addr, (int)sizeof(addr)) != 0) {
        pv_socket_close(fd);
        return PV_SOCKET_INVALID;
    }
    if (listen(fd, 128) != 0) {
        pv_socket_close(fd);
        return PV_SOCKET_INVALID;
    }
    return fd;
}

pv_socket_t pv_socket_accept(pv_socket_t fd)
{
#ifdef _WIN32
    struct sockaddr_in addr;
    int len = (int)sizeof(addr);
    return accept(fd, (struct sockaddr *)&addr, &len);
#else
    struct sockaddr_in addr;
    socklen_t len = (socklen_t)sizeof(addr);
    return accept(fd, (struct sockaddr *)&addr, &len);
#endif
}

int pv_socket_read(pv_socket_t fd, void *buf, int len)
{
#ifdef _WIN32
    return recv(fd, (char *)buf, len, 0);
#else
    return (int)recv(fd, buf, (size_t)len, 0);
#endif
}

int pv_socket_write(pv_socket_t fd, const void *buf, int len)
{
    int sent = 0;
    while (sent < len) {
#ifdef _WIN32
        int n = send(fd, (const char *)buf + sent, len - sent, 0);
#else
        int n = (int)send(fd, (const char *)buf + sent, (size_t)(len - sent), 0);
#endif
        if (n <= 0) return sent > 0 ? sent : -1;
        sent += n;
    }
    return sent;
}

void pv_socket_close(pv_socket_t fd)
{
    if (fd == PV_SOCKET_INVALID) return;
#ifdef _WIN32
    closesocket(fd);
#else
    close(fd);
#endif
}

int pv_send_http_response(pv_socket_t conn_fd, pv_ctx *ctx)
{
    char header[256];
    int hlen;
    int status;
    const char *ctype;
    if (!ctx) return -1;
    if (ctx->out_len >= 5 && ctx->out[0] == 'H' && ctx->out[1] == 'T' &&
        ctx->out[2] == 'T' && ctx->out[3] == 'P' && ctx->out[4] == '/') {
        return pv_socket_write(conn_fd, ctx->out, ctx->out_len);
    }
    status = ctx->http_status > 0 ? ctx->http_status : 200;
    ctype = pv_http_type_name(ctx->http_type);
    hlen = snprintf(header, sizeof(header),
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "\r\n",
        status, pv_http_reason(status), ctype, ctx->out_len);
    if (hlen < 0) return -1;
    if (pv_socket_write(conn_fd, header, hlen) < 0) return -1;
    if (ctx->out_len > 0 && pv_socket_write(conn_fd, ctx->out, ctx->out_len) < 0) return -1;
    return hlen + ctx->out_len;
}

/* Parse an HTTP/1.1 request in `buf` (length n) and point ctx->req_* at it.
 * Reads the request line (METHOD SP PATH SP VERSION CRLF), the header block,
 * and the body after CRLFCRLF. Reads more from the socket if Content-Length
 * indicates the body is not yet fully buffered. The buffer must outlive the
 * handler call (it is the worker's persistent reqbuf). */
static void pv_http_parse_request(pv_ctx *ctx, char *buf, int n, pv_socket_t fd, int cap)
{
    ctx->req_method = ""; ctx->req_method_len = 0;
    ctx->req_path = "/";  ctx->req_path_len = 1;
    ctx->req_headers = ""; ctx->req_headers_len = 0;
    ctx->req_body = ""; ctx->req_body_len = 0;
    if (n <= 0) return;

    int i = 0;
    int ms = 0; while (i < n && buf[i] != ' ') i++;        /* method */
    ctx->req_method = buf + ms; ctx->req_method_len = i - ms;
    if (i < n) i++;                                         /* skip space */
    int ps = i; while (i < n && buf[i] != ' ' && buf[i] != '\r' && buf[i] != '\n') i++;
    ctx->req_path = buf + ps; ctx->req_path_len = i - ps;
    /* advance to end of request line */
    while (i < n && buf[i] != '\n') i++;
    if (i < n) i++;                                         /* past first \n */

    int hs = i;                                            /* header block start */
    /* find CRLFCRLF (end of headers) */
    int he = -1;
    for (int k = hs; k + 3 < n; k++) {
        if (buf[k] == '\r' && buf[k+1] == '\n' && buf[k+2] == '\r' && buf[k+3] == '\n') { he = k; break; }
    }
    if (he < 0) { ctx->req_headers = buf + hs; ctx->req_headers_len = n - hs; return; }
    ctx->req_headers = buf + hs; ctx->req_headers_len = he - hs;

    int body_start = he + 4;
    /* Determine Content-Length (case-insensitive) from the header block. */
    long content_len = 0;
    for (int k = hs; k < he; ) {
        int ls = k; while (k < he && buf[k] != '\n') k++;
        int le = k; if (le > ls && buf[le-1] == '\r') le--;
        const char *name = "content-length:";
        int nl = 15, m = 1;
        for (int j = 0; j < nl; j++) {
            if (ls + j >= le) { m = 0; break; }
            char a = buf[ls + j]; if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
            if (a != name[j]) { m = 0; break; }
        }
        if (m) {
            int vs = ls + nl; while (vs < le && (buf[vs] == ' ' || buf[vs] == '\t')) vs++;
            content_len = 0;
            /* Overflow-safe parse: a Content-Length can never exceed the request
               buffer, so cap there and treat anything larger as the cap. */
            while (vs < le && buf[vs] >= '0' && buf[vs] <= '9') {
                content_len = content_len * 10 + (buf[vs++] - '0');
                if (content_len > cap) { content_len = cap; break; }
            }
            if (content_len < 0) content_len = 0;
            break;
        }
        if (k < he) k++;
    }
    int have = n - body_start;
    /* Read the rest of the body if it didn't all arrive in the first recv. */
    while (content_len > have && body_start + have < cap - 1) {
        int got = pv_socket_read(fd, buf + body_start + have, cap - 1 - (body_start + have));
        if (got <= 0) break;
        have += got;
        n += got;
    }
    ctx->req_body = buf + body_start;
    ctx->req_body_len = (int)(content_len > 0 ? (content_len < have ? content_len : have) : have);
    if (ctx->req_body_len < 0) ctx->req_body_len = 0;
}

#ifdef _WIN32
static DWORD WINAPI pv_worker_main(LPVOID arg)
#else
static void *pv_worker_main(void *arg)
#endif
{
    pv_worker *w = (pv_worker *)arg;
    for (;;) {
#ifdef _WIN32
        WaitForSingleObject(w->wake_event, INFINITE);
        if (w->stop) break;
#else
        pthread_mutex_lock(&w->lock);
        while (!w->stop && !w->busy) pthread_cond_wait(&w->cond, &w->lock);
        if (w->stop) {
            pthread_mutex_unlock(&w->lock);
            break;
        }
        pthread_mutex_unlock(&w->lock);
#endif
        w->ctx.regs[0] = (int32_t)(intptr_t)w->conn_fd;
        /* Read + parse the HTTP request into the worker's persistent reqbuf so
           PicoScript Req.Method()/Req.Path()/Req.Header()/Req.BodySpan() resolve
           natively while the handler runs. */
        {
            int rn = pv_socket_read(w->conn_fd, w->reqbuf, (int)sizeof(w->reqbuf) - 1);
            if (rn < 0) rn = 0;
            w->reqbuf[rn] = '\0';
            pv_http_parse_request(&w->ctx, w->reqbuf, rn, w->conn_fd, (int)sizeof(w->reqbuf));
        }
        if (w->owner && w->owner->handler) (void)w->owner->handler(&w->ctx);
        (void)pv_send_http_response(w->conn_fd, &w->ctx);
        pv_socket_close(w->conn_fd);
#ifdef _WIN32
        pv_worker_reset(w);
#else
        pthread_mutex_lock(&w->lock);
        pv_worker_reset(w);
        pthread_mutex_unlock(&w->lock);
#endif
    }
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

static pv_worker *pv_pool_claim_worker(pv_pool *pool)
{
    int i;
    while (pool->running) {
        for (i = 0; i < pool->worker_count; i++) {
            pv_worker *w = &pool->workers[i];
#ifdef _WIN32
            if (InterlockedCompareExchange((volatile LONG *)&w->busy, 1, 0) == 0) return w;
#else
            pthread_mutex_lock(&w->lock);
            if (!w->busy && !w->stop) {
                w->busy = 1;
                pthread_mutex_unlock(&w->lock);
                return w;
            }
            pthread_mutex_unlock(&w->lock);
#endif
        }
        PV_SLEEP_MS(1);
    }
    return NULL;
}

static void pv_pool_wake_worker(pv_worker *w, pv_socket_t conn_fd)
{
#ifdef _WIN32
    w->conn_fd = conn_fd;
    SetEvent(w->wake_event);
#else
    pthread_mutex_lock(&w->lock);
    w->conn_fd = conn_fd;
    pthread_cond_signal(&w->cond);
    pthread_mutex_unlock(&w->lock);
#endif
}

int pv_pool_init(pv_pool *pool, int port, int workers, pv_handler_fn handler)
{
    int i;
    if (!pool || !handler) return -1;
    memset(pool, 0, sizeof(*pool));
    if (workers <= 0) workers = PV_POOL_DEFAULT;
    if (workers > PV_POOL_MAX) workers = PV_POOL_MAX;
    pool->server_fd = pv_socket_listen(port);
    if (pool->server_fd == PV_SOCKET_INVALID) return -1;
    pool->port = port;
    pool->worker_count = workers;
    pool->running = 1;
    pool->handler = handler;
    for (i = 0; i < workers; i++) {
        pv_worker *w = &pool->workers[i];
        pv_worker_bind_ctx(w);
        w->owner = pool;
        w->conn_fd = PV_SOCKET_INVALID;
        w->busy = 0;
        w->stop = 0;
#ifdef _WIN32
        w->wake_event = CreateEvent(NULL, FALSE, FALSE, NULL);
        if (!w->wake_event) return -1;
        w->thread = CreateThread(NULL, 0, pv_worker_main, w, 0, NULL);
        if (!w->thread) return -1;
#else
        if (pthread_mutex_init(&w->lock, NULL) != 0) return -1;
        if (pthread_cond_init(&w->cond, NULL) != 0) return -1;
        if (pthread_create(&w->thread, NULL, pv_worker_main, w) != 0) return -1;
#endif
    }
    return 0;
}

void pv_pool_run(pv_pool *pool)
{
    while (pool && pool->running) {
        pv_socket_t conn_fd = pv_socket_accept(pool->server_fd);
        pv_worker *w;
        if (conn_fd == PV_SOCKET_INVALID) {
            if (!pool->running) break;
            PV_SLEEP_MS(1);
            continue;
        }
        w = pv_pool_claim_worker(pool);
        if (!w) {
            pv_socket_close(conn_fd);
            break;
        }
        pv_pool_wake_worker(w, conn_fd);
    }
}

void pv_pool_stop(pv_pool *pool)
{
    int i;
    if (!pool) return;
    pool->running = 0;
    pv_socket_close(pool->server_fd);
    pool->server_fd = PV_SOCKET_INVALID;
    for (i = 0; i < pool->worker_count; i++) {
        pv_worker *w = &pool->workers[i];
        w->stop = 1;
#ifdef _WIN32
        SetEvent(w->wake_event);
        if (w->thread) WaitForSingleObject(w->thread, INFINITE);
        if (w->thread) CloseHandle(w->thread);
        if (w->wake_event) CloseHandle(w->wake_event);
#else
        pthread_mutex_lock(&w->lock);
        pthread_cond_signal(&w->cond);
        pthread_mutex_unlock(&w->lock);
        pthread_join(w->thread, NULL);
        pthread_cond_destroy(&w->cond);
        pthread_mutex_destroy(&w->lock);
#endif
        if (w->busy && w->conn_fd != PV_SOCKET_INVALID) pv_socket_close(w->conn_fd);
        pv_worker_reset(w);
    }
#ifdef _WIN32
    WSACleanup();
#endif
}

#else
/* Freestanding non-PIOS builds intentionally skip the native pool runtime. */
#endif
