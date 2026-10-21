"""Reproduce the bounded Potion 5->6 native-core experiment in a new private folder.

This is a fixture-specific research tool, not runtime compatibility registration.
Supply a trusted native core. Recipes are validated JSON data, never Python code.
Only battery RAM is injected; CPU RAM and emulator states are never imported.
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.builtin_safe_candidate import build_safe_candidate
from tools.mgba_libretro import SAVE_RAM, LibretroCore, frame_to_rgb

SCHEMA_VERSION = 1
BATTERY_SIZE = 0x20000
MAX_ACTIONS = 1000
MAX_TOTAL_FRAMES = 30000
POTION_ADDRESS = 0x0203BB20
SUPPORTED_VERSIONS = {"1.0", "1.2", "1.33"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"Recipe requires exactly these keys: {sorted(required)}")


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def _buttons(value):
    if not isinstance(value, list):
        raise TypeError("buttons must be a list of unique joypad IDs")
    for button in value:
        _integer(button, 0, 15, "button")
    if len(set(value)) != len(value):
        raise ValueError("buttons must be unique")


def validate_steps(steps):
    if not isinstance(steps, list) or not steps or len(steps) > MAX_ACTIONS:
        raise ValueError("Recipe needs a bounded nonempty list of steps")
    total = 0
    for step in steps:
        if not isinstance(step, dict):
            raise TypeError("Recipe action must be an object")
        op = step.get("op")
        if op == "run":
            _keys(step, ("op", "frames"))
            total += _integer(step["frames"], 1, 5000, "frames")
        elif op == "tap":
            _keys(step, ("op", "button", "hold", "release"))
            _integer(step["button"], 0, 15, "button")
            total += _integer(step["hold"], 1, 120, "hold")
            total += _integer(step["release"], 1, 5000, "release")
        elif op == "buttonsteps":
            _keys(step, ("op", "steps"))
            if (
                not isinstance(step["steps"], list)
                or not 1 <= len(step["steps"]) <= 100
            ):
                raise ValueError("buttonsteps needs 1..100 steps")
            for item in step["steps"]:
                _keys(item, ("buttons", "frames"))
                _buttons(item["buttons"])
                total += _integer(item["frames"], 1, 5000, "frames")
        elif op == "assert_memory":
            _keys(step, ("op", "address", "expected"))
            if not isinstance(step["address"], str) or not re.fullmatch(
                r"[0-9a-f]{8}", step["address"]
            ):
                raise ValueError("Memory address must be eight lowercase hex digits")
            if not isinstance(step["expected"], str) or not re.fullmatch(
                r"(?:[0-9a-f]{2}){1,64}", step["expected"]
            ):
                raise ValueError("Expected memory must be 1..64 lowercase hex bytes")
            address, size = int(step["address"], 16), len(step["expected"]) // 2
            if not 0x02000000 <= address < address + size <= 0x02040000:
                raise ValueError("Recipe assertions must stay inside exact GBA EWRAM")
        elif op == "capture":
            _keys(step, ("op", "name"))
            if not isinstance(step["name"], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}", step["name"]
            ):
                raise ValueError("Capture name must be a safe filename stem")
        else:
            raise ValueError(f"Unsupported recipe action: {op!r}")
    if total > MAX_TOTAL_FRAMES:
        raise ValueError("Recipe exceeds total frame budget")
    names = [step["name"] for step in steps if step["op"] == "capture"]
    if len(set(names)) != len(names):
        raise ValueError("Capture names must be unique")
    return total


def validate_recipe(recipe):
    _keys(recipe, ("schema_version", "fixture", "versions", "record", "reload"))
    if (
        type(recipe["schema_version"]) is not int
        or recipe["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("Unsupported recipe schema")
    if recipe["fixture"] != "potion-5-to-6":
        raise ValueError("Only the verified potion-5-to-6 fixture is supported")
    versions = recipe["versions"]
    if (
        not isinstance(versions, list)
        or not versions
        or any(not isinstance(v, str) or v not in SUPPORTED_VERSIONS for v in versions)
    ):
        raise ValueError("Unsupported recipe ROM versions")
    for phase in ("record", "reload"):
        validate_steps(recipe[phase])
    expected_before = {
        "op": "assert_memory",
        "address": "0203bb20",
        "expected": "0d000500",
    }
    expected_after = {
        "op": "assert_memory",
        "address": "0203bb20",
        "expected": "0d000600",
    }
    if (
        expected_before not in recipe["record"]
        or expected_after not in recipe["record"]
    ):
        raise ValueError(
            "Record recipe must assert the verified Potion 5->6 transition"
        )
    if recipe["record"].index(expected_before) >= recipe["record"].index(
        expected_after
    ):
        raise ValueError("Potion fixture assertions are out of order")
    if expected_after not in recipe["reload"]:
        raise ValueError("Reload recipe must assert Potion quantity 6")
    if recipe["record"][-1] != expected_after:
        raise ValueError("Record must finish with the quantity-6 memory assertion")
    active_record = [
        step
        for step in recipe["record"]
        if step["op"] not in ("capture", "assert_memory")
    ]
    if not active_record or active_record[-1] != {
        "op": "tap",
        "button": 8,
        "hold": 6,
        "release": 1200,
    }:
        raise ValueError("Record must finish normal-menu saving with A6/release1200")
    expected_reload = [
        {"op": "run", "frames": 600},
        {"op": "tap", "button": 3, "hold": 2, "release": 120},
        {"op": "tap", "button": 8, "hold": 2, "release": 300},
        {"op": "tap", "button": 8, "hold": 2, "release": 120},
        {"op": "tap", "button": 8, "hold": 2, "release": 180},
        expected_after,
    ]
    if [
        step for step in recipe["reload"] if step["op"] != "capture"
    ] != expected_reload:
        raise ValueError(
            "Reload permits only the verified battery bootstrap and quantity assertion"
        )
    return recipe


def load_recipe(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate recipe key")
            result[key] = value
        return result

    return validate_recipe(
        json.loads(
            Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs
        )
    )


def reserve_output(path, inputs):
    """Exclusively create a fresh folder after resolving every existing input."""
    resolved = [Path(item).resolve(strict=True) for item in inputs]
    if any(not item.is_file() for item in resolved):
        raise ValueError("All inputs must be existing regular files")
    target = Path(path).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("Output must be a new, nonexistent directory")
    real_target = target.resolve()
    if any(real_target == item or real_target in item.parents for item in resolved):
        raise ValueError("Output aliases an input or its parent")
    target.mkdir(parents=False, exist_ok=False)
    return target.resolve(strict=True)


def write_new(path, data):
    with Path(path).open("xb") as handle:
        handle.write(data)


def write_json_new(path, payload):
    write_new(
        path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def encode_png(frame):
    rgb = frame_to_rgb(frame)
    width, height = frame["width"], frame["height"]

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    scanlines = b"".join(
        b"\x00" + rgb[row * width * 3 : (row + 1) * width * 3] for row in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )
    return png, sha256(rgb)


def execute_steps(core, steps, output, events):
    validate_steps(steps)
    for index, step in enumerate(steps):
        event = {"index": index, "op": step["op"], "passed": False}
        events.append(event)
        op = step["op"]
        if op == "run":
            core.run(step["frames"])
        elif op == "tap":
            core.run(step["hold"], (step["button"],))
            core.run(step["release"])
        elif op == "buttonsteps":
            for item in step["steps"]:
                core.run(item["frames"], item["buttons"])
        elif op == "assert_memory":
            expected = bytes.fromhex(step["expected"])
            actual = core.read_memory(int(step["address"], 16), len(expected))
            event.update(
                address=step["address"], expected=step["expected"], actual=actual.hex()
            )
            if actual != expected:
                raise ValueError(
                    f"Fixture assertion failed at {step['address']}: expected {expected.hex()}, got {actual.hex()}"
                )
        elif op == "capture":
            if core.latest_video is None:
                raise ValueError("No native video frame available for capture")
            png, pixel_hash = encode_png(core.latest_video)
            name = step["name"] + ".png"
            write_new(output / name, png)
            event.update(image=name, rgb_sha256=pixel_hash, png_sha256=sha256(png))
        if core.errors:
            raise ValueError("Native core callback error")
        event.update(frame_count=core.frame_count, passed=True)


def battery_bytes(data):
    if len(data) not in (BATTERY_SIZE, BATTERY_SIZE + 16):
        raise ValueError("Input save must be exactly 0x20000 or 0x20010 bytes")
    return data[:BATTERY_SIZE]


def _save_validation(rom, save):
    from tools.inspect_mercury_save import analyze_save

    result = analyze_save(rom, save)
    slots = result.get("slots", [])
    if not any(
        slot.get("complete") is True and slot.get("base_checksums_valid") is True
        for slot in slots
    ):
        raise ValueError("Battery has no complete slot with valid base checksums")
    return result


def _load_core(core, rom, battery, expected_version, report):
    info = core.load_game(rom)
    report["core"] = info
    if info.get("library_version") != expected_version:
        raise ValueError("Core version does not match the required pin")
    if len(core.get_memory(SAVE_RAM)) != BATTERY_SIZE:
        raise ValueError(
            "Core SAVE_RAM size differs from the verified 0x20000-byte fixture"
        )
    core.set_memory(SAVE_RAM, battery)
    report.update(
        battery_injected_bytes=len(battery),
        state_load_calls=0,
        direct_ram_edits=False,
        rtc_injected=False,
        rtc_note="Only 0x20000 battery bytes are injected; optional 16-byte RTC tail is not injected.",
    )


def _worker_command(args, output):
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--reload-worker",
        "--core",
        str(Path(args.core).resolve()),
        "--expected-core-version",
        args.expected_core_version,
        "--source",
        str(output / "candidate.gba"),
        "--save",
        str(output / "saved.sav"),
        "--output",
        str(output / "reload"),
        "--parent-pid",
        str(os.getpid()),
        "--recipe",
        str(output / "recipe.json"),
    ]


def run_reload_process(args, output, report):
    process = subprocess.Popen(
        _worker_command(args, output),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise ValueError("Fresh-process reload exceeded its time limit") from None
    report["reload_process_returncode"] = process.returncode
    if process.returncode != 0:
        raise ValueError("Fresh-process reload failed: " + stderr[-2000:])
    child = json.loads(stdout)
    if not isinstance(child, dict) or child.get("all_checks_passed") is not True:
        raise ValueError("Reload child did not report successful checks")
    if (
        child.get("pid") != process.pid
        or child.get("parent_pid") != os.getpid()
        or process.pid == os.getpid()
    ):
        raise ValueError("Reload process identity mismatch")
    for key in ("core_sha256", "rom_sha256", "battery_sha256"):
        if child.get(key) != report[key]:
            raise ValueError("Reload identity mismatch: " + key)
    if (
        child.get("state_load_calls") != 0
        or child.get("direct_ram_edits") is not False
        or child.get("battery_injected_bytes") != BATTERY_SIZE
        or child.get("rtc_injected") is not False
        or child.get("core", {}).get("library_version") != args.expected_core_version
    ):
        raise ValueError("Reload violated battery-only verification contract")
    stored = json.loads((output / "reload" / "result.json").read_text(encoding="utf-8"))
    if stored != child:
        raise ValueError("Reload stdout and saved result disagree")
    report["reload"] = child


def _base_report(args):
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": "potion-5-to-6",
        "all_checks_passed": False,
        "pid": os.getpid(),
        "core_sha256": sha256(Path(args.core).read_bytes()),
        "events": [],
        "runtime_compatibility_registration": "not assessed by native experiment",
        "state_load_calls": 0,
        "direct_ram_edits": False,
    }


def perform_record(args, output, report):
    source, template, patch = (
        Path(path).read_bytes() for path in (args.source, args.template, args.ups)
    )
    candidate = build_safe_candidate(source, template, patch)
    recipe_path = (
        Path(args.recipe)
        if args.recipe
        else Path(__file__).with_name("native_recipes")
        / (
            "builtin-v10.json"
            if candidate.version == "1.0"
            else "builtin-v12-plus.json"
        )
    )
    recipe = load_recipe(recipe_path)
    if candidate.version not in recipe["versions"]:
        raise ValueError("Recipe does not support candidate ROM version")
    save = battery_bytes(Path(args.save).read_bytes())
    report.update(
        version=candidate.version,
        source_sha256=sha256(source),
        rom_sha256=sha256(candidate.image),
        input_battery_sha256=sha256(save),
        recipe_sha256=sha256(recipe_path.read_bytes()),
    )
    report["input_save_analysis"] = _save_validation(candidate.image, save)
    write_new(output / "candidate.gba", candidate.image)
    write_new(output / "input-battery.sav", save)
    write_json_new(output / "recipe.json", recipe)
    system = output / "system"
    system.mkdir()
    with LibretroCore(args.core, system) as core:
        _load_core(
            core, output / "candidate.gba", save, args.expected_core_version, report
        )
        execute_steps(core, recipe["record"], output, report["events"])
        saved = core.get_memory(SAVE_RAM)
        if len(saved) != BATTERY_SIZE:
            raise ValueError("Exported battery has an unexpected size")
        report.update(frame_count=core.frame_count, callback_errors=list(core.errors))
    report["output_save_analysis"] = _save_validation(candidate.image, saved)
    write_new(output / "saved.sav", saved)
    report.update(battery_sha256=sha256(saved), battery_size=len(saved))
    run_reload_process(args, output, report)
    report["all_checks_passed"] = True


def perform_reload(args, output, report):
    if args.parent_pid is None or args.parent_pid == os.getpid():
        raise ValueError("Reload requires a distinct parent process identity")
    recipe = load_recipe(args.recipe)
    save = battery_bytes(Path(args.save).read_bytes())
    rom = Path(args.source).read_bytes()
    report.update(
        parent_pid=args.parent_pid,
        rom_sha256=sha256(rom),
        battery_sha256=sha256(save),
        battery_size=len(save),
        fresh_process=True,
    )
    report["save_analysis"] = _save_validation(rom, save)
    system = output / "system"
    system.mkdir()
    with LibretroCore(args.core, system) as core:
        _load_core(core, args.source, save, args.expected_core_version, report)
        execute_steps(core, recipe["reload"], output, report["events"])
        report.update(frame_count=core.frame_count, callback_errors=list(core.errors))
    report["all_checks_passed"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "source", "save", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--expected-core-version", required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--ups", type=Path)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--reload-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.expected_core_version.strip():
        parser.error("--expected-core-version must be nonempty")
    if args.reload_worker and args.recipe is None:
        parser.error("Reload worker requires --recipe")
    if not args.reload_worker and (args.template is None or args.ups is None):
        parser.error("Record requires --template and --ups")
    inputs = [args.core, args.source, args.save]
    inputs.extend(
        path for path in (args.template, args.ups, args.recipe) if path is not None
    )
    output, report = None, {"all_checks_passed": False}
    try:
        output = reserve_output(args.output, inputs)
        report = _base_report(args)
        if args.reload_worker:
            perform_reload(args, output, report)
        else:
            perform_record(args, output, report)
    except Exception as exc:  # noqa: BLE001 - always leave an explicit failed experiment result.
        report.update(all_checks_passed=False, error=f"{type(exc).__name__}: {exc}")
    if output is not None:
        write_json_new(output / "result.json", report)
    # The worker's full result travels only through its parent's captured pipe.
    # Keep the interactive invocation concise; private diagnostics stay on disk.
    if args.reload_worker:
        console = report
    else:
        assertions = [
            event
            for event in report.get("events", [])
            if event.get("op") == "assert_memory"
        ]
        actual = assertions[-1].get("actual") if assertions else None
        potion = None
        if actual and len(actual) == 8:
            value = bytes.fromhex(actual)
            potion = {
                "id": int.from_bytes(value[:2], "little"),
                "quantity": int.from_bytes(value[2:], "little"),
            }
        console = {
            "all_checks_passed": report["all_checks_passed"],
            "version": report.get("version"),
            "parent_pid": report.get("pid"),
            "fresh_pid": report.get("reload", {}).get("pid"),
            "frames": report.get("frame_count"),
            "potion": potion,
            "report": str(output / "result.json") if output else None,
        }
        if "error" in report:
            console["error"] = report["error"]
    print(json.dumps(console, ensure_ascii=False))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
