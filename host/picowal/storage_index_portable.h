#ifndef STORAGE_INDEX_PORTABLE_H
#define STORAGE_INDEX_PORTABLE_H

#include "picovm.h"

/* Initializes the bounded derived-index arena. The implementation is the
 * vendored PicoWAL portable picowal_index.c, not a second search engine. */
void pwf_portable_indexes_init(void);
int pwf_portable_index_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);
int pwf_portable_storage_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#endif
