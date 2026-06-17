/* picovm_pool.h -- thread-pooled native event runtime for PicoScript. */
#ifndef PV_POOL_H
#define PV_POOL_H

#include "picovm.h"
#include <stdint.h>

#define PV_POOL_MAX 64
#define PV_POOL_DEFAULT 8
#define PV_POOL_ARENA_SIZE (512u * 1024u)

#if defined(PIOS) || (defined(__STDC_HOSTED__) && __STDC_HOSTED__)
# ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#   define WIN32_LEAN_AND_MEAN
#  endif
#  include <winsock2.h>
#  include <windows.h>
   typedef SOCKET pv_socket_t;
#  define PV_SOCKET_INVALID INVALID_SOCKET
# else
#  include <pthread.h>
   typedef int pv_socket_t;
#  define PV_SOCKET_INVALID (-1)
# endif
#else
typedef int pv_socket_t;
# define PV_SOCKET_INVALID (-1)
#endif

typedef int64_t (*pv_handler_fn)(pv_ctx *ctx);

typedef struct pv_pool pv_pool;
typedef struct pv_worker pv_worker;

struct pv_worker {
    pv_ctx ctx;
    uint8_t arena[PV_POOL_ARENA_SIZE];
    pv_socket_t conn_fd;
    volatile int busy;
    volatile int stop;
    pv_pool *owner;
#if defined(PIOS)
    volatile int mailbox_token;
#elif defined(_WIN32)
    HANDLE thread;
    HANDLE wake_event;
#elif defined(__STDC_HOSTED__) && __STDC_HOSTED__
    pthread_t thread;
    pthread_mutex_t lock;
    pthread_cond_t cond;
#endif
};

struct pv_pool {
    pv_socket_t server_fd;
    int port;
    int worker_count;
    volatile int running;
    pv_handler_fn handler;
    pv_worker workers[PV_POOL_MAX];
};

#if defined(PIOS) || (defined(__STDC_HOSTED__) && __STDC_HOSTED__)
int pv_pool_init(pv_pool *pool, int port, int workers, pv_handler_fn handler);
void pv_pool_run(pv_pool *pool);
void pv_pool_stop(pv_pool *pool);
void pv_worker_reset(pv_worker *w);

pv_socket_t pv_socket_listen(int port);
pv_socket_t pv_socket_accept(pv_socket_t fd);
int pv_socket_read(pv_socket_t fd, void *buf, int len);
int pv_socket_write(pv_socket_t fd, const void *buf, int len);
void pv_socket_close(pv_socket_t fd);

int pv_send_http_response(pv_socket_t conn_fd, pv_ctx *ctx);
#endif

#endif /* PV_POOL_H */
