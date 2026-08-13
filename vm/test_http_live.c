#include "picovm.h"
#include "picovm_emu.h"
#include "pico_hooks.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <process.h>
static unsigned __stdcall pv_test_http_server(void *arg)
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <pthread.h>
#include <unistd.h>
static void *pv_test_http_server(void *arg)
#endif
{
    int port = *(int *)arg;
#ifdef _WIN32
    SOCKET s = socket(AF_INET, SOCK_STREAM, 0), c;
#else
    int s = socket(AF_INET, SOCK_STREAM, 0), c;
#endif
    struct sockaddr_in addr;
    char req[1024];
    int yes = 1;
    const char *resp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nX-Test: yes\r\nContent-Length: 4\r\n\r\npong";
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((unsigned short)port);
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char *)&yes, sizeof(yes));
    bind(s, (struct sockaddr *)&addr, sizeof(addr));
    listen(s, 1);
    c = accept(s, 0, 0);
    recv(c, req, sizeof(req), 0);
    send(c, resp, (int)strlen(resp), 0);
#ifdef _WIN32
    closesocket(c);
    closesocket(s);
    return 0;
#else
    close(c);
    close(s);
    return 0;
#endif
}

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

static int has_bytes(const unsigned char *buf, int len, const char *lit, int lit_len)
{
    int i;
    if (!buf || len < lit_len || lit_len <= 0) return 0;
    for (i = 0; i + lit_len <= len; i++)
        if (memcmp(buf + i, lit, (size_t)lit_len) == 0) return 1;
    return 0;
}

int main(void)
{
    static unsigned char mem[256 * 1024];
    pv_ctx ctx;
    int endpoint, method, status, hdr, body, genh, genr;
    int port = 29432;
#ifdef _WIN32
    HANDLE th;
    unsigned tid;
#else
    pthread_t th;
#endif

    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = (long)sizeof(mem);
    ctx.caps = PV_CAP_ALL;

#ifdef _WIN32
    WSADATA wsa;
    CHECK(WSAStartup(MAKEWORD(2, 2), &wsa) == 0);
    th = (HANDLE)_beginthreadex(NULL, 0, pv_test_http_server, &port, 0, &tid);
    CHECK(th != 0);
    Sleep(50);
#else
    CHECK(pthread_create(&th, NULL, pv_test_http_server, &port) == 0);
    usleep(50000);
#endif

    endpoint = pv_span_from_bytes(&ctx, "http://127.0.0.1:29432/ping", 28);
    method = pv_span_from_bytes(&ctx, "GET", 3);
    ctx.regs[1] = endpoint;
    ctx.regs[2] = method;
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_REQUEST, 0, 1, 2);
    status = ctx.regs[0];
    CHECK(status == 200);
    CHECK(ctx.host_status == 0);
    ctx.regs[1] = 0; ctx.regs[2] = 0;
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_RESPHEADERS, 0, 1, 2);
    hdr = ctx.regs[0];
    CHECK(hdr > 0 && ctx.span_len[hdr] > 0);
    CHECK(has_bytes(mem + ctx.span_ptr[hdr], ctx.span_len[hdr], "X-Test: yes", 11));
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_RESPBODY, 0, 1, 2);
    body = ctx.regs[0];
    CHECK(body > 0 && ctx.span_len[body] == 4);
    CHECK(memcmp(mem + ctx.span_ptr[body], "pong", 4) == 0);

    ctx.regs[1] = 201;
    ctx.regs[2] = 0;
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_GENERATEHEADERS, 0, 1, 2);
    genh = ctx.regs[0];
    CHECK(genh > 0 && ctx.span_len[genh] > 0);
    CHECK(has_bytes(mem + ctx.span_ptr[genh], ctx.span_len[genh], "201", 3));

    ctx.regs[1] = 202;
    ctx.regs[2] = pv_span_from_bytes(&ctx, "ok", 2);
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_GENERATERESPONSE, 0, 1, 2);
    genr = ctx.regs[0];
    CHECK(genr > 0 && ctx.span_len[genr] == 2);
    CHECK(memcmp(mem + ctx.span_ptr[genr], "ok", 2) == 0);

    ctx.regs[1] = pv_span_from_bytes(&ctx, "https://127.0.0.1:29432/ping", 29);
    ctx.regs[2] = method;
    pv_emu_dispatch(&ctx, PV_HOOK_HTTP_REQUEST, 0, 1, 2);
    CHECK(ctx.regs[0] == 0);
    CHECK(ctx.host_status == 2);

#ifdef _WIN32
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
    WSACleanup();
#else
    pthread_join(th, NULL);
#endif
    puts("PASS http live hooks");
    return 0;
}
