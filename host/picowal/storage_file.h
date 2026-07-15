#ifndef STORAGE_FILE_H
#define STORAGE_FILE_H
#include "picovm.h"

int pwf_storage_open(const char *path);
void pwf_storage_close(void);
int pv_storage_file_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#endif
