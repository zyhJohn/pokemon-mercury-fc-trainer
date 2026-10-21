"""Verify local audited UPS compatibility; no live emulator or ROM file writes."""

import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_versions import load_profile
from tools.build_patch_contract import build
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
        bytes(image[a - 0x08000000:a - 0x08000000 + n]) if a >= 0x08000000 else ram.read(a, n))
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
    handle = lua.execute(Path("mercury_bridge.lua").read_bytes())
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
            # A foreign change outside our owned wild overlay remains visible.
            image[0xA7A100] ^= 1
            try:
                trainer.verify(full_compatibility=True)
            except ValueError:
                pass
            else:
                raise AssertionError("Foreign internal-editor payload change was hidden")
            image[0xA7A100] ^= 1
            assert control.stop()["state"] == "inactive"
            assert bytes(image) == target
            trainer.verify(full_compatibility=True)
        return {"arm_verify_stop_roundtrip": True, "foreign_payload_change_rejected": True,
                "scope": "real Lua/TCP with synthetic RAM/cart0; no real mGBA execution"}
    finally:
        client.close()
        server.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("--lua-tcp", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source, patch = args.rom.read_bytes(), args.ups.read_bytes()
    entry, report = build(source, patch, root=root)
    shipped = json.loads((root / "rom_patch_compatibility.json").read_text(encoding="utf-8"))
    assert entry == next(e for e in shipped["variants"] if e["id"] == entry["id"])
    target = apply_ups(source, patch)
    local = load_profile(target, root=root)
    online = load_profile(crc=entry["target_crc32"], memory=RomReader(target), root=root)
    assert local["compatibility"]["patch_id"] == online["compatibility"]["patch_id"] == entry["id"]
    report.update({"shipped_contract_reproduced": True, "offline_online_recognition": True,
                   "scope": "local read-only ROM/UPS; all mutations are in-memory copies"})
    if args.lua_tcp:
        report["party_lua_tcp"] = verify_transaction(target)
        report["wild_lua_tcp"] = verify_wild(target)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Audited patch verification passed:", args.report)


if __name__ == "__main__":
    main()
