#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "picobrotli.h"

/* A valid compressed "abc" * 64 stream, not the upstream CVE reproducer.
 * WBITS=16 and a final 4-nibble-length block put NBLTYPESL/I/D at bits 21-23.
 * Rejecting each unsupported block count must remain a permanent error,
 * including when more than Google's vulnerable eight-byte buffer is supplied.
 */
static const uint8_t valid[] = {
    0xe2, 0x17, 0x00, 0x00, 0x64, 0x98, 0xd8,
    0x58, 0x68, 0x16, 0xd1, 0xad, 0x01
};

static int check_valid(void)
{
    uint8_t output[256];
    memset(output, 0xa5, sizeof(output));
    if (brotli_decode(valid, sizeof(valid), output + 32, 192) != 192)
        return 1;
    for (size_t i = 0; i < sizeof(output); i++) {
        uint8_t expected = (i >= 32 && i < 224)
            ? (uint8_t)("abc"[(i - 32) % 3]) : 0xa5;
        if (output[i] != expected) return 1;
    }
    return 0;
}

int main(void)
{
    static const size_t lengths[] = {
        0, 1, 2, 3, 8, 9, sizeof(valid), sizeof(valid) + 1,
        sizeof(valid) + 8, sizeof(valid) + 9,
        sizeof(valid) + 64, sizeof(valid) + 4096
    };
    if (check_valid()) {
        fprintf(stderr, "CVE-2026-111497: valid control failed\n");
        return 1;
    }
    for (unsigned field = 0; field < 3; field++) {
        uint8_t input[sizeof(valid) + 4096];
        memset(input, 0x5a, sizeof(input));
        memcpy(input, valid, sizeof(valid));
        input[2] |= (uint8_t)(1u << (5 + field));
        for (size_t n = 0; n < sizeof(lengths) / sizeof(lengths[0]); n++) {
            uint8_t output[256];
            memset(output, 0xa5, sizeof(output));
            if (brotli_decode(input, lengths[n], output + 32, 192) != -1) {
                fprintf(stderr, "block field %u accepted at length %zu\n",
                        field, lengths[n]);
                return 1;
            }
            for (size_t i = 0; i < sizeof(output); i++) {
                if (output[i] != 0xa5) {
                    fprintf(stderr, "block field %u wrote output at length %zu\n",
                            field, lengths[n]);
                    return 1;
                }
            }
        }
    }
    if (check_valid()) {
        fprintf(stderr, "decoder did not recover after rejected input\n");
        return 1;
    }
    puts("PASS CVE-2026-111497 block-type rejection and output guards");
    return 0;
}
