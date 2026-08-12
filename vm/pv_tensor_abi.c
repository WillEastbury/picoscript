#include "picovm.h"

#include <string.h>

static uint32_t pv_tensor_u32(const uint8_t *p)
{
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static int32_t pv_tensor_i32(const uint8_t *p)
{
    return (int32_t)pv_tensor_u32(p);
}

int pv_tensor_descriptor_decode(const uint8_t *data, uint32_t length,
                                pv_tensor_descriptor *out)
{
    uint32_t pos;
    if (!data || !out || length < 16 ||
        memcmp(data, "PTEN", 4) != 0)
        return PV_TENSOR_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    out->version = data[4];
    out->dtype = data[5];
    out->rank = data[6];
    out->flags = data[7];
    if (out->version != 1 || out->rank == 0 || out->rank > 8 ||
        out->dtype < 1 || out->dtype > 6)
        return PV_TENSOR_FORMAT_MISMATCH;
    out->byte_offset = pv_tensor_u32(data + 8);
    out->byte_length = pv_tensor_u32(data + 12);
    if (length != 16u + (uint32_t)out->rank * 8u)
        return PV_TENSOR_INVALID_ARGUMENT;
    pos = 16;
    for (uint32_t i = 0; i < out->rank; i++, pos += 4) {
        out->dimensions[i] = pv_tensor_u32(data + pos);
        if (out->dimensions[i] == 0)
            return PV_TENSOR_INVALID_ARGUMENT;
    }
    for (uint32_t i = 0; i < out->rank; i++, pos += 4)
        out->strides[i] = pv_tensor_i32(data + pos);
    return PV_TENSOR_OK;
}

int pv_tensor_request_status(const pv_tensor_request *request)
{
    if (!request || request->workspace_bytes > 0x7fffffffU ||
        request->workspace_limit > 0x7fffffffU)
        return PV_TENSOR_INVALID_ARGUMENT;
    if (request->cancelled)
        return PV_TENSOR_CANCELLED;
    if (request->workspace_limit &&
        request->workspace_bytes > request->workspace_limit)
        return PV_TENSOR_WORKSPACE_EXHAUSTED;
    return PV_TENSOR_OK;
}
