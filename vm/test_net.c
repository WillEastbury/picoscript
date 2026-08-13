#include "picovm.h"
#include "picovm_net.h"
#include "pico_hooks.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <process.h>
static unsigned __stdcall pv_test_net_server(void *arg)
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <pthread.h>
#include <unistd.h>
static void *pv_test_net_server(void *arg)
#endif
{
    int port = *(int *)arg;
    char req[4];
#ifdef _WIN32
    SOCKET s = socket(AF_INET, SOCK_STREAM, 0), c;
#else
    int s = socket(AF_INET, SOCK_STREAM, 0), c;
#endif
    struct sockaddr_in addr;
    int yes = 1;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((unsigned short)port);
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char *)&yes, sizeof(yes));
    bind(s, (struct sockaddr *)&addr, sizeof(addr));
    listen(s, 1);
    c = accept(s, 0, 0);
    recv(c, req, 4, 0);
    send(c, "pong", 4, 0);
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

int main(void)
{
    static unsigned char mem[256 * 1024];
    pv_ctx ctx;
    int conn, sent, reply, ok;
    int port = 29431;
    int endpoint;
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
    ctx.cap_ceiling = PV_CAP_ALL;
    ok = pv_net_install_socket_provider();
    CHECK(ok == 1);

#ifdef _WIN32
    th = (HANDLE)_beginthreadex(NULL, 0, pv_test_net_server, &port, 0, &tid);
    CHECK(th != 0);
    Sleep(50);
#else
    CHECK(pthread_create(&th, NULL, pv_test_net_server, &port) == 0);
    usleep(50000);
#endif

    endpoint = pv_span_from_bytes(&ctx, "127.0.0.1", 9);
    CHECK(endpoint > 0);
    conn = (int)pv_host2(&ctx, PV_HOOK_NET_CONNECT, endpoint, port);
    CHECK(conn > 0);
    sent = (int)pv_host2(&ctx, PV_HOOK_NET_SENDSPAN, conn, pv_span_from_bytes(&ctx, "ping", 4));
    CHECK(sent == 4);
    reply = (int)pv_host2(&ctx, PV_HOOK_NET_RECVSPAN, conn, 4);
    CHECK(reply > 0);
    CHECK(ctx.span_len[reply] == 4);
    CHECK(memcmp(ctx.mem + ctx.span_ptr[reply], "pong", 4) == 0);
    CHECK((int)pv_host2(&ctx, PV_HOOK_NET_SHUTDOWN, conn, 0) == 1);

#ifdef _WIN32
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
#else
    pthread_join(th, NULL);
#endif
    pv_net_socket_cleanup();
    printf("PASS raw net primitives\n");
    return 0;
}
