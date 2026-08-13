#ifndef PV_BLOCK_VM_H
#define PV_BLOCK_VM_H

#include "pv_block.h"
#include "../../vm/pico_hooks.h"
#include "../../vm/picovm.h"

#ifndef PV_BLOCK_VM_MAX_TRANSFER
#define PV_BLOCK_VM_MAX_TRANSFER (16u * 1024u)
#endif

void pv_block_vm_install(void);
void pv_block_vm_bind(pv_ctx *ctx, pv_block_dev *device);
int pv_block_vm_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#endif
