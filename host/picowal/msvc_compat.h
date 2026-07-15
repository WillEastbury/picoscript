/* msvc_compat.h -- forced include (via /FI) to let MSVC compile GCC-flavoured
 * PicoScript VM sources (picobrotli.c uses __attribute__((cold))). Only used
 * for the MSVC build of the host server; gcc/clang builds don't need it. */
#ifndef PICOWAL_MSVC_COMPAT_H
#define PICOWAL_MSVC_COMPAT_H
#if defined(_MSC_VER) && !defined(__clang__)
#define __attribute__(x)
#endif
#endif
