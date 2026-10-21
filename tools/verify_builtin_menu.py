"""Offline execution fixture for the builtin editor. Never opens an emulator.

Optional research dependency: unicorn 2.1.4. Addresses are absolute and are
never silently remapped between releases. ``state`` is a decoded 0x61000-byte
mGBA snapshot (tools.read_state.read_state). No native function is stubbed by
default; explicitly installed stubs and BIOS services are logged separately.
"""

import struct
from collections import Counter, deque

from unicorn import (
    UC_ARCH_ARM,
    UC_HOOK_CODE,
    UC_HOOK_INTR,
    UC_HOOK_MEM_WRITE,
    UC_MODE_THUMB,
    UC_PROT_EXEC,
    UC_PROT_READ,
    Uc,
    UcError,
)
from unicorn.arm_const import (
    UC_ARM_REG_CPSR,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)


class BuiltinCPU:
    RETURN = 0x0203FFF0
    ARG_REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

    def __init__(self, rom: bytes, state: bytes):
        if len(state) != 0x61000 or not rom or len(rom) % 0x1000:
            raise ValueError("Expected page-aligned ROM and decoded mGBA 0x61000 state")
        self.uc = self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        self.rom = rom
        for address, size, data in (
            (0x02000000, 0x40000, state[0x21000:]),
            (0x03000000, 0x8000, state[0x19000:0x21000]),
            (0x04000000, 0x1000, state[0x400:0x800]),
            (0x05000000, 0x1000, state[0x800:0xC00]),
            (0x06000000, 0x20000, state[0x1000:0x19000]),
            (0x07000000, 0x1000, state[0xC00:0x1000]),
            (0x08000000, len(rom), rom),
        ):
            self.uc.mem_map(address, size)
            if data:
                self.uc.mem_write(address, data[:size])
        self.uc.mem_protect(0x08000000, len(rom), UC_PROT_READ | UC_PROT_EXEC)
        self.trace = deque(maxlen=80)
        self.visits = Counter()
        # Per-instruction native PC counts, not a count of function calls.
        self.native_calls = Counter()
        self.stubs = {}
        self.stub_calls = []
        self.bios_calls = []
        self.dma_calls = []
        self._pending_dma = set()
        self.stop = None
        self.uc.hook_add(UC_HOOK_CODE, self._code)
        self.uc.hook_add(UC_HOOK_INTR, self._interrupt)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._io_write, begin=0x040000B0, end=0x040000DF)

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def write(self, address, data):
        if address < 0x08000000 + len(self.rom) and address + len(data) > 0x08000000:
            raise ValueError("The isolated fixture cannot write to ROM")
        self.uc.mem_write(address, bytes(data))

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def w32(self, address, value):
        self.write(address, struct.pack("<I", value))

    def install_stub(self, address, name, handler=None):
        """handler(cpu, args) returns r0, or None to preserve it; return via LR."""
        self.stubs[address & ~1] = (name, handler)

    def _code(self, uc, address, size, _):
        self._flush_dma()
        self.trace.append(address)
        self.visits[address] += 1
        if address in self.stubs:
            name, handler = self.stubs[address]
            args = [uc.reg_read(r) for r in self.ARG_REGS]
            self.stub_calls.append({"address": address, "name": name, "args": args})
            result = handler(self, args) if handler else 0
            if result is not None:
                uc.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        elif 0x08000000 <= address < 0x0A000000 and not 0x08A7A024 <= address < 0x08A80000:
            self.native_calls[address] += 1

    def _io_write(self, uc, access, address, size, value, _):
        for channel in range(4):
            control = 0x040000B8 + 12 * channel
            if address <= control + 3 and address + size > control + 1:
                self._pending_dma.add(channel)

    def _flush_dma(self):
        pending, self._pending_dma = self._pending_dma, set()
        for channel in pending:
            base = 0x040000B0 + channel * 12
            source, dest, control = struct.unpack("<III", self.read(base, 12))
            if not control & 0x80000000:
                continue
            # This fixture has no HBlank/VBlank timing. Immediate DMA is modeled
            # synchronously; timed transfers are retained and explicitly logged.
            timing = control >> 28 & 3
            if timing:
                self.dma_calls.append({"channel": channel, "control": control, "timed_not_executed": True})
                continue
            count = control & 0xFFFF or (0x10000 if channel == 3 else 0x4000)
            width = 4 if control & (1 << 26) else 2
            smode, dmode = control >> 23 & 3, control >> 21 & 3
            source &= ~(width - 1)
            dest &= ~(width - 1)
            self.dma_calls.append({"channel": channel, "source": source, "dest": dest, "count": count, "width": width, "source_mode": smode, "dest_mode": dmode})
            if smode == 3:
                raise RuntimeError("Reserved DMA source mode")
            if smode == 0 and dmode in (0, 3):
                self.write(dest, self.read(source, count * width))
            elif smode == 2 and dmode in (0, 3):
                self.write(dest, self.read(source, width) * count)
            else:
                for index in range(count):
                    sa = source + index * width * (0 if smode == 2 else -1 if smode == 1 else 1)
                    da = dest + index * width * (0 if dmode == 2 else -1 if dmode == 1 else 1)
                    self.write(da, self.read(sa, width))
            self.w32(base + 8, control & ~0x80000000)

    def _interrupt(self, uc, intno, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        opcode = struct.unpack("<H", self.read(pc - 2, 2))[0]
        if opcode >> 8 != 0xDF:
            raise RuntimeError(f"Unsupported ARM interrupt {intno} at {pc:08x}")
        swi = opcode & 255
        r0, r1, r2, r3 = [uc.reg_read(r) for r in self.ARG_REGS]
        self.bios_calls.append({"pc": pc - 2, "swi": swi, "args": [r0, r1, r2, r3]})
        if swi in (0x0B, 0x0C):
            width = 4 if swi == 0x0C or r2 & (1 << 26) else 2
            count = r2 & 0x1FFFFF
            if swi == 0x0C:
                count = (count + 7) & ~7
            if count > 0x40000:
                raise RuntimeError("BIOS copy count exceeds isolated fixture bound")
            source, dest = r0 & ~(width - 1), r1 & ~(width - 1)
            data = self.read(source, width) * count if r2 & (1 << 24) else self.read(source, width * count)
            self.write(dest, data)
        elif swi in (0x11, 0x12):
            header = self.u32(r0)
            if header & 255 != 0x10 or header >> 8 > 0x20000:
                raise RuntimeError(f"Invalid LZ77 BIOS input {r0:08x}: {header:08x}")
            output, pos, total = bytearray(), r0 + 4, header >> 8
            while len(output) < total:
                flags = self.read(pos, 1)[0]
                pos += 1
                for bit in range(7, -1, -1):
                    if len(output) >= total:
                        break
                    if flags & (1 << bit):
                        high, low = self.read(pos, 2)
                        pos += 2
                        distance = ((high & 15) << 8 | low) + 1
                        if distance > len(output):
                            raise RuntimeError("Invalid LZ77 back reference")
                        for _ in range((high >> 4) + 3):
                            if len(output) < total:
                                output.append(output[-distance])
                    else:
                        output += self.read(pos, 1)
                        pos += 1
            self.write(r1, output)
        elif swi == 6:
            signed = lambda n: n if n < 0x80000000 else n - 0x100000000
            a, b = signed(r0), signed(r1)
            if b == 0:
                raise RuntimeError("BIOS division by zero")
            q = abs(a) // abs(b) * (-1 if (a < 0) != (b < 0) else 1)
            for reg, value in ((UC_ARM_REG_R0, q), (UC_ARM_REG_R1, a - q * b), (UC_ARM_REG_R3, abs(q))):
                uc.reg_write(reg, value & 0xFFFFFFFF)
        else:
            raise RuntimeError(f"Unsupported BIOS SWI {swi:02x} at {pc-2:08x}")

    def call(self, address, *args, max_instructions=2000000):
        if len(args) > 4:
            raise ValueError("Only register arguments supported; write stack arguments explicitly")
        self.stop = None
        self.trace.clear()
        for register, value in zip(self.ARG_REGS, args):
            self.uc.reg_write(register, value & 0xFFFFFFFF)
        self.uc.reg_write(UC_ARM_REG_SP, 0x03007E00)
        self.uc.reg_write(UC_ARM_REG_LR, self.RETURN | 1)
        try:
            self.uc.emu_start(address | 1, self.RETURN, count=max_instructions)
        except (UcError, RuntimeError) as exc:
            self.stop = {"reason": str(exc), **self.registers(), "trace": list(self.trace)}
            raise RuntimeError(f"Builtin execution stopped: {self.stop}") from exc
        if self.uc.reg_read(UC_ARM_REG_PC) != self.RETURN:
            self.stop = {"reason": "instruction limit", **self.registers(), "trace": list(self.trace)}
            raise RuntimeError(f"Builtin execution did not return: {self.stop}")
        return self.uc.reg_read(UC_ARM_REG_R0)

    def registers(self):
        return {name: self.uc.reg_read(reg) for name, reg in (
            ("pc", UC_ARM_REG_PC), ("lr", UC_ARM_REG_LR), ("sp", UC_ARM_REG_SP),
            ("cpsr", UC_ARM_REG_CPSR), *zip(("r0", "r1", "r2", "r3"), self.ARG_REGS))}


def verify_lifecycle(rom, state, cycles=3, *, field_entry=True, reset_tasks=False):
    """Run the original menu task, scheduler, allocator and cleanup in one CPU.

    The field callback is invoked explicitly, then the native script entry is
    invoked at the script boundary. This is not a complete field/script engine
    or frame timing simulation. Key state is injected at the native input ABI.
    """
    if not isinstance(cycles, int) or isinstance(cycles, bool) or not 1 <= cycles <= 100:
        raise ValueError("cycles must be an integer from 1 through 100")
    cpu = BuiltinCPU(rom, state)
    if reset_tasks:
        cpu.call(0x080773BC)
    runs = []

    def frame():
        cpu.call(cpu.u32(0x030030F4))
        cpu.call(0x08000C34)  # Native ProcessDma3Requests at the VBlank boundary.
        vblank = cpu.u32(0x030030FC)
        # Before initialization installs the menu callback, the snapshot may
        # still carry a field-specific callback. Only run the installed menu one.
        if vblank == 0x080F4BFD:
            cpu.call(vblank)

    def press(key):
        cpu.write(0x0300311C, struct.pack("<HHH", key, key, key))
        frame()
        cpu.write(0x0300311C, bytes(6))

    for cycle in range(cycles):
        if field_entry:
            cpu.call(0x08A7FA9C, 0)
            assert cpu.u32(0x02039998) == 0x08A7FAB3
            cpu.call(cpu.u32(0x02039998), 0)
        cpu.call(0x08A7A250)
        context = cpu.u32(0x020370C0)
        task_id = cpu.read(0x020370C4, 1)[0]
        assert context and cpu.u32(context - 16) & 1
        assert cpu.u32(context - 12) >= 0x1004
        phases = []
        for opening_frames in range(100):
            frame()
            phase = cpu.read(context, 3).hex()
            if not phases or phases[-1] != phase:
                phases.append(phase)
            if cpu.read(context, 1) == b"\1" and cpu.u32(0x03005090 + task_id * 40) == 0x08A7B1D1:
                break
        else:
            raise AssertionError("Menu did not become ready within 100 frames")
        screens = []
        for tab in range(3):
            assert cpu.read(0x02021CD1, 1)[0] == tab
            press(1)  # A: enter the selected Pokemon / bag / other menu.
            for _ in range(2):
                frame()
            screen = cpu.read(0x02021CD0, 1)[0]
            assert screen != 0
            screens.append(screen)
            press(2)  # B: return to the top row.
            for _ in range(2):
                frame()
            assert cpu.read(0x02021CD0, 1) == b"\0"
            if tab != 2:
                press(0x10)
        # The original tab navigation wraps at both ends.
        press(0x10)
        assert cpu.read(0x02021CD1, 1) == b"\0"
        press(0x20)
        assert cpu.read(0x02021CD1, 1) == b"\2"
        press(0x10)
        press(2)
        for closing_frames in range(100):
            frame()
            if cpu.u32(0x030030F4) == 0x080568C5:
                break
        else:
            raise AssertionError("Menu did not close within 100 frames")
        assert not cpu.u32(context - 16) & 1, "Menu allocation was not freed"
        assert cpu.call(0x08002BEC) == 1, "Native heap integrity check failed"
        runs.append({"cycle": cycle, "context": context, "task_id": task_id, "opening_frames": opening_frames + 1,
                     "closing_frames": closing_frames + 1, "phases": phases,
                     "screens": screens, "context_freed": True, "native_heap_valid": True,
                     "free_block_size_after_close": cpu.u32(context - 12)})
    assert not cpu.stub_calls
    assert not any(item.get("timed_not_executed") for item in cpu.dma_calls)
    return {"cycles": runs, "field_item_callback_executed": field_entry, "native_tasks_reset_for_fixture": reset_tasks, "native_stub_calls": cpu.stub_calls,
            "bios_services": dict(Counter(str(c["swi"]) for c in cpu.bios_calls)),
            "immediate_dma_transfers": len(cpu.dma_calls),
            "function_entry_visits": {f"{a:08x}": cpu.visits[a] for a in (
                0x08A7FA9C, 0x08A7FAB2, 0x08A7A250, 0x08A7A9F4, 0x08A7B1D0,
                0x08A7B180, 0x08002BB0, 0x08002BC4, 0x08002BEC, 0x08077578,
                0x08000C34, 0x080F4BFC, 0x08A7A05C)},
            "scope": "Isolated native entry/task/callback lifecycle with BIOS service and immediate DMA models; no field script interpreter, IRQ timing, display, audio, save/reload or live emulator verification."}


def main():
    import argparse
    import hashlib
    import json
    import sys
    import zlib
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.builtin_port_candidate import build_candidate
    from tools.read_state import read_state
    from tools.research_report import write_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--template-rom", required=True, type=Path)
    parser.add_argument("--ups", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--cycles", default=3, type=int)
    parser.add_argument("--native-entry-only", action="store_true", help="Start at the native script boundary; snapshot need not have an active bag task")
    parser.add_argument("--report", type=Path, help="Optional JSON report; input aliases and hard links are refused")
    args = parser.parse_args()
    source = args.rom.read_bytes()
    state = read_state(args.state)
    if struct.unpack_from("<I", state, 8)[0] != zlib.crc32(source):
        raise ValueError("Snapshot ROM CRC does not match the strictly pinned source")
    candidate = build_candidate(source, args.template_rom.read_bytes(), args.ups.read_bytes())
    result = verify_lifecycle(candidate.image, state, args.cycles, field_entry=not args.native_entry_only)
    report = {"version": candidate.version, "candidate_sha256": hashlib.sha256(candidate.image).hexdigest(),
              "source_state_sha256": hashlib.sha256(state).hexdigest(), "state_matches_source_crc": True, **result}
    if args.report:
        write_report(args.report, report, inputs=(args.rom, args.template_rom, args.ups, args.state))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
