"""Compare offline sidequest bits with the game's actual FlagGet in isolated CPU.

Read-only: ROM and mGBA .ss1 are never changed. Examples:
  python tools/verify_sidequests.py --rom GAME.gba --state GAME.ss1
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rom_versions import RELEASES  # noqa: E402
from sidequest_data import load_catalog, read_snapshot  # noqa: E402
from tools.read_state import StateMemory, read_state  # noqa: E402
from tools.verify_rom_routines import RomCPU  # noqa: E402


def verify(rom, state, layout):
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES or sha not in layout["releases"]:
        raise ValueError("ROM SHA-256 未核对")
    version, _, profile_name = RELEASES[sha]
    profile = json.loads((ROOT / profile_name).read_text(encoding="utf-8"))
    if profile["rom_sha256"] != sha:
        raise ValueError("配置与ROM不符")
    release = layout["releases"][sha]
    if release["version"] != version:
        raise ValueError("支线布局版本与ROM不符")
    catalog = load_catalog()
    mem = StateMemory(state, rom)
    decoded = read_snapshot(mem, profile, layout, catalog)
    engine = RomCPU(rom, state)
    mismatches = []
    sample = {}
    for quest in catalog:
        flags = release["quest_flags"][quest["id"]]
        entry = flags["rom_entry"] - 0x08000000
        pair = struct.unpack_from("<HH", rom, entry + 10)
        if pair != (flags["accept_flag"], flags["complete_flag"]):
            raise ValueError(f"{quest['id']}: ROM任务表标志与布局不符")
        if "extra_flag_table" in release:
            extra_at = release["extra_flag_table"] - 0x08000000 + flags["table_index"] * 8 + 4
            extra = struct.unpack_from("<H", rom, extra_at)[0]
            if extra != next(iter(flags.get("extra_accept_flags", [])), 0):
                raise ValueError(f"{quest['id']}: ROM额外接取标志与布局不符")
        raw_accepted = engine.call(release["flag_get"], flags["accept_flag"])
        completed = engine.call(release["flag_get"], flags["complete_flag"])
        if raw_accepted not in (0, 1) or completed not in (0, 1):
            raise ValueError(f"{quest['id']}: ROM FlagGet返回非布尔值")
        accepted = bool(raw_accepted) or any(
            engine.call(release["flag_get"], flag) for flag in flags.get("extra_accept_flags", []))
        if "accepted_function" in release:
            game_accepted = engine.call(release["accepted_function"], flags["table_index"])
            if bool(game_accepted) != (accepted or bool(completed)):
                mismatches.append({"id": quest["id"], "reason": "游戏任务接取函数与标志解码不同",
                                   "rom": game_accepted, "decoded": accepted or bool(completed)})
        actual = ("complete" if completed else "in_progress" if accepted else "incomplete")
        if actual != decoded[quest["id"]]:
            mismatches.append({"id": quest["id"], "rom": actual, "decoded": decoded[quest["id"]]})
        sample[quest["id"]] = actual
    if mismatches:
        raise ValueError(f"ROM函数与内存解码不符：{mismatches[:5]}")
    synthetic_cases = 0
    if "accepted_function" in release:
        for ident in ("001", "042"):
            flags = release["quest_flags"][ident]
            involved = [flags["accept_flag"], flags["complete_flag"],
                        *flags["extra_accept_flags"]]
            addresses = {0x0203B174 + (flag - 0x900) // 8 for flag in involved}
            original = {address: engine.read(address, 1)[0] for address in addresses}
            try:
                for flag in involved:
                    address = 0x0203B174 + (flag - 0x900) // 8
                    engine.write(address, bytes((engine.read(address, 1)[0] & ~(1 << (flag & 7)),)))
                if engine.call(release["accepted_function"], flags["table_index"]) != 0:
                    raise ValueError(f"{ident}: 清除所有接取标志后游戏仍判已接取")
                extra = flags["extra_accept_flags"][0]
                address = 0x0203B174 + (extra - 0x900) // 8
                engine.write(address, bytes((engine.read(address, 1)[0] | (1 << (extra & 7)),)))
                if engine.call(release["accepted_function"], flags["table_index"]) != 1:
                    raise ValueError(f"{ident}: 单设额外标志后游戏未判已接取")
                synthetic_cases += 2
            finally:
                for address, value in original.items():
                    engine.write(address, bytes((value,)))
    return {
        "rom_sha256": sha,
        "version": version,
        "snapshot_sha256": hashlib.sha256(state).hexdigest(),
        "flag_get_address": f"0x{release['flag_get']:08X}",
        "primary_flag_checks": len(catalog) * 2,
        "rom_table_entries": len(catalog),
        "accepted_function_checks": len(catalog) if "accepted_function" in release else 0,
        "counts": dict(Counter(sample.values())),
        "synthetic_cases": synthetic_cases,
        "statuses": sample,
        "passed": True,
        "scope": "验证ROM任务表、FlagGet与内存解码一致；V1.2另核对额外接取表及游戏accepted函数。",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--layout", type=Path, default=ROOT / "sidequests_layout.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    result = verify(args.rom.read_bytes(), read_state(args.state), layout)
    print(json.dumps({key: value for key, value in result.items() if key != "statuses"},
                     ensure_ascii=False, indent=2))
    if args.report:
        target = args.report.resolve()
        diagnostics = (ROOT / "diagnostics").resolve()
        if not target.is_relative_to(diagnostics):
            raise ValueError("报告只可写入已忽略的 diagnostics/ 目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
