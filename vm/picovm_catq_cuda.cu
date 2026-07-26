#include "picovm_catq_cuda.h"

#include <cuda_runtime.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

constexpr int kWeightThreads = 256;
constexpr int kDotThreads = 256;
char last_error[256] = "";

void set_error(const char *operation, cudaError_t status)
{
    std::snprintf(
        last_error, sizeof(last_error), "%s: %s",
        operation, cudaGetErrorString(status));
}

__device__ float softplusf_stable(float value)
{
    if (value > 20.0f) return value;
    if (value < -20.0f) return expf(value);
    return log1pf(expf(value));
}

__device__ float sigmoidf_stable(float value)
{
    if (value >= 0.0f) {
        float z = expf(-value);
        return 1.0f / (1.0f + z);
    }
    float z = expf(value);
    return z / (1.0f + z);
}

__device__ float softened_value(
    float value, float sharpness, float threshold,
    float *d_value, float *d_threshold)
{
    float a = sharpness * (value - threshold);
    float b = sharpness * (value + threshold);
    float ta = tanhf(a);
    float tb = tanhf(b);
    float denom = 2.0f * tanhf(sharpness);
    float sa = 1.0f - ta * ta;
    float sb = 1.0f - tb * tb;
    if (fabsf(denom) < 1e-12f) denom = copysignf(1e-12f, denom);
    if (d_value) *d_value = sharpness * (sa + sb) / denom;
    if (d_threshold) *d_threshold = sharpness * (-sa + sb) / denom;
    return (ta + tb) / denom;
}

__device__ float hard_value(float value, float threshold)
{
    if (value > threshold) return 1.0f;
    if (value < -threshold) return -1.0f;
    return 0.0f;
}

__device__ float block_reduce_sum(float value, float *shared)
{
    int lane = threadIdx.x;
    shared[lane] = value;
    __syncthreads();
    for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
        if (lane < stride) shared[lane] += shared[lane + stride];
        __syncthreads();
    }
    return shared[0];
}

__global__ void group_stats_kernel(
    const float *weight, size_t count, int group_size,
    float *mu0, float *alpha0)
{
    extern __shared__ float shared[];
    size_t group = blockIdx.x;
    size_t start = group * static_cast<size_t>(group_size);
    size_t end = min(start + static_cast<size_t>(group_size), count);
    float sum = 0.0f;
    for (size_t index = start + threadIdx.x; index < end; index += blockDim.x)
        sum += weight[index];
    float mean = block_reduce_sum(sum, shared) / static_cast<float>(end - start);
    __syncthreads();
    float absolute = 0.0f;
    for (size_t index = start + threadIdx.x; index < end; index += blockDim.x)
        absolute += fabsf(weight[index] - mean);
    float deviation =
        block_reduce_sum(absolute, shared) / static_cast<float>(end - start);
    if (threadIdx.x == 0) {
        mu0[group] = mean;
        alpha0[group] = fmaxf(deviation, 1e-8f);
    }
}

__global__ void init_parameters_kernel(
    size_t groups,
    float *raw_mu, float *raw_alpha, float *raw_threshold)
{
    size_t group = blockIdx.x * blockDim.x + threadIdx.x;
    if (group >= groups) return;
    raw_mu[group] = 0.0f;
    raw_alpha[group] = 0.5413248546f;
    raw_threshold[group] = 0.5413248546f;
}

__global__ void group_cache_kernel(
    size_t groups,
    const float *raw_mu, const float *raw_alpha, const float *raw_threshold,
    const float *mu0, const float *alpha0,
    float *delta_mu, float *alpha, float *mu, float *threshold,
    float *sig_alpha, float *sig_threshold)
{
    size_t group = blockIdx.x * blockDim.x + threadIdx.x;
    if (group >= groups) return;
    float dm = tanhf(raw_mu[group]);
    float da = softplusf_stable(raw_alpha[group]);
    delta_mu[group] = dm;
    alpha[group] = da * alpha0[group];
    mu[group] = mu0[group] + dm * alpha0[group];
    threshold[group] = softplusf_stable(raw_threshold[group]) * 0.5f;
    sig_alpha[group] = sigmoidf_stable(raw_alpha[group]);
    sig_threshold[group] = sigmoidf_stable(raw_threshold[group]);
}

__global__ void quantize_kernel(
    const float *weight, size_t count, int group_size,
    const float *alpha, const float *mu, const float *threshold,
    float sharpness, int softened, float *quantized)
{
    size_t index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= count) return;
    size_t group = index / static_cast<size_t>(group_size);
    float z = (weight[index] - mu[group]) / alpha[group];
    float ternary = softened
        ? softened_value(z, sharpness, threshold[group], nullptr, nullptr)
        : hard_value(z, threshold[group]);
    quantized[index] = alpha[group] * ternary;
}

__global__ void error_kernel(
    const float *weight, const float *quantized,
    const float *calibration, int rows, int cols,
    int batch_start, int batch_count, float *errors)
{
    extern __shared__ float shared[];
    int item = blockIdx.x;
    int row = item % rows;
    int sample = item / rows;
    if (sample >= batch_count) return;
    const float *input =
        calibration + static_cast<size_t>(batch_start + sample) * cols;
    const float *source = weight + static_cast<size_t>(row) * cols;
    const float *approximation = quantized + static_cast<size_t>(row) * cols;
    float target = 0.0f;
    float predicted = 0.0f;
    for (int col = threadIdx.x; col < cols; col += blockDim.x) {
        float x = input[col];
        target += x * source[col];
        predicted += x * approximation[col];
    }
    float target_sum = block_reduce_sum(target, shared);
    __syncthreads();
    float predicted_sum = block_reduce_sum(predicted, shared);
    if (threadIdx.x == 0)
        errors[static_cast<size_t>(sample) * rows + row] =
            predicted_sum - target_sum;
}

__global__ void gradient_kernel(
    const float *calibration, const float *errors,
    int rows, int cols, int batch_start, int batch_count,
    float scale, float *gradient)
{
    size_t index = blockIdx.x * blockDim.x + threadIdx.x;
    size_t count = static_cast<size_t>(rows) * cols;
    if (index >= count) return;
    int row = static_cast<int>(index / cols);
    int col = static_cast<int>(index % cols);
    float value = 0.0f;
    for (int sample = 0; sample < batch_count; sample++)
        value += errors[static_cast<size_t>(sample) * rows + row] *
            calibration[static_cast<size_t>(batch_start + sample) * cols + col];
    gradient[index] = value * scale;
}

__global__ void derivative_kernel(
    const float *weight, const float *gradient, size_t count, int group_size,
    const float *alpha0, const float *delta_mu,
    const float *alpha, const float *mu, const float *threshold,
    const float *sig_alpha, const float *sig_threshold,
    float sharpness, int softened,
    float *mu_grad, float *alpha_grad, float *threshold_grad)
{
    extern __shared__ float shared[];
    float *shared_mu = shared;
    float *shared_alpha = shared + blockDim.x;
    float *shared_threshold = shared + blockDim.x * 2;
    size_t group = blockIdx.x;
    size_t start = group * static_cast<size_t>(group_size);
    size_t end = min(start + static_cast<size_t>(group_size), count);
    float gm = 0.0f, ga = 0.0f, gt = 0.0f;
    for (size_t index = start + threadIdx.x; index < end; index += blockDim.x) {
        float z = (weight[index] - mu[group]) / alpha[group];
        float dz, dth;
        float soft = softened_value(z, sharpness, threshold[group], &dz, &dth);
        float value = softened ? soft : hard_value(z, threshold[group]);
        float d_alpha = value - dz * z;
        float grad = gradient[index];
        gm += grad * (-dz * alpha0[group]) *
            (1.0f - delta_mu[group] * delta_mu[group]);
        ga += grad * (d_alpha * alpha0[group]) * sig_alpha[group];
        gt += grad * (alpha[group] * dth * 0.5f) * sig_threshold[group];
    }
    shared_mu[threadIdx.x] = gm;
    shared_alpha[threadIdx.x] = ga;
    shared_threshold[threadIdx.x] = gt;
    __syncthreads();
    for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
        if (threadIdx.x < stride) {
            shared_mu[threadIdx.x] += shared_mu[threadIdx.x + stride];
            shared_alpha[threadIdx.x] += shared_alpha[threadIdx.x + stride];
            shared_threshold[threadIdx.x] += shared_threshold[threadIdx.x + stride];
        }
        __syncthreads();
    }
    if (threadIdx.x == 0) {
        mu_grad[group] = shared_mu[0];
        alpha_grad[group] = shared_alpha[0];
        threshold_grad[group] = shared_threshold[0];
    }
}

__global__ void adam_kernel(
    size_t groups,
    float *raw_mu, float *raw_alpha, float *raw_threshold,
    const float *mu_grad, const float *alpha_grad, const float *threshold_grad,
    float *m_mu, float *m_alpha, float *m_threshold,
    float *v_mu, float *v_alpha, float *v_threshold,
    float learning_rate, float weight_decay,
    float beta1_pow, float beta2_pow)
{
    size_t group = blockIdx.x * blockDim.x + threadIdx.x;
    if (group >= groups) return;
    float *parameters[3] = {
        raw_mu + group, raw_alpha + group, raw_threshold + group
    };
    const float gradients[3] = {
        mu_grad[group], alpha_grad[group], threshold_grad[group]
    };
    float *moments[3] = {
        m_mu + group, m_alpha + group, m_threshold + group
    };
    float *variances[3] = {
        v_mu + group, v_alpha + group, v_threshold + group
    };
    for (int index = 0; index < 3; index++) {
        *moments[index] = 0.9f * *moments[index] + 0.1f * gradients[index];
        *variances[index] =
            0.999f * *variances[index] +
            0.001f * gradients[index] * gradients[index];
        float mhat = *moments[index] / (1.0f - beta1_pow);
        float vhat = *variances[index] / (1.0f - beta2_pow);
        *parameters[index] -= learning_rate *
            (mhat / (sqrtf(vhat) + 1e-8f) + weight_decay * *parameters[index]);
    }
}

int next_power_of_two(int value)
{
    int power = 1;
    while (power < value && power < 1024) power <<= 1;
    return power;
}

bool cuda_ok(cudaError_t status)
{
    return status == cudaSuccess;
}

}  // namespace

extern "C" int pv_catq_cuda_available(void)
{
    int devices = 0;
    return cudaGetDeviceCount(&devices) == cudaSuccess && devices > 0;
}

extern "C" const char *pv_catq_cuda_last_error(void)
{
    return last_error;
}

extern "C" int pv_catq_cuda_optimize(
    const float *weight,
    int rows,
    int cols,
    const float *calibration,
    int calibration_rows,
    const pv_catq_cuda_options *options,
    float *delta_mu,
    float *delta_alpha,
    float *delta_threshold,
    float *final_loss)
{
    size_t count;
    size_t groups;
    size_t calibration_count;
    int group_threads;
    int group_blocks;
    int weight_blocks;
    int steps_per_epoch;
    int total_steps;
    int step = 0;
    int last_batch_count = 0;
    float *d_weight = nullptr, *d_calibration = nullptr;
    float *d_mu0 = nullptr, *d_alpha0 = nullptr;
    float *d_raw_mu = nullptr, *d_raw_alpha = nullptr, *d_raw_threshold = nullptr;
    float *d_m_mu = nullptr, *d_m_alpha = nullptr, *d_m_threshold = nullptr;
    float *d_v_mu = nullptr, *d_v_alpha = nullptr, *d_v_threshold = nullptr;
    float *d_delta_mu = nullptr, *d_alpha = nullptr, *d_mu = nullptr;
    float *d_threshold = nullptr, *d_sig_alpha = nullptr, *d_sig_threshold = nullptr;
    float *d_quantized = nullptr, *d_gradient = nullptr, *d_errors = nullptr;
    float *d_mu_grad = nullptr, *d_alpha_grad = nullptr, *d_threshold_grad = nullptr;
    std::vector<float> host_errors;
    std::vector<float> raw_alpha;
    std::vector<float> raw_threshold;
    int result = 0;

    if (!weight || !calibration || !options || !delta_mu || !delta_alpha ||
        !delta_threshold || !final_loss || rows <= 0 || cols <= 0 ||
        calibration_rows <= 0 || options->group_size <= 0 ||
        options->normalization_rows <= 0 ||
        !pv_catq_cuda_available())
        return 0;
    last_error[0] = '\0';

    count = static_cast<size_t>(rows) * cols;
    groups =
        (count + static_cast<size_t>(options->group_size) - 1) /
        static_cast<size_t>(options->group_size);
    calibration_count = static_cast<size_t>(calibration_rows) * cols;
    group_threads = next_power_of_two(options->group_size);
    group_blocks = static_cast<int>((groups + 255) / 256);
    weight_blocks = static_cast<int>((count + kWeightThreads - 1) / kWeightThreads);
    steps_per_epoch =
        (calibration_rows + options->batch_size - 1) / options->batch_size;
    total_steps = options->epochs * steps_per_epoch;
    host_errors.resize(static_cast<size_t>(options->batch_size) * rows);
    raw_alpha.resize(groups);
    raw_threshold.resize(groups);

#define CUDA_ALLOC(pointer, elements) \
    do { \
        cudaError_t status = cudaMalloc(&(pointer), (elements) * sizeof(*(pointer))); \
        if (!cuda_ok(status)) { set_error("cudaMalloc " #pointer, status); goto cleanup; } \
    } while (0)
#define CUDA_ZERO(pointer, elements) \
    do { \
        cudaError_t status = cudaMemset((pointer), 0, (elements) * sizeof(*(pointer))); \
        if (!cuda_ok(status)) { set_error("cudaMemset " #pointer, status); goto cleanup; } \
    } while (0)

    CUDA_ALLOC(d_weight, count);
    CUDA_ALLOC(d_calibration, calibration_count);
    CUDA_ALLOC(d_mu0, groups);
    CUDA_ALLOC(d_alpha0, groups);
    CUDA_ALLOC(d_raw_mu, groups);
    CUDA_ALLOC(d_raw_alpha, groups);
    CUDA_ALLOC(d_raw_threshold, groups);
    CUDA_ALLOC(d_m_mu, groups);
    CUDA_ALLOC(d_m_alpha, groups);
    CUDA_ALLOC(d_m_threshold, groups);
    CUDA_ALLOC(d_v_mu, groups);
    CUDA_ALLOC(d_v_alpha, groups);
    CUDA_ALLOC(d_v_threshold, groups);
    CUDA_ALLOC(d_delta_mu, groups);
    CUDA_ALLOC(d_alpha, groups);
    CUDA_ALLOC(d_mu, groups);
    CUDA_ALLOC(d_threshold, groups);
    CUDA_ALLOC(d_sig_alpha, groups);
    CUDA_ALLOC(d_sig_threshold, groups);
    CUDA_ALLOC(d_quantized, count);
    CUDA_ALLOC(d_gradient, count);
    CUDA_ALLOC(d_errors, static_cast<size_t>(options->batch_size) * rows);
    CUDA_ALLOC(d_mu_grad, groups);
    CUDA_ALLOC(d_alpha_grad, groups);
    CUDA_ALLOC(d_threshold_grad, groups);

    {
        cudaError_t status = cudaMemcpy(
            d_weight, weight, count * sizeof(float), cudaMemcpyHostToDevice);
        if (!cuda_ok(status)) {
            set_error("copy weight to CUDA", status);
            goto cleanup;
        }
        status = cudaMemcpy(
            d_calibration, calibration, calibration_count * sizeof(float),
            cudaMemcpyHostToDevice);
        if (!cuda_ok(status)) {
            set_error("copy calibration to CUDA", status);
            goto cleanup;
        }
    }
    CUDA_ZERO(d_m_mu, groups);
    CUDA_ZERO(d_m_alpha, groups);
    CUDA_ZERO(d_m_threshold, groups);
    CUDA_ZERO(d_v_mu, groups);
    CUDA_ZERO(d_v_alpha, groups);
    CUDA_ZERO(d_v_threshold, groups);

    group_stats_kernel<<<static_cast<unsigned>(groups), group_threads,
        static_cast<size_t>(group_threads) * sizeof(float)>>>(
            d_weight, count, options->group_size, d_mu0, d_alpha0);
    init_parameters_kernel<<<group_blocks, 256>>>(
        groups, d_raw_mu, d_raw_alpha, d_raw_threshold);

    for (int epoch = 0; epoch < options->epochs; epoch++) {
        float time = static_cast<float>(epoch + 1) /
            static_cast<float>(options->epochs);
        float sharpness = time <= options->gamma
            ? (time / options->gamma) * options->sharpness
            : options->sharpness;
        int softened = time <= options->gamma;
        if (sharpness < 1e-6f) sharpness = 1e-6f;
        for (int batch_start = 0; batch_start < calibration_rows;
             batch_start += options->batch_size) {
            int batch_count =
                min(options->batch_size, calibration_rows - batch_start);
            float gradient_scale =
                2.0f /
                static_cast<float>(batch_count * options->normalization_rows);
            float learning_rate = options->learning_rate *
                (1.0f - static_cast<float>(step) /
                 static_cast<float>(total_steps));
            step++;
            last_batch_count = batch_count;

            group_cache_kernel<<<group_blocks, 256>>>(
                groups, d_raw_mu, d_raw_alpha, d_raw_threshold,
                d_mu0, d_alpha0, d_delta_mu, d_alpha, d_mu, d_threshold,
                d_sig_alpha, d_sig_threshold);
            quantize_kernel<<<weight_blocks, kWeightThreads>>>(
                d_weight, count, options->group_size,
                d_alpha, d_mu, d_threshold, sharpness, softened, d_quantized);
            error_kernel<<<batch_count * rows, kDotThreads,
                static_cast<size_t>(kDotThreads) * sizeof(float)>>>(
                    d_weight, d_quantized, d_calibration, rows, cols,
                    batch_start, batch_count, d_errors);
            gradient_kernel<<<weight_blocks, kWeightThreads>>>(
                d_calibration, d_errors, rows, cols,
                batch_start, batch_count, gradient_scale, d_gradient);
            derivative_kernel<<<static_cast<unsigned>(groups), group_threads,
                static_cast<size_t>(group_threads) * 3 * sizeof(float)>>>(
                    d_weight, d_gradient, count, options->group_size,
                    d_alpha0, d_delta_mu, d_alpha, d_mu, d_threshold,
                    d_sig_alpha, d_sig_threshold, sharpness, softened,
                    d_mu_grad, d_alpha_grad, d_threshold_grad);
            adam_kernel<<<group_blocks, 256>>>(
                groups, d_raw_mu, d_raw_alpha, d_raw_threshold,
                d_mu_grad, d_alpha_grad, d_threshold_grad,
                d_m_mu, d_m_alpha, d_m_threshold,
                d_v_mu, d_v_alpha, d_v_threshold,
                fmaxf(learning_rate, 0.0f), options->weight_decay,
                powf(0.9f, static_cast<float>(step)),
                powf(0.999f, static_cast<float>(step)));
        }
    }

    {
        cudaError_t status = cudaGetLastError();
        if (!cuda_ok(status)) {
            set_error("CAT-Q kernel launch", status);
            goto cleanup;
        }
        status = cudaDeviceSynchronize();
        if (!cuda_ok(status)) {
            set_error("CAT-Q kernel execution", status);
            goto cleanup;
        }
        status = cudaMemcpy(
            delta_mu, d_raw_mu, groups * sizeof(float), cudaMemcpyDeviceToHost);
        if (!cuda_ok(status)) {
            set_error("copy delta_mu from CUDA", status);
            goto cleanup;
        }
        status = cudaMemcpy(
            raw_alpha.data(), d_raw_alpha, groups * sizeof(float),
            cudaMemcpyDeviceToHost);
        if (!cuda_ok(status)) {
            set_error("copy delta_alpha from CUDA", status);
            goto cleanup;
        }
        status = cudaMemcpy(
            raw_threshold.data(), d_raw_threshold, groups * sizeof(float),
            cudaMemcpyDeviceToHost);
        if (!cuda_ok(status)) {
            set_error("copy delta_threshold from CUDA", status);
            goto cleanup;
        }
        status = cudaMemcpy(
            host_errors.data(), d_errors,
            static_cast<size_t>(last_batch_count) * rows * sizeof(float),
            cudaMemcpyDeviceToHost);
        if (!cuda_ok(status)) {
            set_error("copy errors from CUDA", status);
            goto cleanup;
        }
    }

    {
        double loss = 0.0;
        for (int index = 0; index < last_batch_count * rows; index++)
            loss += static_cast<double>(host_errors[index]) * host_errors[index];
        *final_loss = static_cast<float>(
            loss / static_cast<double>(last_batch_count * rows));
    }
    for (size_t group = 0; group < groups; group++) {
        delta_mu[group] = tanhf(delta_mu[group]);
        delta_alpha[group] =
            raw_alpha[group] > 20.0f
                ? raw_alpha[group]
                : log1pf(expf(raw_alpha[group]));
        delta_threshold[group] =
            raw_threshold[group] > 20.0f
                ? raw_threshold[group]
                : log1pf(expf(raw_threshold[group]));
    }
    result = 1;

cleanup:
    cudaFree(d_weight);
    cudaFree(d_calibration);
    cudaFree(d_mu0);
    cudaFree(d_alpha0);
    cudaFree(d_raw_mu);
    cudaFree(d_raw_alpha);
    cudaFree(d_raw_threshold);
    cudaFree(d_m_mu);
    cudaFree(d_m_alpha);
    cudaFree(d_m_threshold);
    cudaFree(d_v_mu);
    cudaFree(d_v_alpha);
    cudaFree(d_v_threshold);
    cudaFree(d_delta_mu);
    cudaFree(d_alpha);
    cudaFree(d_mu);
    cudaFree(d_threshold);
    cudaFree(d_sig_alpha);
    cudaFree(d_sig_threshold);
    cudaFree(d_quantized);
    cudaFree(d_gradient);
    cudaFree(d_errors);
    cudaFree(d_mu_grad);
    cudaFree(d_alpha_grad);
    cudaFree(d_threshold_grad);
    return result;

#undef CUDA_ALLOC
#undef CUDA_ZERO
}
