"""Public Block.* compiler and reference-VM conformance."""

from picoscript_lang import Compiler, HOST_HOOK_CODES, disassemble
from picoscript_vm import HostApi, PicoVM


def test_block_hook_abi_and_disassembly():
    source = "\n".join((
        "Block.SetOffset(R0, R1)",
        "Block.Read(R2, R3)",
        "Block.Status(R4)",
    ))
    words = Compiler().compile(source)
    assert [word & 0xFFF for word in words] == [
        HOST_HOOK_CODES[("Block", "SetOffset")],
        HOST_HOOK_CODES[("Block", "Read")],
        HOST_HOOK_CODES[("Block", "Status")],
    ]
    assert "Block.SetOffset(R0, R1);" in disassemble(words)
    assert "Block.Read(R2, R3);" in disassemble(words)


def test_block_reference_range_and_lba_io():
    host = HostApi()
    host.block_data[8:12] = b"WAL!"
    words = Compiler().compile("\n".join((
        "Block.SetOffset(R0, R1)",
        "Block.Read(R2, R3)",
        "Block.Status(R4)",
        "Block.SetLba(R5, R1)",
        "Block.ReadBlocks(R6, R7)",
    )))
    vm = PicoVM(host=host)
    vm.load(words)
    vm.regs[0] = 8
    vm.regs[2] = 4
    vm.regs[5] = 0
    vm.regs[6] = 1
    vm.run()
    assert host._span_raw(vm, vm.regs[3]) == b"WAL!"
    assert vm.regs[4] == 0
    assert len(host._span_raw(vm, vm.regs[7])) == 512


def test_block_reference_rejects_oversized_transfer():
    host = HostApi()
    words = Compiler().compile("Block.Read(R0, R1)\nBlock.Status(R2)")
    vm = PicoVM(host=host)
    vm.load(words)
    vm.regs[0] = 16385
    vm.run()
    assert host._span_raw(vm, vm.regs[1]) == b""
    assert vm.regs[2] == 0xFFFFFFFE
