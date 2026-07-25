#ifndef PICOVM_NET_H
#define PICOVM_NET_H

#include "picovm.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Install the synchronous host socket provider into pv_net_hook. */
int pv_net_install_socket_provider(void);

/* Close every socket owned by the provider and release host resources. */
void pv_net_socket_cleanup(void);

/* Provider entry point for applications that want explicit hook composition. */
int pv_net_socket_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#ifdef __cplusplus
}
#endif

#endif
