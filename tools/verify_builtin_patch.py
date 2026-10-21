"""Verify local audited UPS compatibility; no live emulator or ROM file writes."""

import argparse
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import load_profile
from tools.build_patch_contract import build, build_safe
from tools.builtin_safe_candidate import build_safe_candidate
from tools.research_report import write_report
from tools.verify_rom_compatibility import RomReader, verify_transaction
from ups_patch import apply_ups


def verify_wild(target):
    from lupa import LuaRuntime

    from memory_client import MemClient
    from tests.test_core import Memory
    from tests.test_pokemon_data import sample
    from tests.test_transport import Server
    from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer
    from wild_encounter_control import WildEncounterControl

    image = bytearray(target)
    crc = f"{zlib.crc32(target) & 0xFFFFFFFF:08x}"
    ram = Memory()
    ram.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
    ram.put(PARTY_COUNT, b"\1")
    ram.put(PARTY, sample().raw)
    lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    emu = lua.table()
    emu[b"readRange"] = lambda _, a, n: (
        bytes(image[a - 0x08000000 : a - 0x08000000 + n])
        if a >= 0x08000000
        else ram.read(a, n)
    )
    emu[b"write8"] = lambda _, a, v: ram.put(a, bytes([v]))
    emu[b"checksum"] = lambda _: bytes.fromhex(crc)
    emu[b"readRegister"] = lambda *_: 0x08001000
    cart = lua.table()
    cart[b"write8"] = lambda _, a, v: image.__setitem__(a, v)
    memory = lua.table()
    memory[b"cart0"] = cart
    emu[b"memory"] = memory
    lua.globals()[b"emu"] = emu
    lua.globals()[b"MERCURY_BRIDGE_TEST"] = True
    root = Path(__file__).resolve().parents[1]
    handle = lua.execute((root / "mercury_bridge.lua").read_bytes())
    server = Server(lambda line: [handle(line) + b"\n"])
    client = MemClient(port=server.port)
    try:
        client.connect(scan=1)
        profile = load_profile(crc=crc, memory=client)
        with tempfile.TemporaryDirectory() as folder:
            trainer = Trainer(client, profile, folder)
            control = WildEncounterControl(client, profile, folder)
            assert control.arm(201, 50)["state"] == "active"
            trainer.verify(full_compatibility=True)
            rejected = []
            # The bridge deliberately retains its cached target CRC while the
            # owned wild overlay is active. Every foreign editor/safety change
            # must still be found by the complete structural fingerprint check.
            for offset in (0xA7A100, 0xA7FD00, 0xA7FE00):
                image[offset] ^= 1
                try:
                    assert client.command("ROMCRC").decode("ascii") == crc
                    try:
                        trainer.verify(full_compatibility=True)
                    except ValueError:
                        rejected.append(f"{0x08000000 + offset:08x}")
                    else:
                        raise AssertionError(
                            "Foreign internal-editor payload change was hidden"
                        )
                finally:
                    image[offset] ^= 1
                trainer.verify(full_compatibility=True)
            assert control.stop()["state"] == "inactive"
            assert bytes(image) == target
            trainer.verify(full_compatibility=True)
        return {
            "arm_verify_stop_roundtrip": True,
            "foreign_payload_change_rejected": True,
            "cached_crc_payload_changes_rejected": rejected,
            "scope": "real Lua/TCP with synthetic RAM/cart0; no real mGBA execution",
        }
    finally:
        client.close()
        server.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument(
        "--safe-template", type=Path, help="原版1.33模板；位置参数ups为原始已审计UPS"
    )
    parser.add_argument("--lua-tcp", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    inputs = (args.rom, args.ups) + (
        (args.safe_template,) if args.safe_template else ()
    )
    if args.report.suffix.lower() != ".json":
        raise ValueError("核验报告必须使用JSON文件")
    for original in inputs:
        if args.report.resolve() == original.resolve() or (
            args.report.exists()
            and original.exists()
            and args.report.samefile(original)
        ):
            raise ValueError("核验报告不能覆盖输入文件或其链接")
    root = Path(__file__).resolve().parents[1]
    source, patch = args.rom.read_bytes(), args.ups.read_bytes()
    if args.safe_template:
        template = args.safe_template.read_bytes()
        entry, report = build_safe(source, template, patch, root=root)
        target = build_safe_candidate(source, template, patch).image
    else:
        entry, report = build(source, patch, root=root)
        target = apply_ups(source, patch)
    shipped = json.loads(
        (root / "rom_patch_compatibility.json").read_text(encoding="utf-8")
    )
    assert entry == next(e for e in shipped["variants"] if e["id"] == entry["id"])
    local = load_profile(target, root=root)
    online = load_profile(
        crc=entry["target_crc32"], memory=RomReader(target), root=root
    )
    assert (
        local["compatibility"]["patch_id"]
        == online["compatibility"]["patch_id"]
        == entry["id"]
    )
    report.update(
        {
            "shipped_contract_reproduced": True,
            "offline_online_recognition": True,
            "scope": "local read-only ROM/UPS; all mutations are in-memory copies",
        }
    )
    if args.lua_tcp:
        report["party_lua_tcp"] = verify_transaction(target)
        report["wild_lua_tcp"] = verify_wild(target)
    write_report(args.report, report, inputs=inputs)
    print("Audited patch verification passed:", args.report)


if __name__ == "__main__":
    main()
