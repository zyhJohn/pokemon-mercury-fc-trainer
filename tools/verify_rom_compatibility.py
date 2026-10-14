"""Exercise compatibility on in-memory copies of locally supplied audited ROMs.

No ROM, save, emulator or game memory is written. Reports should stay private.
"""

import argparse
import json
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_compatibility import build_manifest_entry
from rom_versions import load_profile, release, runtime_compatibility_check


class RomReader:
    def __init__(self, rom):
        self.rom = rom
        self.crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}"
        self.reads = 0

    def command(self, command):
        if command != "ROMCRC":
            raise ValueError("Only read-only ROMCRC is implemented")
        return self.crc.encode("ascii")

    def read(self, address, size):
        if not 1 <= size <= 4096:
            raise ValueError("READ size exceeded")
        return self.read_rom(address, size)

    def read_rom(self, address, size):
        if not 1 <= size <= 65536 or not 0x08000000 <= address <= 0x0A000000 - size:
            raise ValueError("ROM read exceeded")
        self.reads += 1
        start = address - 0x08000000
        return self.rom[start:start + size]


def verify_transaction(rom):
    """Real Lua/TCP path with actual ROM bytes and synthetic RAM only."""
    from lupa import LuaRuntime
    from memory_client import MemClient
    from tests.test_core import Memory
    from tests.test_pokemon_data import sample
    from tests.test_transport import Server
    from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer

    changed = bytearray(rom)
    changed[0xA0] ^= 1
    crc = f"{zlib.crc32(changed) & 0xFFFFFFFF:08x}"
    ram = Memory()
    ram.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
    ram.put(PARTY_COUNT, b"\1")
    ram.put(PARTY, sample().raw)
    lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    emu = lua.table()
    emu[b"readRange"] = lambda _, address, size: (
        bytes(changed[address - 0x08000000:address - 0x08000000 + size])
        if address >= 0x08000000 else ram.read(address, size)
    )

    def write(_, address, value):
        ram.put(address, bytes([value]))
        ram.writes += 1

    emu[b"write8"] = write
    emu[b"checksum"] = lambda _: bytes.fromhex(crc)
    lua.globals()[b"emu"] = emu
    lua.globals()[b"MERCURY_BRIDGE_TEST"] = True
    handle = lua.execute(Path("mercury_bridge.lua").read_bytes())
    server = Server(lambda line: [handle(line) + b"\n"])
    client = MemClient(port=server.port)
    started = time.perf_counter()
    try:
        client.connect(scan=1)
        selected = load_profile(crc=crc, memory=client)
        assert "ROMREAD" in client.capabilities and selected.get("compatibility")
        with tempfile.TemporaryDirectory() as folder:
            trainer = Trainer(client, selected, folder)
            snapshot = trainer.snapshot()
            patches, _ = trainer.edit_pokemon(snapshot, 0, shiny=True, ivs=[31] * 6)
            result = trainer.commit(snapshot, patches, "modified ROM Lua/TCP fixture")
            assert client.read(PARTY, 100) != sample().raw
            trainer.restore(result["backup"])
            assert client.read(PARTY, 100) == sample().raw
            before = ram.writes
            snapshot = trainer.snapshot()
            patches, _ = trainer.edit_pokemon(snapshot, 0, shiny=True)
            changed[selected["base_stats_address"] - 0x08000000 + 28] ^= 1
            try:
                trainer.commit(snapshot, patches, "tampered numeric table")
            except ValueError:
                pass
            else:
                raise AssertionError("Changed metadata unexpectedly permitted a write")
            assert ram.writes == before
        return {"write_readback_restore": True, "changed_table_write_refused": True,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "scope": "real Lua/TCP; synthetic RAM; no real mGBA/game/save writes"}
    finally:
        client.close()
        server.join()


def verify(path, *, lua_tcp=False):
    started = time.perf_counter()
    rom = path.read_bytes()
    sha, (version, crc, _) = release(rom)
    profile = load_profile(rom)
    manifest = json.loads(Path("rom_compatibility_manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads(Path("catalog.json").read_text(encoding="utf-8"))
    entry = build_manifest_entry(rom, profile, catalog)
    if entry != manifest["releases"][sha]:
        raise AssertionError("Contract reproduction differs")
    guards = entry["guards"]

    def protected(address):
        return any(g["address"] <= address < g["address"] + g["size"] for g in guards)

    asset = next(value["tiles"] for value in profile["icons"].values()
                 if not protected(value["tiles"]))
    results = []
    for name, address, accepted in (
        ("header-title", 0x080000A0, True),
        ("icon-pixels", asset, True),
        ("species-parameters", profile["base_stats_address"] + 28, False),
        ("core-code", 0x0803F94C, False),
    ):
        changed = bytearray(rom)
        changed[address - 0x08000000] ^= 1
        reader = RomReader(bytes(changed))
        observed = []
        for mode in ("offline", "rom-backed-reader"):
            try:
                selected = (load_profile(changed) if mode == "offline" else
                            load_profile(crc=reader.crc, memory=reader))
            except ValueError:
                passed = False
            else:
                passed = True
                assert selected["rom_sha256"] == sha
                assert selected["rom_crc32"] == reader.crc != crc
                assert selected["compatibility"]["baseline_version"] == version
                if mode != "offline":
                    runtime_compatibility_check(reader, selected)
            assert passed == accepted, (version, name, mode, passed)
            observed.append({"mode": mode, "accepted": passed})
        results.append({"change": name, "outcomes": observed, "read_calls": reader.reads})
    result = {"baseline_version": version, "manifest_reproduced": True,
            "protected_bytes": sum(g["size"] for g in guards), "cases": results,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "scope": "offline and ROM-backed reader; no real mGBA or game/save writes"}
    if lua_tcp:
        result["lua_tcp"] = verify_transaction(rom)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", nargs="+", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--lua-tcp", action="store_true", help="also verify real Lua/TCP with synthetic RAM")
    args = parser.parse_args()
    result = {"results": [verify(path, lua_tcp=args.lua_tcp) for path in args.rom]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
