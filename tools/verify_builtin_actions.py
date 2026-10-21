"""Execute builtin editor actions offline against user-selected research ROMs.

All code runs in Unicorn RAM. Input ROM/UPS/state files are read only. Reports
contain private RAM deltas and belong in ignored work/diagnostics directories.
"""
import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import zlib

from pokemon_creation import box_to_party_pokemon, create_box_pokemon
from pokemon_data import Pokemon, calculate_stats
from rom_versions import load_profile
from tools.builtin_port_candidate import build_candidate, identity
from tools.read_state import read_state
from tools.research_report import write_report
from tools.verify_builtin_menu import BuiltinCPU
from ups_patch import apply_ups

PARTY = 0x02024284
CONTEXT = 0x02021CD0
HANDLER = 0x08A7B9D4
UI_STUBS = {
    0x08A7A5EC: "sprite redraw", 0x08A7A40C: "nickname display buffer",
    0x08A7B590: "selection drawing", 0x08A7B734: "UI delay",
    0x08A7D67C: "menu drawing", 0x08A7E040: "menu confirmation drawing",
    0x08A7CE24: "nickname drawing", 0x08A7CD7C: "result text drawing",
}
# Absolute entrypoints from each exact baseline; candidate SHA is never used
# for RomCPU's identity-triggered V1.0 alias mapping.
NATIVE = {
    "1.0": {"compress": 0x09D54A94, "decompress": 0x09D54868},
    "1.2": {"compress": 0x09D58854, "decompress": 0x09D58628},
    "1.33": {"compress": 0x09D5A630, "decompress": 0x09D5A404},
}

def h16(cpu, address, value):
    cpu.write(address, struct.pack("<H", value))

def synth_party(profile):
    result = []
    for slot in range(6):
        pc = create_box_pokemon(profile, species=(1, 25, 201, 29, 32, 81)[slot],
            level=20 + slot, pid=0x12340010 + slot, otid=0x56780000 + slot,
            nickname="TEST", ot_name="QA", moves=(33, 45, 0, 0),
            ivs=(1, 2, 3, 4, 5, 6), evs=(10, 20, 30, 40, 50, 60))
        result.append(box_to_party_pokemon(profile, pc).raw)
    return b"".join(result)

def engine(rom, state, party):
    cpu = BuiltinCPU(rom, state)
    for address, name in UI_STUBS.items():
        cpu.install_stub(address, name)
    # Allocation used only by the ability list preview. Core fields, capacity
    # scans, native setters, statistics and compression are never stubbed.
    cpu.install_stub(0x08002B9C, "isolated preview allocator", lambda c, a: 0x02016000)
    cpu.install_stub(0x08002BC4, "isolated preview free")
    cpu.write(CONTEXT, bytes(0x1004))
    cpu.write(PARTY, party)
    cpu.write(0x02024029, b"\x06")
    return cpu

def ram(cpu):
    return {a: cpu.read(a, n) for a, n in ((0x02000000, 0x40000), (0x03000000, 0x8000))}

def ram_delta(before, after):
    out = []
    for base, old in before.items():
        new = after[base]
        ranges, i = [], 0
        while i < len(old):
            if old[i] == new[i]:
                i += 1
                continue
            start = i
            while i < len(old) and old[i] != new[i]:
                i += 1
            ranges.append({"address": f"{base+start:08x}", "size": i-start,
                "before": old[start:i].hex(), "after": new[start:i].hex()})
        out.append({"address": f"{base:08x}", "size": len(old),
            "before_sha256": hashlib.sha256(old).hexdigest(),
            "after_sha256": hashlib.sha256(new).hexdigest(), "changed_ranges": ranges})
    return out

def native_evidence(cpu):
    wanted = {0x0803FBE8: "GetMonData", 0x0804037C: "SetMonData",
        0x0803E47C: "CalculateMonStats", 0x0809A084: "AddBagItem",
        0x08040B14: "SendMonToPC", **{v: k for k, v in NATIVE.get(cpu.version, {}).items()}}
    return {name: {"address": f"{address:08x}", "entries": cpu.visits[address]}
        for address, name in wanted.items() if cpu.visits[address]}

def record(cpu, name, before, assertions=None):
    return {"case": name, "status": "passed", "assertions": assertions or {},
        "native_execution": native_evidence(cpu), "ui_stubs": cpu.stub_calls,
        "bios": cpu.bios_calls, "ram": ram_delta(before, ram(cpu))}

def hydrate(cpu, slot):
    cpu.write(CONTEXT + 0x10, bytes([slot]))
    cpu.call(0x08A7A6B4)

def verify_actions(rom, state, profile, version):
    party = synth_party(profile)
    rows = []
    for slot in range(6):
        for operation in ("species", "level", "move", "iv", "ev"):
            cpu = engine(rom, state, party)
            cpu.version = version
            before = ram(cpu)
            try:
                hydrate(cpu, slot)
                if operation == "species":
                    menu, expected = 0x15, 4
                    h16(cpu, CONTEXT + 0x2C, expected)
                elif operation == "level":
                    menu, expected = 0x10, 50
                    cpu.write(CONTEXT + 0x14, bytes([expected]))
                elif operation == "move":
                    menu, expected = 0x14, 85
                    cpu.write(CONTEXT + 9, b"\x02")
                    h16(cpu, CONTEXT + 0x2C, expected)
                elif operation == "iv":
                    menu, expected = 0x12, [31, 0, 15, 30, 1, 29]
                    cpu.write(CONTEXT + 0x1A, bytes(expected))
                else:
                    menu, expected = 0x13, [252, 0, 4, 252, 0, 2]
                    cpu.write(CONTEXT + 0x1A, bytes(expected))
                cpu.write(CONTEXT, bytes([menu]))
                before = ram(cpu)
                cpu.call(HANDLER)
                after_party = cpu.read(PARTY, 600)
                actual = Pokemon(after_party[slot*100:(slot+1)*100])
                assert all(after_party[i*100:(i+1)*100] == party[i*100:(i+1)*100]
                           for i in range(6) if i != slot), "unselected party slot changed"
                found = {"species": actual.species, "level": actual.level,
                    "move": actual.moves[2], "iv": list(actual.ivs), "ev": list(actual.evs)}[operation]
                assert found == expected, (operation, found, expected)
                # Independent project formula; actual native recalc must have run.
                if operation != "move":
                    meta = profile["species"][str(actual.species)]
                    expected_stats = calculate_stats(meta["base"], actual.ivs, actual.evs,
                        actual.level, actual.pid % 25, actual.species == 303)
                    assert actual.stats == expected_stats, (actual.stats, expected_stats)
                    assert cpu.visits[0x0803E47C]
                assert cpu.visits[0x0804037C]
                rows.append(record(cpu, f"slot{slot+1}-{operation}", before,
                    {"field_equal": True, "unselected_five_unchanged": True,
                     "native_stats_equal_formula": operation != "move",
                     "party_before": party.hex(), "party_after": after_party.hex()}))
            except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
                rows.append(failure_record(cpu, f"slot{slot+1}-{operation}", before, exc))
                # Keep diagnosing other handlers; report every failed path.
    return rows

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("ups", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ui-bag", action="store_true")
    parser.add_argument("--field-entry", action="store_true")
    parser.add_argument("--reset-tasks", action="store_true")
    args = parser.parse_args()
    source, template, ups = (p.read_bytes() for p in (args.source, args.template, args.ups))
    profile = load_profile(source)
    version = next(v for v, table in (("1.0", "rom_profile.json"), ("1.2", "rom_profile_v12.json"),
        ("1.33", "rom_profile_v133.json")) if profile["rom_sha256"] == json.loads(
        (Path(__file__).resolve().parents[1] / table).read_text(encoding="utf-8"))["rom_sha256"])
    target = apply_ups(source, ups) if version == "1.33" else build_candidate(source, template, ups).image
    state = read_state(args.state)
    rows = verify_actions(target, state, profile, version)
    rows += verify_bag(target, state, profile, version)
    rows += verify_pc(target, state, profile, version)
    rows += verify_boundaries(target, state, profile, version)
    rows += verify_full_native(source, state, profile, version)
    if args.ui_bag:
        rows += verify_ui_bag(target, state, profile, version, field_entry=args.field_entry,
                             reset_tasks=args.reset_tasks)
    result = {"scope": "isolated payload handlers and native core routines; no save/reload or live gameplay",
        "version": version, "source": identity(source), "candidate": identity(target),
        "state": {"decoded_sha256": hashlib.sha256(state).hexdigest(),
                  "recorded_rom_crc32": f"{struct.unpack_from('<I', state, 8)[0]:08x}",
                  "source_rom_crc32": identity(source)["crc32"],
                  "cross_version_fixture": struct.unpack_from('<I', state, 8)[0] != zlib.crc32(source),
                  "synthetic_party_replaced": True,
                  "scope": "RAM fixture; recorded CRC match is reported, not a claim of live gameplay"},
        "binary_files_written": False, "cases": rows,
        "counts": dict(Counter(row["status"] for row in rows))}
    write_report(args.report, result, inputs=(args.source, args.template, args.ups, args.state))
    print(json.dumps(case_summary(version, rows)))


def case_summary(version, rows):
    return {"version": version, "counts": dict(Counter(row["status"] for row in rows)),
            "failures": [{"case": row["case"], "error": row["error"].split("\n")[0][:160],
                          "pc": (row.get("stop") or {}).get("pc")}
                         for row in rows if "error" in row]}




def verify_bag(rom, state, profile, version):
    rows, party = [], synth_party(profile)
    for name, qty, full in (("empty-zero", 0, False), ("empty-one", 1, False),
            ("empty-999", 999, False), ("empty-1000", 1000, False),
            ("empty-65535", 65535, False), ("full-one", 1, True)):
        cpu = engine(rom, state, party)
        cpu.version = version
        before = ram(cpu)
        pocket = profile["pockets"][1 if full else 0]
        addr, size = pocket["address"], pocket["capacity"] * 4
        ids = sorted(int(i) for i, item in profile["items"].items() if int(i) and item["pocket"] == pocket["id"])
        item = ids[pocket["capacity"]] if full else 13
        initial = b"".join(struct.pack("<HH", i, 1) for i in ids[:pocket["capacity"]]) if full else bytes(size)
        cpu.write(addr, initial)
        cpu.write(CONTEXT, b"\x20")
        h16(cpu, CONTEXT + 0x44, item)
        h16(cpu, CONTEXT + 0x28, qty)
        cpu.write(CONTEXT + 0x46, b"\x01")
        before = ram(cpu)
        try:
            cpu.call(HANDLER)
            bag = cpu.read(addr, size)
            pairs = list(struct.iter_unpack("<HH", bag))
            added = [(i, q) for i, q in pairs if i == item]
            total = sum(q for i, q in added)
            expected = 0 if full else qty
            assert total == expected, (total, expected, added[:5])
            # Direct-handler probes document native u16 quantity behavior.
            assert cpu.read(PARTY, 600) == party
            if full or qty == 0:
                assert bag == before[0x02000000][addr-0x02000000:addr-0x02000000+size]
            if qty:
                assert cpu.visits[0x0809A084]
            rows.append(record(cpu, "bag-" + name, before, {"requested": qty,
                "added_total": total, "stacks": len(added), "stack_quantities": [q for i, q in added], "direct_handler_input": True,
                "party_unchanged": True, "pocket_before": before[0x02000000][addr-0x02000000:addr-0x02000000+size].hex(),
                "pocket_after": bag.hex()}))
        except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
            row = failure_record(cpu, "bag-" + name, before, exc)
            row["fixture"] = {"pocket": pocket["id"], "capacity": pocket["capacity"], "existing_distinct_valid_ids": ids[:pocket["capacity"]], "requested_item": item, "requested_quantity": qty}
            rows.append(row)
    return rows


def verify_pc(rom, state, profile, version):
    rows, party = [], synth_party(profile)
    addresses = profile["storage"]["box_addresses"]
    pc_sample = create_box_pokemon(profile, species=1, level=20, pid=0x12340010,
        otid=0x56780000, nickname="TEST", ot_name="QA", moves=(33, 45, 0, 0),
        ivs=(1, 2, 3, 4, 5, 6), evs=(10, 20, 30, 40, 50, 60)).raw
    for name, full_boxes in (("empty", 0), ("first-full", 1), ("all-full", 25)):
        cpu = engine(rom, state, party)
        cpu.version = version
        for i, address in enumerate(addresses):
            cpu.write(address, pc_sample * 30 if i < full_boxes else bytes(58 * 30))
        # Current box 0 is the state fixture's normal starting point, explicitly
        # set through its native accessor rather than guessing a pointer.
        try:
            cpu.call(0x0806E584, 0x4037, 0)
            cpu.call(0x080CC79C, 0)
            cpu.call(0x0808BA00, 0)
            hydrate(cpu, 0)
            cpu.write(CONTEXT, b"\x11")
            cpu.write(CONTEXT + 0x43, b"\x01")
            before = ram(cpu)
            before_boxes = [cpu.read(a, 1740) for a in addresses]
            cpu.call(HANDLER)
            after_party = cpu.read(PARTY, 600)
            after_boxes = [cpu.read(a, 1740) for a in addresses]
            changed = [(b, s) for b in range(25) for s in range(30)
                if before_boxes[b][58*s:58*(s+1)] != after_boxes[b][58*s:58*(s+1)]]
            assert changed == ([] if full_boxes == 25 else [(full_boxes, 0)]), changed
            assert all(after_party[i*100:(i+1)*100] == party[i*100:(i+1)*100] for i in range(1, 6))
            assert cpu.visits[0x08040B14]
            if changed:
                cpu.write(0x02018000, after_party[:100])
                cpu.call(NATIVE[version]["compress"], 0x02018000, 0x02018100)
                expected = cpu.read(0x02018100, 58)
                b, s = changed[0]
                assert after_boxes[b][58*s:58*(s+1)] == expected, "native copied record differs"
                cpu.call(NATIVE[version]["decompress"], 0x02018200, addresses[b] + 58*s)
                decoded = Pokemon(cpu.read(0x02018200, 80) + bytes(20))
                actual = Pokemon(after_party[:100])
                for attr in ("species", "pid", "otid", "moves", "ivs", "evs", "egg", "ability_flag"):
                    assert getattr(decoded, attr) == getattr(actual, attr), attr
            rows.append(record(cpu, "pc-copy-" + name, before, {"changed_slots": changed,
                "unselected_party_unchanged": True, "native_compress_decompress_equal": bool(changed),
                "boxes_before": [b.hex() for b in before_boxes], "boxes_after": [b.hex() for b in after_boxes]}))
        except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
            rows.append(failure_record(cpu, "pc-copy-" + name, before, exc))
    return rows




def verify_boundaries(rom, state, profile, version):
    rows, party = [], synth_party(profile)
    # XOR 8 is an existing payload display boundary, measured separately.
    for xor in (0, 7, 8, 9):
        cpu = engine(rom, state, party)
        cpu.version = version
        raw = bytearray(party[:100])
        struct.pack_into("<II", raw, 0, xor, 0)
        cpu.write(PARTY, raw)
        cpu.w32(CONTEXT + 0x20, xor)
        cpu.w32(CONTEXT + 0x24, 0)
        before = ram(cpu)
        cpu.call(0x08A7A3C8)
        displayed = cpu.read(CONTEXT + 0x18, 1)[0]
        native = cpu.call(0x08044470, PARTY)
        assert bool(displayed) == (xor <= 8)
        assert bool(native) == (xor < 8)
        row = record(cpu, f"shiny-xor-{xor}", before,
            {"payload_display": displayed, "native": native, "agree": bool(displayed) == bool(native)})
        if xor == 8:
            row["status"] = "known_issue"
            row["finding"] = "Existing payload display uses XOR <=8; native rule is XOR <8. Not changed by this research."
        rows.append(row)
    for operation, values in (("level", (1, 100)), ("iv", (0, 31)), ("ev", (0, 255)), ("move", (0, 1023))):
        for value in values:
            cpu = engine(rom, state, party)
            cpu.version = version
            before = ram(cpu)
            try:
                hydrate(cpu, 0)
                menu = {"level": 0x10, "iv": 0x12, "ev": 0x13, "move": 0x14}[operation]
                cpu.write(CONTEXT, bytes([menu]))
                if operation == "level":
                    cpu.write(CONTEXT + 0x14, bytes([value]))
                elif operation == "move":
                    cpu.write(CONTEXT + 9, b"\x03")
                    h16(cpu, CONTEXT + 0x2C, value)
                else:
                    cpu.write(CONTEXT + 0x1A, bytes([value] * 6))
                before = ram(cpu)
                cpu.call(HANDLER)
                mon = Pokemon(cpu.read(PARTY, 100))
                found = {"level": mon.level, "iv": list(mon.ivs), "ev": list(mon.evs), "move": mon.moves[3]}[operation]
                assert found == (value if operation in ("level", "move") else [value]*6), found
                assert cpu.read(PARTY + 100, 500) == party[100:]
                rows.append(record(cpu, f"boundary-{operation}-{value}", before,
                    {"observed": found, "direct_handler_input": True,
                     "menu_reachability_of_out_of_range_values": "not asserted"}))
            except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
                rows.append(failure_record(cpu, f"boundary-{operation}-{value}", before, exc))
    # Same handler with ordinary/egg/fixed-gender species and hidden ability.
    for species, egg, hidden in ((1, False, True), (29, False, False),
            (32, False, False), (81, False, False), (1, True, False)):
        cpu = engine(rom, state, party)
        cpu.version = version
        before = ram(cpu)
        name = f"basic-species-{species}-egg-{egg}-hidden-{hidden}"
        try:
            pc = create_box_pokemon(profile, species=species, level=1 if egg else 20,
                pid=0x12340010, otid=0x56780000, nickname="TEST", ot_name="QA",
                moves=(33, 45, 0, 0), egg=egg)
            cpu.write(PARTY, box_to_party_pokemon(profile, pc).raw)
            hydrate(cpu, 0)
            cpu.write(CONTEXT, b"\x10")
            if hidden:
                cpu.write(CONTEXT + 0x16, b"\x02")
            cpu.write(CONTEXT + 0x14, b"\x32")
            before = ram(cpu)
            cpu.call(HANDLER)
            mon = Pokemon(cpu.read(PARTY, 100))
            assert cpu.read(PARTY + 100, 500) == party[100:]
            if hidden:
                assert mon.ability_flag
                assert cpu.call(0x08040D38, PARTY) == profile["species"][str(species)]["abilities"][2]
            if not egg:
                assert mon.level == 50
            row = record(cpu, name, before, {"species_after": mon.species,
                "egg_after": mon.egg, "level_after": mon.level, "pid_after": mon.pid,
                "hidden_after": mon.ability_flag, "gender_context": cpu.read(CONTEXT + 0x19, 1)[0]})
            if egg:
                row["status"] = "observation"
                row["finding"] = "Egg dispatch enters payload CreateMon branch; result is measured, not asserted to preserve egg identity."
            rows.append(row)
        except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
            rows.append(failure_record(cpu, name, before, exc))
    return rows


def failure_record(cpu, name, before, exc):
    return {"case": name, "status": "blocked", "error": str(exc), "stop": cpu.stop,
        "native_execution": native_evidence(cpu), "ram": ram_delta(before, ram(cpu)),
        "ui_stubs": cpu.stub_calls, "bios": cpu.bios_calls}

def verify_full_native(source, state, profile, version):
    """Reproduce full legitimate key-item pocket on pristine native code."""
    cpu = BuiltinCPU(source, state)
    cpu.version = version
    pocket = profile["pockets"][1]
    ids = sorted(int(i) for i, item in profile["items"].items()
                 if int(i) and item["pocket"] == pocket["id"])
    cpu.write(pocket["address"], b"".join(struct.pack("<HH", i, 1)
              for i in ids[:pocket["capacity"]]))
    before = ram(cpu)
    try:
        result = cpu.call(0x0809A084, ids[pocket["capacity"]], 1)
        row = record(cpu, "pristine-native-full-key-pocket", before, {"return": result})
        assert result == 0, result
        return [row]
    except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
        row = failure_record(cpu, "pristine-native-full-key-pocket", before, exc)
        row["pristine_source_rom"] = True
        row["native_stubs"] = False
        row["fixture"] = {"pocket": 2, "distinct_valid_occupied": pocket["capacity"],
            "requested_item": ids[pocket["capacity"]], "requested_quantity": 1}
        return [row]


def verify_ui_bag(rom, state, profile, version, *, hardened=False, field_entry=False,
                  reset_tasks=False):
    """Actual initialized menu and key processors; renderer stubs after open."""
    cpu = BuiltinCPU(rom, state)
    cpu.version = version
    if reset_tasks:
        cpu.call(0x080773BC)
    steps = []
    def frame():
        cpu.call(cpu.u32(0x030030F4))
        cpu.call(0x08000C34)
        if cpu.u32(0x030030FC) == 0x080F4BFD:
            cpu.call(0x080F4BFC)
    def press(key):
        cpu.write(0x0300311C, struct.pack("<HHH", key, key, key))
        try:
            frame()
        finally:
            cpu.write(0x0300311C, bytes(6))
            steps.append({"key": key, "state": cpu.read(CONTEXT, 4).hex(),
                "item": int.from_bytes(cpu.read(CONTEXT+0x44, 2), "little"),
                "quantity": int.from_bytes(cpu.read(CONTEXT+0x28, 2), "little"),
                "flags": cpu.read(CONTEXT+0x46, 1)[0]})
    before = ram(cpu)
    try:
        if field_entry:
            cpu.call(0x08A7FA9C, 0)
            cpu.call(0x08A7FAB2, 0)
        cpu.call(0x08A7A250)
        for _ in range(42):
            frame()
        # Menu state, input routing, item validity and edits stay real. These
        # callbacks only draw text/cursors or wait; native allocation stays real.
        for address in (0x08A7B590, 0x08A7B734, 0x08A7CD7C):
            cpu.install_stub(address, UI_STUBS[address])
        for address, label in ((0x08A7CDFC, "leaf text and rectangle renderer"),
                               (0x08A7CE60, "leaf decimal renderer"),
                               (0x08A7CDD8, "leaf rectangle renderer")):
            cpu.install_stub(address, label)
        ids = sorted(int(i) for i, item in profile["items"].items()
                     if int(i) and item["pocket"] == 2)
        pocket = profile["pockets"][1]
        cpu.write(pocket["address"], b"".join(struct.pack("<HH", i, 1) for i in ids[1:76]))
        before_pockets = {p["address"]: cpu.read(p["address"], p["capacity"]*4)
                          for p in profile["pockets"]}
        press(0x10)  # Root: bag tab.
        press(1)     # Enter bag.
        press(1)     # Edit item selector.
        # Pick item 182 (first legal key item) through the original cursor code.
        for _ in range(100):
            current = int.from_bytes(cpu.read(CONTEXT+0x44, 2), "little")
            if current == 182:
                break
            press(0x10 if current < 179 else 0x80)
        else:
            raise AssertionError("Item 182 not selected by bounded native keys")
        press(2)  # Exit selector.
        press(0x10)  # Quantity row.
        press(1)     # Edit quantity.
        for _ in range(10):
            press(0x10)
        press(0x40)
        assert cpu.read(CONTEXT+0x28, 2) == b"\x63\0", "UI quantity maximum"
        for _ in range(10):
            press(0x20)
        assert cpu.read(CONTEXT+0x28, 2) == bytes(2), "UI quantity minimum"
        press(0x40)
        assert cpu.read(CONTEXT+0x28, 2) == b"\1\0"
        press(2)
        # Up/down move between the grid rows, left/right its columns.
        for key in (0x80, 0x80, 0x80, 1):
            press(key)
        # Preserve the key history even when a newly found navigation path
        # requires another research turn; never synthesize an A-handler success.
        if hardened:
            assert cpu.visits[0x08A7FE00], "Navigation has not reached the safety wrapper"
            assert not cpu.visits[0x0809A084], "Full pocket reached unsafe native add"
            assert all(cpu.read(address, len(raw)) == raw for address, raw in before_pockets.items()), "UI full-pocket refusal changed a bag pocket"
        else:
            assert cpu.visits[0x0809A084], "Navigation has not reached AddBagItem"
        return [record(cpu, "ui-full-key-pocket", before,
                       {"key_steps": steps, "ui_quantity_bounds": [0, 99],
                        "hardened_full_pocket_refused": hardened,
                        "all_five_pockets_unchanged": hardened,
                        "native_script_entry_only": not field_entry,
                        "native_tasks_reset_for_fixture": reset_tasks})]
    except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
        row = failure_record(cpu, "ui-full-key-pocket", before, exc)
        row["key_steps"] = steps
        row["native_script_entry_only"] = not field_entry
        row["native_tasks_reset_for_fixture"] = reset_tasks
        return [row]


def add_ewram_mirror(cpu):
    """Bounded first EWRAM alias, enough to model the measured faulty stores.

    This is an explicit address model, not a native algorithm stub. The alias is
    initialized after fixture setup and native alias writes update canonical RAM.
    """
    from unicorn import UC_HOOK_MEM_WRITE
    cpu.uc.mem_map(0x02040000, 0x40000)
    cpu.write(0x02040000, cpu.read(0x02000000, 0x40000))
    writes = []
    def mirror(uc, access, address, size, value, user_data):
        canonical = address - 0x40000
        cpu.write(canonical, value.to_bytes(size, "little"))
        writes.append({"alias": address, "canonical": canonical,
                       "size": size, "value": value})
    cpu.uc.hook_add(UC_HOOK_MEM_WRITE, mirror, begin=0x02040000, end=0x0207FFFF)
    return writes


def verify_mirrored_full(rom, state, profile, version, *, pristine=False):
    """Measure real native overflow's mirrored write, not a claimed GBA crash."""
    cpu = BuiltinCPU(rom, state)
    cpu.version = version
    pocket = profile["pockets"][1]
    ids = sorted(int(i) for i, item in profile["items"].items()
                 if int(i) and item["pocket"] == 2)
    cpu.write(pocket["address"], b"".join(struct.pack("<HH", i, 1) for i in ids[1:76]))
    writes = add_ewram_mirror(cpu)
    before = ram(cpu)
    result = cpu.call(0x0809A084, ids[0], 1)
    assert result == 1
    assert writes == [{"alias": 0x0207C224, "canonical": 0x0203C224, "size": 2, "value": 182},
                      {"alias": 0x0207C226, "canonical": 0x0203C226, "size": 2, "value": 1}], writes
    row = record(cpu, "mirrored-native-full-key-pocket", before,
                 {"native_return": result, "mirror_writes": writes, "pristine_source_rom": pristine})
    row["status"] = "known_issue"
    row["finding"] = "Native index -1 was truncated to 65535; mirrored writes corrupt the preceding normal-pocket slot."
    return [row]


def verify_guard_cases(rom, state, profile, version):
    """Actual native functions behind authored guard; no native stubs."""
    from tools.builtin_bag_guard import CAVE
    rows = []
    # kind, existing first stack quantity, request, expected return, full pocket
    cases = (("empty-one", 0, 1, 1, False), ("empty-99", 0, 99, 1, False),
             ("zero", 0, 0, 0, False), ("over-ui-100", 0, 100, 0, False),
             ("stack-to-999", 975, 24, 1, False), ("stack-over-999", 975, 25, 0, False),
             ("full-matched-to-999", 975, 24, 1, True),
             ("full-matched-over-999", 975, 25, 0, True),
             ("full-unmatched", 0, 1, 0, True),
             ("duplicate-stacks-no-splitting", 999, 1, 0, True),
             ("invalid-item-zero", 0, 1, 0, False),
             ("invalid-item-750", 0, 1, 0, False),
             ("invalid-item-65535", 0, 1, 0, False),
             ("zero-capacity", 0, 1, 0, False),
             ("invalid-misaligned-pointer", 0, 1, 0, False),
             ("invalid-wrapping-pointer", 0, 1, 0, False))
    for name, old_qty, qty, expected_return, full in cases:
        cpu = BuiltinCPU(rom, state)
        cpu.version = version
        pocket = profile["pockets"][0]
        address, capacity = pocket["address"], pocket["capacity"]
        # Full matched cases intentionally use duplicate legal potion IDs to
        # exercise native stacking on a structurally full expanded pocket.
        entries = [(14, 1)] * capacity if full else [(0, 0)] * capacity
        if old_qty:
            entries[0] = (13, old_qty)
        if name == "duplicate-stacks-no-splitting":
            entries[1] = (13, 1)
        cpu.write(address, b"".join(struct.pack("<HH", *entry) for entry in entries))
        item = {"invalid-item-zero": 0, "invalid-item-750": 750,
                "invalid-item-65535": 65535}.get(name, 13)
        if name == "zero-capacity":
            h16(cpu, 0x02039890, 0)
        elif name == "invalid-misaligned-pointer":
            cpu.w32(0x0203988C, address+1)
        elif name == "invalid-wrapping-pointer":
            cpu.w32(0x0203988C, 0xFFFFFFFC)
            h16(cpu, 0x02039890, 1)
        writes = add_ewram_mirror(cpu)
        before = ram(cpu)
        try:
            result = cpu.call(CAVE, item, qty)
            assert result == expected_return, (result, expected_return)
            expected = bytearray(before[0x02000000])
            if expected_return:
                offset = address - 0x02000000
                expected[offset:offset+4] = struct.pack("<HH", item, old_qty + qty)
            assert cpu.read(0x02000000, 0x40000) == bytes(expected), "Unrelated EWRAM changed"
            assert not writes, writes
            assert cpu.read(0x03000000, 0x7D00) == before[0x03000000][:0x7D00], "Unrelated IWRAM changed"
            assert cpu.read(0x03007E00, 0x200) == before[0x03000000][0x7E00:]
            rows.append(record(cpu, "guard-" + name, before,
                {"return": result, "no_mirror_writes": True, "only_expected_pocket_bytes_changed": True,
                 "native_add_entries": cpu.visits[0x0809A084], "native_sanitize_entries": cpu.visits[0x0809A8A4],
                 "native_pocket_entries": cpu.visits[0x0809A9D8]}))
        except (AssertionError, RuntimeError, ValueError, struct.error) as exc:
            rows.append(failure_record(cpu, "guard-" + name, before, exc))
    # Actual supported key IDs: 75 distinct occupied / request a different one.
    cpu = BuiltinCPU(rom, state)
    cpu.version = version
    ids = sorted(int(i) for i, item in profile["items"].items() if int(i) and item["pocket"] == 2)
    cpu.write(profile["pockets"][1]["address"], b"".join(struct.pack("<HH", i, 1) for i in ids[1:76]))
    writes = add_ewram_mirror(cpu)
    before = ram(cpu)
    result = cpu.call(CAVE, ids[0], 1)
    assert result == 0 and not writes and not cpu.visits[0x0809A084]
    assert ram(cpu)[0x02000000] == before[0x02000000]
    rows.append(record(cpu, "guard-full-distinct-key-items", before,
                       {"return": result, "ewram_unchanged": True, "no_mirror_writes": True}))
    return rows


def verify_bag_guard(image, state, profile, version, *, field_entry=False, reset_tasks=False):
    """Public verifier for the caller's combined safety research candidate.

    No SHA-based address aliasing; image must already contain the authored guard
    and its pointer change. Native core logic stays real, first-mirror writes
    are explicitly modeled, and only leaf renderers/delay are stubbed for UI.
    """
    from tools.builtin_bag_guard import CALL_POINTER, CAVE, GUARD_CODE
    assert image[CAVE-0x08000000:CAVE-0x08000000+len(GUARD_CODE)] == GUARD_CODE
    assert struct.unpack_from("<I", image, CALL_POINTER-0x08000000)[0] == CAVE | 1
    rows = verify_guard_cases(image, state, profile, version)
    rows += verify_ui_bag(image, state, profile, version, hardened=True,
                          field_entry=field_entry, reset_tasks=reset_tasks)
    return {"cases": rows, "counts": dict(Counter(row["status"] for row in rows)),
            "scope": "Authored payload guard with actual native sanitizer/pocket/add and actual root key processors; UI leaf renderer and BIOS/DMA models; no persistence."}


if __name__ == "__main__":
    main()
