#ifndef STORAGE_FILE_H
#define STORAGE_FILE_H
#include "picovm.h"
#include <stdint.h>

int pwf_storage_open(const char *path);
void pwf_storage_close(void);
int pwf_storage_sync(void);
int pwf_storage_recover(void);
uint64_t pwf_storage_recovery_offset(void);
int pv_storage_file_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#endif
