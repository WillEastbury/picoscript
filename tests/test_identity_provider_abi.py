import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import HostApi, PicoVM


def test_identity_provider_returns_principal_without_exposing_key_material():
    def identity(namespace, method, user, arg, *, vm, host):
        if namespace == "Auth" and method == "GetUserPermissions":
            return b"read"
        if namespace == "X509" and method == "GetKeyHandle":
            return 91
        return (1, 0)

    host = HostApi(identity_provider=identity)
    vm = PicoVM(host=host).run(lower_to_bytecode_safe(
        compile_c('int p = Auth.GetUserPermissions(0); Io.Write(p);')
    ))
    assert b"".join(vm.output) == b"read"
