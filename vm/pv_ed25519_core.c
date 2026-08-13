/* VM-local imported Ed25519 core from picoweb userspace modules.
 * Kept separate from the TLS core because sha512.c and sha256.c use
 * overlapping private static symbol names when amalgamated.
 */

#include "picotls/crypto/sha512.c"
#include "picotls/crypto/ed25519.c"
