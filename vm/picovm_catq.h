#ifndef PICOVM_CATQ_H
#define PICOVM_CATQ_H

#include "picovm.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct pv_catq_packed_info {
    const uint8_t *codes;
    size_t codes_len;
    const float *scales;
    size_t scale_count;
    size_t value_count;
    int rows;
    int cols;
    int group_size;
} pv_catq_packed_info;

/* Install the dependency-free native CAT-Q provider into pv_compute_hook. */
int pv_catq_install(void);

/* Release all tensors, contexts, jobs, shards, and packed buffers. */
void pv_catq_cleanup(void);

/* Provider entry point for explicit hook composition. */
int pv_catq_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

/* Native host helpers for registering calibration/weight tensors. */
int pv_catq_register_f32(const float *values, int rows, int cols);

/* Inspection helpers used by native hosts and conformance tests. */
int pv_catq_get_packed(int handle, pv_catq_packed_info *out);
float pv_catq_final_loss(int optimized_handle);

#ifdef __cplusplus
}
#endif

#endif
