#include "picovm_catq.h"
#include "pico_hooks.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PV_CATQ_MAX_OBJECTS 256
#define PV_CATQ_HANDLE_BASE 10000
#define PV_CATQ_MAX_DIMS 4
#define PV_CATQ_OPTION_BYTES 512

enum {
    PV_CATQ_NONE = 0,
    PV_CATQ_TENSOR,
    PV_CATQ_CONTEXT,
    PV_CATQ_OPTIMIZED,
    PV_CATQ_TERNARY,
    PV_CATQ_PACKED,
    PV_CATQ_SHARD,
    PV_CATQ_JOB
};

typedef struct {
    float *data;
    size_t count;
    int rows;
    int cols;
} pv_catq_tensor;

typedef struct {
    int calibration;
    int epochs;
    int group_size;
    int batch_size;
    float gamma;
    float sharpness;
    float learning_rate;
    float weight_decay;
} pv_catq_context;

typedef struct {
    float *weight;
    size_t count;
    int rows;
    int cols;
    int group_size;
    size_t groups;
    float *delta_mu;
    float *delta_alpha;
    float *delta_threshold;
    float final_loss;
} pv_catq_optimized;

typedef struct {
    int8_t *codes;
    float *scales;
    size_t count;
    size_t groups;
    int rows;
    int cols;
    int group_size;
} pv_catq_ternary;

typedef struct {
    uint8_t *codes;
    float *scales;
    size_t codes_len;
    size_t count;
    size_t groups;
    int rows;
    int cols;
    int group_size;
} pv_catq_packed;

typedef struct {
    char *path;
} pv_catq_shard;

typedef struct {
    int result;
} pv_catq_job;

typedef struct {
    int type;
    void *value;
} pv_catq_object;

static pv_catq_object pv_catq_objects[PV_CATQ_MAX_OBJECTS];

static char *pv_catq_strdup(const char *s)
{
    size_t n = strlen(s) + 1;
    char *out = (char *)malloc(n);
    if (out) memcpy(out, s, n);
    return out;
}

static int pv_catq_index(int handle)
{
    int index = handle - PV_CATQ_HANDLE_BASE;
    return index >= 0 && index < PV_CATQ_MAX_OBJECTS ? index : -1;
}

static pv_catq_object *pv_catq_object_get(int handle, int type)
{
    int index = pv_catq_index(handle);
    if (index < 0 || pv_catq_objects[index].type != type) return 0;
    return &pv_catq_objects[index];
}

static int pv_catq_object_put(int type, void *value)
{
    int i;
    for (i = 0; i < PV_CATQ_MAX_OBJECTS; i++) {
        if (pv_catq_objects[i].type == PV_CATQ_NONE) {
            pv_catq_objects[i].type = type;
            pv_catq_objects[i].value = value;
            return PV_CATQ_HANDLE_BASE + i;
        }
    }
    return 0;
}

static void pv_catq_free_object(pv_catq_object *object)
{
    if (!object || !object->value) return;
    if (object->type == PV_CATQ_TENSOR) {
        pv_catq_tensor *v = (pv_catq_tensor *)object->value;
        free(v->data);
    } else if (object->type == PV_CATQ_OPTIMIZED) {
        pv_catq_optimized *v = (pv_catq_optimized *)object->value;
        free(v->weight);
        free(v->delta_mu);
        free(v->delta_alpha);
        free(v->delta_threshold);
    } else if (object->type == PV_CATQ_TERNARY) {
        pv_catq_ternary *v = (pv_catq_ternary *)object->value;
        free(v->codes);
        free(v->scales);
    } else if (object->type == PV_CATQ_PACKED) {
        pv_catq_packed *v = (pv_catq_packed *)object->value;
        free(v->codes);
        free(v->scales);
    } else if (object->type == PV_CATQ_SHARD) {
        pv_catq_shard *v = (pv_catq_shard *)object->value;
        free(v->path);
    }
    free(object->value);
    object->value = 0;
    object->type = PV_CATQ_NONE;
}

void pv_catq_cleanup(void)
{
    int i;
    for (i = 0; i < PV_CATQ_MAX_OBJECTS; i++)
        pv_catq_free_object(&pv_catq_objects[i]);
}

static int pv_catq_span(pv_ctx *ctx, int handle, const uint8_t **ptr, int32_t *len)
{
    uint32_t offset;
    int32_t size;
    if (!ctx || !ctx->mem || !ptr || !len ||
        handle <= 0 || handle >= ctx->span_count)
        return 0;
    offset = ctx->span_ptr[handle];
    size = ctx->span_len[handle];
    if (size < 0 || offset > (uint32_t)ctx->mem_size ||
        (uint32_t)size > (uint32_t)ctx->mem_size - offset)
        return 0;
    *ptr = ctx->mem + offset;
    *len = size;
    return 1;
}

static int pv_catq_span_text(pv_ctx *ctx, int handle, char *out, size_t cap)
{
    const uint8_t *ptr;
    int32_t len;
    size_t n;
    if (!out || cap == 0 || !pv_catq_span(ctx, handle, &ptr, &len)) {
        if (out && cap) out[0] = '\0';
        return 0;
    }
    n = (size_t)len < cap - 1 ? (size_t)len : cap - 1;
    memcpy(out, ptr, n);
    out[n] = '\0';
    return (int)n;
}

static int pv_catq_option(const char *options, const char *key, char *out, size_t cap)
{
    size_t key_len = strlen(key);
    const char *p = options;
    while (p && *p) {
        const char *end = strchr(p, ';');
        const char *eq = strchr(p, '=');
        size_t part_len = end ? (size_t)(end - p) : strlen(p);
        if (eq && eq < p + part_len && (size_t)(eq - p) == key_len &&
            strncmp(p, key, key_len) == 0) {
            size_t n = part_len - key_len - 1;
            if (n >= cap) n = cap - 1;
            memcpy(out, eq + 1, n);
            out[n] = '\0';
            return 1;
        }
        p = end ? end + 1 : 0;
    }
    if (cap) out[0] = '\0';
    return 0;
}

static int pv_catq_option_int(const char *options, const char *key, int fallback)
{
    char value[64];
    return pv_catq_option(options, key, value, sizeof(value)) ? atoi(value) : fallback;
}

static float pv_catq_option_float(const char *options, const char *key, float fallback)
{
    char value[64];
    return pv_catq_option(options, key, value, sizeof(value))
        ? (float)strtod(value, 0) : fallback;
}

static float pv_catq_bf16(uint16_t value)
{
    uint32_t bits = (uint32_t)value << 16;
    float out;
    memcpy(&out, &bits, sizeof(out));
    return out;
}

static float pv_catq_f16(uint16_t value)
{
    uint32_t sign = (uint32_t)(value & 0x8000) << 16;
    uint32_t exp = (value >> 10) & 0x1f;
    uint32_t frac = value & 0x3ff;
    uint32_t bits;
    float out;
    if (exp == 0) {
        if (frac == 0) {
            bits = sign;
        } else {
            int shift = 0;
            while ((frac & 0x400) == 0) { frac <<= 1; shift++; }
            frac &= 0x3ff;
            bits = sign | (uint32_t)(127 - 15 - shift) << 23 | frac << 13;
        }
    } else if (exp == 31) {
        bits = sign | 0x7f800000u | frac << 13;
    } else {
        bits = sign | (exp + 112) << 23 | frac << 13;
    }
    memcpy(&out, &bits, sizeof(out));
    return out;
}

static pv_catq_tensor *pv_catq_tensor_new(size_t count, int rows, int cols)
{
    pv_catq_tensor *tensor = (pv_catq_tensor *)calloc(1, sizeof(*tensor));
    if (!tensor) return 0;
    tensor->data = (float *)calloc(count ? count : 1, sizeof(float));
    if (!tensor->data) { free(tensor); return 0; }
    tensor->count = count;
    tensor->rows = rows;
    tensor->cols = cols;
    return tensor;
}

int pv_catq_register_f32(const float *values, int rows, int cols)
{
    size_t count = (size_t)rows * (size_t)cols;
    pv_catq_tensor *tensor;
    if (!values || rows <= 0 || cols <= 0) return 0;
    tensor = pv_catq_tensor_new(count, rows, cols);
    if (!tensor) return 0;
    memcpy(tensor->data, values, count * sizeof(float));
    return pv_catq_object_put(PV_CATQ_TENSOR, tensor);
}

static pv_catq_tensor *pv_catq_tensor_get(int handle)
{
    pv_catq_object *object = pv_catq_object_get(handle, PV_CATQ_TENSOR);
    return object ? (pv_catq_tensor *)object->value : 0;
}

int pv_catq_copy_f32(int tensor_handle, float *out, size_t count)
{
    pv_catq_tensor *tensor = pv_catq_tensor_get(tensor_handle);
    if (!tensor || !out || count < tensor->count) return 0;
    memcpy(out, tensor->data, tensor->count * sizeof(float));
    return (int)tensor->count;
}

static uint64_t pv_catq_u64le(const uint8_t *p)
{
    uint64_t value = 0;
    int i;
    for (i = 7; i >= 0; i--) value = (value << 8) | p[i];
    return value;
}

static void pv_catq_put_u64le(uint8_t *p, uint64_t value)
{
    int i;
    for (i = 0; i < 8; i++) { p[i] = (uint8_t)value; value >>= 8; }
}

static const char *pv_catq_json_key(const char *start, const char *end, const char *key)
{
    char needle[256];
    const char *p;
    size_t n = strlen(key);
    if (n + 3 >= sizeof(needle)) return 0;
    needle[0] = '"';
    memcpy(needle + 1, key, n);
    needle[n + 1] = '"';
    needle[n + 2] = '\0';
    p = strstr(start, needle);
    return p && p < end ? p : 0;
}

static int pv_catq_json_string(const char *start, const char *end, const char *key,
                               char *out, size_t cap)
{
    const char *p = pv_catq_json_key(start, end, key);
    const char *q;
    size_t n;
    if (!p || !(p = strchr(p, ':')) || p >= end || !(p = strchr(p, '"')) || p >= end)
        return 0;
    q = strchr(++p, '"');
    if (!q || q > end) return 0;
    n = (size_t)(q - p);
    if (n >= cap) n = cap - 1;
    memcpy(out, p, n);
    out[n] = '\0';
    return 1;
}

static int pv_catq_json_numbers(const char *start, const char *end, const char *key,
                                uint64_t *out, int max_values)
{
    const char *p = pv_catq_json_key(start, end, key);
    int count = 0;
    if (!p || !(p = strchr(p, '[')) || p >= end) return 0;
    p++;
    while (p < end && *p != ']' && count < max_values) {
        char *next;
        while (p < end && (*p == ' ' || *p == ',')) p++;
        if (p >= end || *p == ']') break;
        out[count++] = (uint64_t)strtoull(p, &next, 10);
        p = next;
    }
    return count;
}

typedef struct {
    uint8_t *data;
    size_t bytes;
    uint64_t shape[PV_CATQ_MAX_DIMS];
    int dims;
    char dtype[16];
} pv_catq_raw_tensor;

static void pv_catq_raw_free(pv_catq_raw_tensor *tensor)
{
    free(tensor->data);
    memset(tensor, 0, sizeof(*tensor));
}

static int pv_catq_load_raw_safetensor(const char *path, const char *name,
                                       pv_catq_raw_tensor *out)
{
    FILE *file = fopen(path, "rb");
    uint8_t len_bytes[8];
    uint64_t header_len, offsets[2];
    char *header = 0;
    const char *entry, *entry_end;
    int ok = 0;
    memset(out, 0, sizeof(*out));
    if (!file || fread(len_bytes, 1, 8, file) != 8) goto done;
    header_len = pv_catq_u64le(len_bytes);
    if (header_len == 0 || header_len > 64u * 1024u * 1024u) goto done;
    header = (char *)malloc((size_t)header_len + 1);
    if (!header || fread(header, 1, (size_t)header_len, file) != (size_t)header_len) goto done;
    header[header_len] = '\0';
    entry = pv_catq_json_key(header, header + header_len, name);
    if (!entry || !(entry = strchr(entry, '{'))) goto done;
    entry_end = strchr(entry, '}');
    if (!entry_end ||
        !pv_catq_json_string(entry, entry_end, "dtype", out->dtype, sizeof(out->dtype)) ||
        pv_catq_json_numbers(entry, entry_end, "data_offsets", offsets, 2) != 2)
        goto done;
    out->dims = pv_catq_json_numbers(
        entry, entry_end, "shape", out->shape, PV_CATQ_MAX_DIMS);
    if (out->dims <= 0) goto done;
    out->bytes = (size_t)(offsets[1] - offsets[0]);
    out->data = (uint8_t *)malloc(out->bytes ? out->bytes : 1);
    if (!out->data ||
        fseek(file, (long)(8 + header_len + offsets[0]), SEEK_SET) != 0 ||
        fread(out->data, 1, out->bytes, file) != out->bytes)
        goto done;
    ok = 1;
done:
    if (!ok) pv_catq_raw_free(out);
    free(header);
    if (file) fclose(file);
    return ok;
}

static pv_catq_tensor *pv_catq_load_safetensor(const char *path, const char *name)
{
    pv_catq_raw_tensor raw;
    pv_catq_tensor *tensor = 0;
    size_t count = 1, i;
    int rows, cols;
    if (!pv_catq_load_raw_safetensor(path, name, &raw)) return 0;
    for (i = 0; i < (size_t)raw.dims; i++) count *= (size_t)raw.shape[i];
    cols = (int)raw.shape[raw.dims - 1];
    rows = cols > 0 ? (int)(count / (size_t)cols) : 0;
    tensor = pv_catq_tensor_new(count, rows, cols);
    if (!tensor) goto done;
    if (strcmp(raw.dtype, "F32") == 0 && raw.bytes >= count * 4) {
        memcpy(tensor->data, raw.data, count * 4);
    } else if (strcmp(raw.dtype, "BF16") == 0 && raw.bytes >= count * 2) {
        for (i = 0; i < count; i++)
            tensor->data[i] = pv_catq_bf16(
                (uint16_t)(raw.data[i * 2] | raw.data[i * 2 + 1] << 8));
    } else if (strcmp(raw.dtype, "F16") == 0 && raw.bytes >= count * 2) {
        for (i = 0; i < count; i++)
            tensor->data[i] = pv_catq_f16(
                (uint16_t)(raw.data[i * 2] | raw.data[i * 2 + 1] << 8));
    } else {
        free(tensor->data); free(tensor); tensor = 0;
    }
done:
    pv_catq_raw_free(&raw);
    return tensor;
}

static pv_catq_tensor *pv_catq_load_mxfp4(const char *path,
                                          const char *blocks_name,
                                          const char *scales_name)
{
    static const float fp4[16] = {
        0.0f, 0.5f, 1.0f, 1.5f, 2.0f, 3.0f, 4.0f, 6.0f,
        -0.0f, -0.5f, -1.0f, -1.5f, -2.0f, -3.0f, -4.0f, -6.0f
    };
    pv_catq_raw_tensor blocks, scales;
    pv_catq_tensor *tensor = 0;
    size_t rows = 1, scale_count = 1, row, group, byte;
    size_t groups, bytes_per_group, cols;
    if (!pv_catq_load_raw_safetensor(path, blocks_name, &blocks)) return 0;
    if (!pv_catq_load_raw_safetensor(path, scales_name, &scales)) {
        pv_catq_raw_free(&blocks);
        return 0;
    }
    if (strcmp(blocks.dtype, "U8") != 0 || strcmp(scales.dtype, "U8") != 0 ||
        blocks.dims < 2 || scales.dims != blocks.dims - 1)
        goto done;
    groups = (size_t)blocks.shape[blocks.dims - 2];
    bytes_per_group = (size_t)blocks.shape[blocks.dims - 1];
    cols = groups * bytes_per_group * 2;
    for (row = 0; row < (size_t)blocks.dims - 2; row++)
        rows *= (size_t)blocks.shape[row];
    for (row = 0; row < (size_t)scales.dims; row++)
        scale_count *= (size_t)scales.shape[row];
    if (scale_count != rows * groups ||
        blocks.bytes < rows * groups * bytes_per_group ||
        scales.bytes < scale_count)
        goto done;
    tensor = pv_catq_tensor_new(rows * cols, (int)rows, (int)cols);
    if (!tensor) goto done;
    for (row = 0; row < rows; row++) {
        for (group = 0; group < groups; group++) {
            int exponent = (int)scales.data[row * groups + group] - 127;
            size_t block_base = (row * groups + group) * bytes_per_group;
            size_t output_base = row * cols + group * bytes_per_group * 2;
            for (byte = 0; byte < bytes_per_group; byte++) {
                uint8_t packed = blocks.data[block_base + byte];
                tensor->data[output_base + byte * 2] =
                    ldexpf(fp4[packed & 0x0f], exponent);
                tensor->data[output_base + byte * 2 + 1] =
                    ldexpf(fp4[packed >> 4], exponent);
            }
        }
    }
done:
    pv_catq_raw_free(&blocks);
    pv_catq_raw_free(&scales);
    return tensor;
}

static pv_catq_packed *pv_catq_load_packed(const char *path)
{
    FILE *file = fopen(path, "rb");
    uint8_t len_bytes[8];
    uint64_t header_len;
    char *header = 0, shape_text[128], group_text[32];
    pv_catq_raw_tensor codes, scales;
    pv_catq_packed *packed = 0;
    char *comma;
    int rows, cols, group_size;
    size_t count;
    memset(&codes, 0, sizeof(codes));
    memset(&scales, 0, sizeof(scales));
    if (!file || fread(len_bytes, 1, 8, file) != 8) goto done;
    header_len = pv_catq_u64le(len_bytes);
    if (header_len == 0 || header_len > 64u * 1024u * 1024u) goto done;
    header = (char *)malloc((size_t)header_len + 1);
    if (!header || fread(header, 1, (size_t)header_len, file) != (size_t)header_len)
        goto done;
    header[header_len] = '\0';
    if (!pv_catq_json_string(
            header, header + header_len, "shape", shape_text, sizeof(shape_text)) ||
        !pv_catq_json_string(
            header, header + header_len, "group_size", group_text, sizeof(group_text)))
        goto done;
    comma = strchr(shape_text, ',');
    if (!comma) goto done;
    *comma = '\0';
    rows = atoi(shape_text);
    cols = atoi(comma + 1);
    group_size = atoi(group_text);
    if (rows <= 0 || cols <= 0 || group_size <= 0) goto done;
    fclose(file);
    file = 0;
    if (!pv_catq_load_raw_safetensor(path, "codes", &codes) ||
        !pv_catq_load_raw_safetensor(path, "scales", &scales) ||
        strcmp(codes.dtype, "U8") != 0 || strcmp(scales.dtype, "F32") != 0)
        goto done;
    count = (size_t)rows * cols;
    if (codes.bytes < (count + 3) / 4 ||
        scales.bytes < ((count + (size_t)group_size - 1) / (size_t)group_size) * 4)
        goto done;
    packed = (pv_catq_packed *)calloc(1, sizeof(*packed));
    if (!packed) goto done;
    packed->codes_len = (count + 3) / 4;
    packed->groups = (count + (size_t)group_size - 1) / (size_t)group_size;
    packed->codes = (uint8_t *)malloc(packed->codes_len);
    packed->scales = (float *)malloc(packed->groups * sizeof(float));
    if (!packed->codes || !packed->scales) {
        free(packed->codes); free(packed->scales); free(packed); packed = 0;
        goto done;
    }
    memcpy(packed->codes, codes.data, packed->codes_len);
    memcpy(packed->scales, scales.data, packed->groups * sizeof(float));
    packed->count = count;
    packed->rows = rows;
    packed->cols = cols;
    packed->group_size = group_size;
done:
    pv_catq_raw_free(&codes);
    pv_catq_raw_free(&scales);
    free(header);
    if (file) fclose(file);
    return packed;
}

static int pv_catq_write_packed(const char *path, const pv_catq_packed *packed)
{
    FILE *file;
    char header[2048], shape[128];
    size_t pos = 0, header_len, scales_bytes = packed->groups * sizeof(float);
    uint8_t len_bytes[8];
    (void)snprintf(shape, sizeof(shape), "%d,%d", packed->rows, packed->cols);
    pos = (size_t)snprintf(
        header, sizeof(header),
        "{\"__metadata__\":{\"format\":\"picoscript-catq-ternary-v1\","
        "\"shape\":\"%s\",\"group_size\":\"%d\"},"
        "\"codes\":{\"dtype\":\"U8\",\"shape\":[%llu],\"data_offsets\":[0,%llu]},"
        "\"scales\":{\"dtype\":\"F32\",\"shape\":[%llu],\"data_offsets\":[%llu,%llu]}}",
        shape, packed->group_size,
        (unsigned long long)packed->codes_len,
        (unsigned long long)packed->codes_len,
        (unsigned long long)packed->groups,
        (unsigned long long)packed->codes_len,
        (unsigned long long)(packed->codes_len + scales_bytes));
    if (pos >= sizeof(header)) return 0;
    while (pos % 8) header[pos++] = ' ';
    header[pos] = '\0';
    header_len = pos;
    file = fopen(path, "wb");
    if (!file) return 0;
    pv_catq_put_u64le(len_bytes, header_len);
    if (fwrite(len_bytes, 1, 8, file) != 8 ||
        fwrite(header, 1, header_len, file) != header_len ||
        fwrite(packed->codes, 1, packed->codes_len, file) != packed->codes_len ||
        fwrite(packed->scales, 1, scales_bytes, file) != scales_bytes) {
        fclose(file);
        return 0;
    }
    fclose(file);
    return 1;
}

static int pv_catq_write_tensor(const char *path, const pv_catq_tensor *tensor)
{
    FILE *file;
    char header[1024];
    size_t pos, header_len, bytes = tensor->count * sizeof(float);
    uint8_t len_bytes[8];
    pos = (size_t)snprintf(
        header, sizeof(header),
        "{\"tensor\":{\"dtype\":\"F32\",\"shape\":[%d,%d],\"data_offsets\":[0,%llu]}}",
        tensor->rows, tensor->cols, (unsigned long long)bytes);
    if (pos >= sizeof(header)) return 0;
    while (pos % 8) header[pos++] = ' ';
    header[pos] = '\0';
    header_len = pos;
    file = fopen(path, "wb");
    if (!file) return 0;
    pv_catq_put_u64le(len_bytes, header_len);
    if (fwrite(len_bytes, 1, 8, file) != 8 ||
        fwrite(header, 1, header_len, file) != header_len ||
        fwrite(tensor->data, 1, bytes, file) != bytes) {
        fclose(file);
        return 0;
    }
    fclose(file);
    return 1;
}

static float pv_catq_softplus(float x)
{
    if (x > 20.0f) return x;
    if (x < -20.0f) return expf(x);
    return log1pf(expf(x));
}

static float pv_catq_sigmoid(float x)
{
    if (x >= 0.0f) {
        float z = expf(-x);
        return 1.0f / (1.0f + z);
    }
    {
        float z = expf(x);
        return z / (1.0f + z);
    }
}

static float pv_catq_soft_value(float z, float sharpness, float threshold,
                                float *d_z, float *d_threshold)
{
    float a = sharpness * (z - threshold);
    float b = sharpness * (z + threshold);
    float ta = tanhf(a), tb = tanhf(b);
    float denom = 2.0f * tanhf(sharpness);
    float sa = 1.0f - ta * ta, sb = 1.0f - tb * tb;
    if (fabsf(denom) < 1e-12f) denom = denom < 0 ? -1e-12f : 1e-12f;
    if (d_z) *d_z = sharpness * (sa + sb) / denom;
    if (d_threshold) *d_threshold = sharpness * (-sa + sb) / denom;
    return (ta + tb) / denom;
}

static float pv_catq_hard_value(float z, float threshold)
{
    if (z > threshold) return 1.0f;
    if (z < -threshold) return -1.0f;
    return 0.0f;
}

static pv_catq_optimized *pv_catq_optimize(const pv_catq_context *context,
                                           const pv_catq_tensor *weight,
                                           const pv_catq_tensor *calibration)
{
    size_t count = weight->count;
    size_t groups = (count + (size_t)context->group_size - 1) / (size_t)context->group_size;
    pv_catq_optimized *out = (pv_catq_optimized *)calloc(1, sizeof(*out));
    float *mu0 = 0, *alpha0 = 0, *raw_mu = 0, *raw_alpha = 0, *raw_threshold = 0;
    float *m_mu = 0, *m_alpha = 0, *m_threshold = 0;
    float *v_mu = 0, *v_alpha = 0, *v_threshold = 0;
    float *quantized = 0, *gradient = 0;
    int epoch, batch_start, step = 0;
    int total_steps;
    size_t g, i;
    if (!out || weight->cols != calibration->cols) goto fail;
    out->weight = (float *)malloc(count * sizeof(float));
    out->delta_mu = (float *)malloc(groups * sizeof(float));
    out->delta_alpha = (float *)malloc(groups * sizeof(float));
    out->delta_threshold = (float *)malloc(groups * sizeof(float));
    mu0 = (float *)calloc(groups, sizeof(float));
    alpha0 = (float *)calloc(groups, sizeof(float));
    raw_mu = (float *)calloc(groups, sizeof(float));
    raw_alpha = (float *)malloc(groups * sizeof(float));
    raw_threshold = (float *)malloc(groups * sizeof(float));
    m_mu = (float *)calloc(groups, sizeof(float));
    m_alpha = (float *)calloc(groups, sizeof(float));
    m_threshold = (float *)calloc(groups, sizeof(float));
    v_mu = (float *)calloc(groups, sizeof(float));
    v_alpha = (float *)calloc(groups, sizeof(float));
    v_threshold = (float *)calloc(groups, sizeof(float));
    quantized = (float *)malloc(count * sizeof(float));
    gradient = (float *)malloc(count * sizeof(float));
    if (!out->weight || !out->delta_mu || !out->delta_alpha || !out->delta_threshold ||
        !mu0 || !alpha0 || !raw_mu || !raw_alpha || !raw_threshold ||
        !m_mu || !m_alpha || !m_threshold || !v_mu || !v_alpha || !v_threshold ||
        !quantized || !gradient)
        goto fail;
    memcpy(out->weight, weight->data, count * sizeof(float));
    out->count = count;
    out->rows = weight->rows;
    out->cols = weight->cols;
    out->group_size = context->group_size;
    out->groups = groups;
    for (g = 0; g < groups; g++) {
        size_t start = g * (size_t)context->group_size;
        size_t end = start + (size_t)context->group_size;
        size_t n;
        if (end > count) end = count;
        n = end - start;
        for (i = start; i < end; i++) mu0[g] += weight->data[i];
        mu0[g] /= (float)n;
        for (i = start; i < end; i++) alpha0[g] += fabsf(weight->data[i] - mu0[g]);
        alpha0[g] /= (float)n;
        if (alpha0[g] < 1e-8f) alpha0[g] = 1e-8f;
        raw_alpha[g] = 0.5413248546f;
        raw_threshold[g] = 0.5413248546f;
    }
    total_steps = context->epochs *
        ((calibration->rows + context->batch_size - 1) / context->batch_size);
    if (total_steps < 1) total_steps = 1;
    for (epoch = 0; epoch < context->epochs; epoch++) {
        float t = (float)(epoch + 1) / (float)context->epochs;
        for (batch_start = 0; batch_start < calibration->rows;
             batch_start += context->batch_size) {
            int batch_end = batch_start + context->batch_size;
            float *group_mu_grad = (float *)calloc(groups, sizeof(float));
            float *group_alpha_grad = (float *)calloc(groups, sizeof(float));
            float *group_threshold_grad = (float *)calloc(groups, sizeof(float));
            float loss = 0.0f;
            int s, o, c;
            if (!group_mu_grad || !group_alpha_grad || !group_threshold_grad) {
                free(group_mu_grad); free(group_alpha_grad); free(group_threshold_grad);
                goto fail;
            }
            if (batch_end > calibration->rows) batch_end = calibration->rows;
            memset(gradient, 0, count * sizeof(float));
            for (i = 0; i < count; i++) {
                g = i / (size_t)context->group_size;
                {
                    float dm = tanhf(raw_mu[g]);
                    float da = pv_catq_softplus(raw_alpha[g]);
                    float dt = pv_catq_softplus(raw_threshold[g]);
                    float alpha = da * alpha0[g];
                    float z = (weight->data[i] - (mu0[g] + dm * alpha0[g])) / alpha;
                    float threshold = dt * 0.5f;
                    float dz, dth, soft, tv;
                    float sharpness = t <= context->gamma
                        ? (t / context->gamma) * context->sharpness
                        : context->sharpness;
                    if (sharpness < 1e-6f) sharpness = 1e-6f;
                    soft = pv_catq_soft_value(z, sharpness, threshold, &dz, &dth);
                    tv = t <= context->gamma ? soft : pv_catq_hard_value(z, threshold);
                    quantized[i] = alpha * tv;
                }
            }
            for (s = batch_start; s < batch_end; s++) {
                const float *x = calibration->data + (size_t)s * calibration->cols;
                for (o = 0; o < weight->rows; o++) {
                    float target = 0.0f, prediction = 0.0f;
                    size_t row = (size_t)o * weight->cols;
                    float error, scale;
                    for (c = 0; c < weight->cols; c++) {
                        target += x[c] * weight->data[row + (size_t)c];
                        prediction += x[c] * quantized[row + (size_t)c];
                    }
                    error = prediction - target;
                    loss += error * error;
                    scale = 2.0f * error /
                        (float)((batch_end - batch_start) * weight->rows);
                    for (c = 0; c < weight->cols; c++)
                        gradient[row + (size_t)c] += scale * x[c];
                }
            }
            loss /= (float)((batch_end - batch_start) * weight->rows);
            out->final_loss = loss;
            for (i = 0; i < count; i++) {
                float dm, da, dt, alpha, z, threshold, dz, dth, soft, tv;
                float d_alpha, d_mu, d_delta_mu, d_delta_alpha, d_delta_threshold;
                float sharpness;
                g = i / (size_t)context->group_size;
                dm = tanhf(raw_mu[g]);
                da = pv_catq_softplus(raw_alpha[g]);
                dt = pv_catq_softplus(raw_threshold[g]);
                alpha = da * alpha0[g];
                z = (weight->data[i] - (mu0[g] + dm * alpha0[g])) / alpha;
                threshold = dt * 0.5f;
                sharpness = t <= context->gamma
                    ? (t / context->gamma) * context->sharpness
                    : context->sharpness;
                if (sharpness < 1e-6f) sharpness = 1e-6f;
                soft = pv_catq_soft_value(z, sharpness, threshold, &dz, &dth);
                tv = t <= context->gamma ? soft : pv_catq_hard_value(z, threshold);
                d_alpha = tv - dz * z;
                d_mu = -dz;
                d_delta_mu = d_mu * alpha0[g];
                d_delta_alpha = d_alpha * alpha0[g];
                d_delta_threshold = alpha * dth * 0.5f;
                group_mu_grad[g] += gradient[i] * d_delta_mu * (1.0f - dm * dm);
                group_alpha_grad[g] += gradient[i] * d_delta_alpha * pv_catq_sigmoid(raw_alpha[g]);
                group_threshold_grad[g] += gradient[i] * d_delta_threshold * pv_catq_sigmoid(raw_threshold[g]);
            }
            step++;
            for (g = 0; g < groups; g++) {
                float lr = context->learning_rate *
                    (1.0f - (float)(step - 1) / (float)total_steps);
                float beta1_pow = powf(0.9f, (float)step);
                float beta2_pow = powf(0.999f, (float)step);
                float *params[3] = { &raw_mu[g], &raw_alpha[g], &raw_threshold[g] };
                float grads[3] = { group_mu_grad[g], group_alpha_grad[g], group_threshold_grad[g] };
                float *moments[3] = { &m_mu[g], &m_alpha[g], &m_threshold[g] };
                float *variances[3] = { &v_mu[g], &v_alpha[g], &v_threshold[g] };
                int k;
                if (lr < 0.0f) lr = 0.0f;
                for (k = 0; k < 3; k++) {
                    float mhat, vhat;
                    *moments[k] = 0.9f * *moments[k] + 0.1f * grads[k];
                    *variances[k] = 0.999f * *variances[k] + 0.001f * grads[k] * grads[k];
                    mhat = *moments[k] / (1.0f - beta1_pow);
                    vhat = *variances[k] / (1.0f - beta2_pow);
                    *params[k] -= lr *
                        (mhat / (sqrtf(vhat) + 1e-8f) + context->weight_decay * *params[k]);
                }
            }
            free(group_mu_grad);
            free(group_alpha_grad);
            free(group_threshold_grad);
        }
    }
    for (g = 0; g < groups; g++) {
        out->delta_mu[g] = tanhf(raw_mu[g]);
        out->delta_alpha[g] = pv_catq_softplus(raw_alpha[g]);
        out->delta_threshold[g] = pv_catq_softplus(raw_threshold[g]);
    }
    free(mu0); free(alpha0); free(raw_mu); free(raw_alpha); free(raw_threshold);
    free(m_mu); free(m_alpha); free(m_threshold);
    free(v_mu); free(v_alpha); free(v_threshold);
    free(quantized); free(gradient);
    return out;
fail:
    free(mu0); free(alpha0); free(raw_mu); free(raw_alpha); free(raw_threshold);
    free(m_mu); free(m_alpha); free(m_threshold);
    free(v_mu); free(v_alpha); free(v_threshold);
    free(quantized); free(gradient);
    if (out) {
        free(out->weight); free(out->delta_mu); free(out->delta_alpha); free(out->delta_threshold);
        free(out);
    }
    return 0;
}

static pv_catq_ternary *pv_catq_ternarize(const pv_catq_optimized *optimized)
{
    pv_catq_ternary *out = (pv_catq_ternary *)calloc(1, sizeof(*out));
    size_t g, i;
    if (!out) return 0;
    out->codes = (int8_t *)calloc(optimized->count ? optimized->count : 1, 1);
    out->scales = (float *)calloc(optimized->groups ? optimized->groups : 1, sizeof(float));
    if (!out->codes || !out->scales) {
        free(out->codes); free(out->scales); free(out); return 0;
    }
    out->count = optimized->count;
    out->groups = optimized->groups;
    out->rows = optimized->rows;
    out->cols = optimized->cols;
    out->group_size = optimized->group_size;
    for (g = 0; g < optimized->groups; g++) {
        size_t start = g * (size_t)optimized->group_size;
        size_t end = start + (size_t)optimized->group_size;
        size_t n;
        float mu0 = 0.0f, alpha0 = 0.0f, alpha, mu, threshold;
        if (end > optimized->count) end = optimized->count;
        n = end - start;
        for (i = start; i < end; i++) mu0 += optimized->weight[i];
        mu0 /= (float)n;
        for (i = start; i < end; i++) alpha0 += fabsf(optimized->weight[i] - mu0);
        alpha0 /= (float)n;
        if (alpha0 < 1e-8f) alpha0 = 1e-8f;
        alpha = optimized->delta_alpha[g] * alpha0;
        mu = mu0 + optimized->delta_mu[g] * alpha0;
        threshold = optimized->delta_threshold[g] * 0.5f;
        out->scales[g] = alpha;
        for (i = start; i < end; i++) {
            float z = (optimized->weight[i] - mu) / alpha;
            out->codes[i] = (int8_t)pv_catq_hard_value(z, threshold);
        }
    }
    return out;
}

static pv_catq_packed *pv_catq_pack(const pv_catq_ternary *ternary)
{
    pv_catq_packed *out = (pv_catq_packed *)calloc(1, sizeof(*out));
    size_t i;
    if (!out) return 0;
    out->codes_len = (ternary->count + 3) / 4;
    out->codes = (uint8_t *)calloc(out->codes_len ? out->codes_len : 1, 1);
    out->scales = (float *)malloc(ternary->groups * sizeof(float));
    if (!out->codes || !out->scales) {
        free(out->codes); free(out->scales); free(out); return 0;
    }
    for (i = 0; i < ternary->count; i++) {
        uint8_t code = ternary->codes[i] > 0 ? 1 : (ternary->codes[i] < 0 ? 2 : 0);
        out->codes[i / 4] |= (uint8_t)(code << ((i & 3) * 2));
    }
    memcpy(out->scales, ternary->scales, ternary->groups * sizeof(float));
    out->count = ternary->count;
    out->groups = ternary->groups;
    out->rows = ternary->rows;
    out->cols = ternary->cols;
    out->group_size = ternary->group_size;
    return out;
}

static int pv_catq_tensor_map(pv_ctx *ctx, int source, int options_handle)
{
    char options[PV_CATQ_OPTION_BYTES], value[256], scales[256];
    pv_catq_tensor *tensor = 0;
    pv_catq_object *shard_object = pv_catq_object_get(source, PV_CATQ_SHARD);
    pv_catq_span_text(ctx, options_handle, options, sizeof(options));
    if (shard_object) {
        pv_catq_shard *shard = (pv_catq_shard *)shard_object->value;
        if (pv_catq_option_int(options, "catq_packed", 0)) {
            pv_catq_packed *packed = pv_catq_load_packed(shard->path);
            return packed ? pv_catq_object_put(PV_CATQ_PACKED, packed) : 0;
        }
        if (pv_catq_option(options, "mxfp4_blocks", value, sizeof(value))) {
            if (!pv_catq_option(options, "mxfp4_scales", scales, sizeof(scales)))
                return 0;
            tensor = pv_catq_load_mxfp4(shard->path, value, scales);
        } else {
            if (!pv_catq_option(options, "tensor", value, sizeof(value))) return 0;
            tensor = pv_catq_load_safetensor(shard->path, value);
        }
    } else {
        const uint8_t *raw;
        int32_t len;
        int rows, cols, i;
        char dtype[32] = "float32";
        if (!pv_catq_span(ctx, source, &raw, &len)) return 0;
        pv_catq_option(options, "dtype", dtype, sizeof(dtype));
        rows = pv_catq_option_int(options, "rows", 1);
        cols = pv_catq_option_int(options, "cols",
            strcmp(dtype, "bf16") == 0 || strcmp(dtype, "f16") == 0 ? len / 2 : len / 4);
        if (rows <= 0 || cols <= 0) return 0;
        tensor = pv_catq_tensor_new((size_t)rows * cols, rows, cols);
        if (!tensor) return 0;
        if ((strcmp(dtype, "bf16") == 0 || strcmp(dtype, "bfloat16") == 0) &&
            len >= rows * cols * 2) {
            for (i = 0; i < rows * cols; i++)
                tensor->data[i] = pv_catq_bf16((uint16_t)(raw[i * 2] | raw[i * 2 + 1] << 8));
        } else if ((strcmp(dtype, "f16") == 0 || strcmp(dtype, "float16") == 0) &&
                   len >= rows * cols * 2) {
            for (i = 0; i < rows * cols; i++)
                tensor->data[i] = pv_catq_f16((uint16_t)(raw[i * 2] | raw[i * 2 + 1] << 8));
        } else if (len >= rows * cols * 4) {
            memcpy(tensor->data, raw, (size_t)rows * cols * 4);
        } else {
            free(tensor->data); free(tensor); return 0;
        }
    }
    return tensor ? pv_catq_object_put(PV_CATQ_TENSOR, tensor) : 0;
}

int pv_catq_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    int a = ctx->regs[rs1], b = ctx->regs[rs2];
    if (hook == PV_HOOK_TENSOR_MAP) {
        ctx->regs[rd] = pv_catq_tensor_map(ctx, a, b);
        return 1;
    }
    if (hook == PV_HOOK_TENSOR_VIEW) {
        pv_catq_tensor *source = pv_catq_tensor_get(a);
        char options[PV_CATQ_OPTION_BYTES];
        int start, count;
        pv_catq_tensor *view;
        if (!source) { ctx->regs[rd] = 0; return 1; }
        pv_catq_span_text(ctx, b, options, sizeof(options));
        start = pv_catq_option_int(options, "row_start", 0);
        count = pv_catq_option_int(options, "row_count", source->rows - start);
        if (start < 0 || count < 0 || start + count > source->rows) {
            ctx->regs[rd] = 0; return 1;
        }
        view = pv_catq_tensor_new((size_t)count * source->cols, count, source->cols);
        if (!view) { ctx->regs[rd] = 0; return 1; }
        memcpy(view->data, source->data + (size_t)start * source->cols,
               view->count * sizeof(float));
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, view);
        return 1;
    }
    if (hook == PV_HOOK_TENSOR_GEMM) {
        pv_catq_tensor *left = pv_catq_tensor_get(a), *right = pv_catq_tensor_get(b);
        pv_catq_tensor *out;
        int r, c, k;
        if (!left || !right || left->cols != right->rows) {
            ctx->regs[rd] = 0; return 1;
        }
        out = pv_catq_tensor_new((size_t)left->rows * right->cols, left->rows, right->cols);
        if (!out) { ctx->regs[rd] = 0; return 1; }
        for (r = 0; r < left->rows; r++)
            for (c = 0; c < right->cols; c++)
                for (k = 0; k < left->cols; k++)
                    out->data[(size_t)r * out->cols + c] +=
                        left->data[(size_t)r * left->cols + k] *
                        right->data[(size_t)k * right->cols + c];
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, out);
        return 1;
    }
    if (hook == PV_HOOK_TENSOR_ADD || hook == PV_HOOK_TENSOR_MUL ||
        hook == PV_HOOK_TENSOR_SWIGLU) {
        pv_catq_tensor *left = pv_catq_tensor_get(a), *right = pv_catq_tensor_get(b);
        pv_catq_tensor *out;
        size_t i;
        if (!left || !right || left->count != right->count) {
            ctx->regs[rd] = 0; return 1;
        }
        out = pv_catq_tensor_new(left->count, left->rows, left->cols);
        if (!out) { ctx->regs[rd] = 0; return 1; }
        for (i = 0; i < left->count; i++)
            out->data[i] = hook == PV_HOOK_TENSOR_ADD
                ? left->data[i] + right->data[i]
                : hook == PV_HOOK_TENSOR_SWIGLU
                    ? (left->data[i] / (1.0f + expf(-left->data[i]))) * right->data[i]
                    : left->data[i] * right->data[i];
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, out);
        return 1;
    }
    if (hook == PV_HOOK_TENSOR_RMSNORM) {
        pv_catq_tensor *input = pv_catq_tensor_get(a), *gamma = pv_catq_tensor_get(b);
        pv_catq_tensor *out;
        int row, col;
        if (!input || !gamma || gamma->count != (size_t)input->cols) {
            ctx->regs[rd] = 0; return 1;
        }
        out = pv_catq_tensor_new(input->count, input->rows, input->cols);
        if (!out) { ctx->regs[rd] = 0; return 1; }
        for (row = 0; row < input->rows; row++) {
            float sum = 0.0f;
            float inverse;
            for (col = 0; col < input->cols; col++) {
                float value = input->data[(size_t)row * input->cols + col];
                sum += value * value;
            }
            inverse = 1.0f / sqrtf(sum / (float)input->cols + 1e-6f);
            for (col = 0; col < input->cols; col++)
                out->data[(size_t)row * input->cols + col] =
                    input->data[(size_t)row * input->cols + col] *
                    inverse * gamma->data[col];
        }
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, out);
        return 1;
    }
    if (hook == PV_HOOK_TENSOR_REDUCE || hook == PV_HOOK_TENSOR_ELEMENTWISE) {
        pv_catq_tensor *source = pv_catq_tensor_get(a), *out;
        char operation[64];
        size_t i;
        if (!source) { ctx->regs[rd] = 0; return 1; }
        pv_catq_span_text(ctx, b, operation, sizeof(operation));
        if (hook == PV_HOOK_TENSOR_REDUCE) {
            float value = 0.0f;
            out = pv_catq_tensor_new(1, 1, 1);
            if (!out) { ctx->regs[rd] = 0; return 1; }
            if (strcmp(operation, "l2") == 0) {
                for (i = 0; i < source->count; i++) value += source->data[i] * source->data[i];
                value = sqrtf(value);
            } else {
                for (i = 0; i < source->count; i++)
                    value += strcmp(operation, "absmean") == 0
                        ? fabsf(source->data[i]) : source->data[i];
                if (strcmp(operation, "sum") != 0) value /= (float)source->count;
            }
            out->data[0] = value;
        } else {
            float scale = 1.0f;
            out = pv_catq_tensor_new(source->count, source->rows, source->cols);
            if (!out) { ctx->regs[rd] = 0; return 1; }
            if (strcmp(operation, "normalize") == 0) {
                scale = 0.0f;
                for (i = 0; i < source->count; i++) scale += fabsf(source->data[i]);
                scale = scale > 0.0f ? (float)source->count / scale : 1.0f;
            }
            for (i = 0; i < source->count; i++) {
                float x = source->data[i];
                out->data[i] = strcmp(operation, "abs") == 0 ? fabsf(x) :
                    strcmp(operation, "tanh") == 0 ? tanhf(x) :
                    strcmp(operation, "silu") == 0 ? x / (1.0f + expf(-x)) :
                    strcmp(operation, "neg") == 0 ? -x : x * scale;
            }
        }
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, out);
        return 1;
    }
    if (hook == PV_HOOK_CATQ_CALIBRATE) {
        pv_catq_tensor *calibration = pv_catq_tensor_get(a);
        pv_catq_context *context;
        char options[PV_CATQ_OPTION_BYTES];
        if (!calibration) { ctx->regs[rd] = 0; return 1; }
        context = (pv_catq_context *)calloc(1, sizeof(*context));
        if (!context) { ctx->regs[rd] = 0; return 1; }
        pv_catq_span_text(ctx, b, options, sizeof(options));
        context->calibration = a;
        context->epochs = pv_catq_option_int(options, "epochs", 60);
        context->group_size = pv_catq_option_int(options, "group", 128);
        context->batch_size = pv_catq_option_int(options, "batch", 3);
        context->gamma = pv_catq_option_float(options, "gamma", 0.8f);
        context->sharpness = pv_catq_option_float(options, "s0", 30.0f);
        context->learning_rate = pv_catq_option_float(options, "lr", 0.001f);
        context->weight_decay = pv_catq_option_float(options, "weight_decay", 0.01f);
        if (context->epochs < 1 || context->group_size < 1 || context->batch_size < 1 ||
            context->gamma <= 0.0f || context->gamma > 1.0f) {
            free(context); ctx->regs[rd] = 0; return 1;
        }
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_CONTEXT, context);
        return 1;
    }
    if (hook == PV_HOOK_CATQ_OPTIMIZE) {
        pv_catq_object *context_object = pv_catq_object_get(a, PV_CATQ_CONTEXT);
        pv_catq_tensor *weight = pv_catq_tensor_get(b);
        pv_catq_context *context;
        pv_catq_tensor *calibration;
        pv_catq_optimized *optimized;
        if (!context_object || !weight) { ctx->regs[rd] = 0; return 1; }
        context = (pv_catq_context *)context_object->value;
        calibration = pv_catq_tensor_get(context->calibration);
        optimized = calibration ? pv_catq_optimize(context, weight, calibration) : 0;
        ctx->regs[rd] = optimized ? pv_catq_object_put(PV_CATQ_OPTIMIZED, optimized) : 0;
        return 1;
    }
    if (hook == PV_HOOK_CATQ_TERNARIZE) {
        pv_catq_object *context_object = pv_catq_object_get(a, PV_CATQ_CONTEXT);
        pv_catq_object *optimized_object = pv_catq_object_get(b, PV_CATQ_OPTIMIZED);
        pv_catq_ternary *ternary = optimized_object
            ? pv_catq_ternarize((pv_catq_optimized *)optimized_object->value) : 0;
        ctx->regs[rd] = context_object && ternary
            ? pv_catq_object_put(PV_CATQ_TERNARY, ternary) : 0;
        return 1;
    }
    if (hook == PV_HOOK_CATQ_PACK) {
        pv_catq_object *context_object = pv_catq_object_get(a, PV_CATQ_CONTEXT);
        pv_catq_object *ternary_object = pv_catq_object_get(b, PV_CATQ_TERNARY);
        pv_catq_packed *packed = ternary_object
            ? pv_catq_pack((pv_catq_ternary *)ternary_object->value) : 0;
        ctx->regs[rd] = context_object && packed
            ? pv_catq_object_put(PV_CATQ_PACKED, packed) : 0;
        return 1;
    }
    if (hook == PV_HOOK_BITLINEAR_MATVECCATQ) {
        pv_catq_object *packed_object = pv_catq_object_get(a, PV_CATQ_PACKED);
        pv_catq_tensor *activation = pv_catq_tensor_get(b);
        pv_catq_packed *packed;
        pv_catq_tensor *output;
        int row, col;
        if (!packed_object || !activation) { ctx->regs[rd] = 0; return 1; }
        packed = (pv_catq_packed *)packed_object->value;
        if (activation->count != (size_t)packed->cols) {
            ctx->regs[rd] = 0;
            return 1;
        }
        output = pv_catq_tensor_new((size_t)packed->rows, packed->rows, 1);
        if (!output) { ctx->regs[rd] = 0; return 1; }
        for (row = 0; row < packed->rows; row++) {
            float sum = 0.0f;
            for (col = 0; col < packed->cols; col++) {
                size_t index = (size_t)row * packed->cols + col;
                uint8_t encoded = (packed->codes[index / 4] >> ((index & 3) * 2)) & 3;
                float weight = encoded == 1 ? 1.0f : (encoded == 2 ? -1.0f : 0.0f);
                float scale = packed->scales[index / (size_t)packed->group_size];
                sum += weight * scale * activation->data[col];
            }
            output->data[row] = sum;
        }
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_TENSOR, output);
        return 1;
    }
    if (hook == PV_HOOK_ASYNC_SUBMIT) {
        pv_catq_job *job = (pv_catq_job *)calloc(1, sizeof(*job));
        if (!job) { ctx->regs[rd] = 0; return 1; }
        job->result = b;
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_JOB, job);
        return 1;
    }
    if (hook == PV_HOOK_ASYNC_WAIT || hook == PV_HOOK_ASYNC_RESULT) {
        pv_catq_object *job_object = pv_catq_object_get(a, PV_CATQ_JOB);
        pv_catq_job *job = job_object ? (pv_catq_job *)job_object->value : 0;
        ctx->regs[rd] = !job ? 0 : (hook == PV_HOOK_ASYNC_WAIT ? 1 : job->result);
        return 1;
    }
    if (hook == PV_HOOK_SHARD_LOAD) {
        char path[1024];
        pv_catq_shard *shard;
        if (!pv_catq_span_text(ctx, a, path, sizeof(path))) {
            ctx->regs[rd] = 0; return 1;
        }
        {
            FILE *file = fopen(path, "rb");
            if (!file) { ctx->regs[rd] = 0; return 1; }
            fclose(file);
        }
        shard = (pv_catq_shard *)calloc(1, sizeof(*shard));
        if (!shard || !(shard->path = pv_catq_strdup(path))) {
            free(shard); ctx->regs[rd] = 0; return 1;
        }
        ctx->regs[rd] = pv_catq_object_put(PV_CATQ_SHARD, shard);
        return 1;
    }
    if (hook == PV_HOOK_SHARD_SAVE) {
        char path[1024];
        pv_catq_object *object;
        if (!pv_catq_span_text(ctx, b, path, sizeof(path))) {
            ctx->regs[rd] = 0; return 1;
        }
        object = pv_catq_object_get(a, PV_CATQ_PACKED);
        if (object) {
            ctx->regs[rd] = pv_catq_write_packed(path, (pv_catq_packed *)object->value);
            return 1;
        }
        object = pv_catq_object_get(a, PV_CATQ_TENSOR);
        ctx->regs[rd] = object
            ? pv_catq_write_tensor(path, (pv_catq_tensor *)object->value) : 0;
        return 1;
    }
    return 0;
}

int pv_catq_install(void)
{
    pv_compute_hook = pv_catq_hook;
    return 1;
}

int pv_catq_get_packed(int handle, pv_catq_packed_info *out)
{
    pv_catq_object *object = pv_catq_object_get(handle, PV_CATQ_PACKED);
    pv_catq_packed *packed;
    if (!object || !out) return 0;
    packed = (pv_catq_packed *)object->value;
    out->codes = packed->codes;
    out->codes_len = packed->codes_len;
    out->scales = packed->scales;
    out->scale_count = packed->groups;
    out->value_count = packed->count;
    out->rows = packed->rows;
    out->cols = packed->cols;
    out->group_size = packed->group_size;
    return 1;
}

float pv_catq_final_loss(int optimized_handle)
{
    pv_catq_object *object = pv_catq_object_get(optimized_handle, PV_CATQ_OPTIMIZED);
    return object ? ((pv_catq_optimized *)object->value)->final_loss : -1.0f;
}
