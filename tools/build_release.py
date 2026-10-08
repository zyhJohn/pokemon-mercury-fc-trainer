"""Build a portable trainer bundle; does not copy ROMs, saves or personal backups."""

import subprocess
import sys
import shutil
import hashlib
import json
import zipfile
import importlib.util
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import APP_VERSION


def main():
    if importlib.util.find_spec("lupa") is None:
        raise SystemExit(
            "Install requirements-dev.txt: the release requires actual Lua tests, not skipped tests."
        )
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-q"], cwd=ROOT, check=True
    )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        "MercuryTrainer",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "work" / "build"),
        "--specpath",
        str(ROOT / "work"),
    ]
    for file in [
        "names.json",
        "catalog.json",
        "rom_profile.json",
        "rom_profile_v12.json",
        "sidequests.json",
        "sidequests_layout.json",
        "game_fields_layout.json",
        "player_rival_layout.json",
        "daycare_layout.json",
        "distributions.json",
        "pokemon_creation_layout.json",
    ]:
        command += ["--add-data", str(ROOT / file) + ";."]
    command.append(str(ROOT / "trainer_gui.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    executable = ROOT / "dist" / "MercuryTrainer.exe"
    subprocess.run([str(executable), "--self-test"], cwd=ROOT, check=True, timeout=30)
    for file in ["mercury_bridge.lua", "启动修改器.bat", "启动修改器.ps1", "README.md"]:
        shutil.copy2(ROOT / file, ROOT / "dist" / file)
    files = [
        "MercuryTrainer.exe",
        "mercury_bridge.lua",
        "启动修改器.bat",
        "启动修改器.ps1",
        "README.md",
    ]
    for name in [
        "verified-layout.md",
        "development-status.md",
        "next-stage-plan.md",
        "cross-machine-handoff.md",
        "requirements-audit-2026-10-05.md",
        "requirements-audit-2026-10-06.md",
        "requirements-audit-2026-10-07.md",
        "requirements-audit-2026-10-08.md",
        "field-edit-evidence-2026-10-08.md",
        "daycare-audit-2026-10-08.md",
        "encounter-method-audit-2026-10-08.md",
        "player-avatar-audit-2026-10-08.md",
        "daily-events-audit-2026-10-08.md",
        "distribution-compatibility.md",
    ]:
        source = ROOT / "docs" / name
        if source.exists():
            target = ROOT / "dist" / "docs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files.append("docs/" + name)
    manifest = {
        "app_version": APP_VERSION,
        "built_at": datetime.now().astimezone().isoformat(),
        "bridge_protocol": 3,
        "tests": "unittest suite passed with Lua runtime installed",
        "exe_self_test": "passed",
        "files": {
            name: hashlib.sha256((ROOT / "dist" / name).read_bytes()).hexdigest()
            for name in files
        },
    }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    manifest["source_revision"] = (
        revision.stdout.strip() if revision.returncode == 0 else None
    )
    manifest["source_dirty"] = (
        bool(status.stdout.strip()) if status.returncode == 0 else None
    )
    (ROOT / "dist" / "build-info.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files.append("build-info.json")
    # Explicit allowlist: never bundle backups, diagnostics, ROMs or saves.
    with zipfile.ZipFile(
        ROOT / "dist" / "MercuryTrainer-portable.zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        for name in files:
            archive.write(ROOT / "dist" / name, "MercuryTrainer/" + name)
    # Exercise exactly the shipped archive, outside the source tree. A Chinese
    # path with spaces also catches accidental dependence on shell quoting.
    with tempfile.TemporaryDirectory(
        prefix="便携包 自检 ", dir=ROOT / "work"
    ) as folder:
        with zipfile.ZipFile(ROOT / "dist" / "MercuryTrainer-portable.zip") as archive:
            expected = {"MercuryTrainer/" + name for name in files}
            if set(archive.namelist()) != expected or len(archive.namelist()) != len(
                files
            ):
                raise RuntimeError(
                    "Release archive contains unexpected or duplicate files"
                )
            if archive.testzip() is not None:
                raise RuntimeError("Release archive failed CRC verification")
            # All members were checked against the explicit relative allowlist.
            archive.extractall(folder)
        unpacked = Path(folder) / "MercuryTrainer"
        for name, digest in manifest["files"].items():
            if hashlib.sha256((unpacked / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Release file hash mismatch: " + name)
        subprocess.run(
            [str(unpacked / "MercuryTrainer.exe"), "--self-test"],
            cwd=unpacked,
            check=True,
            timeout=30,
        )
    print("Release ready:", ROOT / "dist")


if __name__ == "__main__":
    main()
