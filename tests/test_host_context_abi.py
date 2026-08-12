import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c
from picoscript_il import lower_to_bytecode_safe
from picoscript_vm import HostApi, PicoVM


def test_python_context_provider_returns_bounded_value():
    def context(namespace, method, a, b, *, vm, host):
        if method == "GetTraceId":
            return b"trace-123"
        return (5, b"")

    vm = PicoVM(host=HostApi(context_provider=context)).run(
        lower_to_bytecode_safe(
            compile_c("int trace = Context.GetTraceId(); Io.Write(trace);")
        )
    )
    assert b"".join(vm.output) == b"trace-123"


def test_python_provider_can_redact_sensitive_context():
    def context(namespace, method, a, b, *, vm, host):
        return (12, b"")

    host = HostApi(context_provider=context)
    vm = PicoVM(host=host).run(
        lower_to_bytecode_safe(
            compile_c("int user = Context.GetUser(); Io.WriteByte(Status.Last());")
        )
    )
    assert b"".join(vm.output) == bytes([12])
