#ifndef PICOVM_CATQ_CUDA_H
#define PICOVM_CATQ_CUDA_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct pv_catq_cuda_options {
    int epochs;
    int group_size;
    int batch_size;
    float gamma;
    float sharpness;
    float learning_rate;
    float weight_decay;
} pv_catq_cuda_options;

int pv_catq_cuda_available(void);

int pv_catq_cuda_optimize(
    const float *weight,
    int rows,
    int cols,
    const float *calibration,
    int calibration_rows,
    const pv_catq_cuda_options *options,
    float *delta_mu,
    float *delta_alpha,
    float *delta_threshold,
    float *final_loss
);

#ifdef __cplusplus
}
#endif

#endif
