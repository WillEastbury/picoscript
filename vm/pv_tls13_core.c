/* VM-local imported TLS core from picoweb userspace modules.
 * Limited to the SHA-256 / HKDF / ChaCha20-Poly1305 / TLS record-keyschedule
 * subset so it builds cleanly on MSVC.
 */

#include "picotls/crypto/util.c"
#include "picotls/crypto/pw_cpuid.c"
#include "picotls/crypto/sha256.c"
#include "picotls/crypto/hmac.c"
#include "picotls/crypto/hkdf.c"
#include "picotls/crypto/poly1305.c"
#include "picotls/crypto/chacha20.c"
#include "picotls/crypto/chacha20_poly1305.c"
#include "picotls/tls/keysched.c"
#include "picotls/tls/record.c"
