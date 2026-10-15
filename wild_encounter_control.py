"""Lease-bound control of the two audited native wild encounter ROM spans.

Only the bridge owns restoration. Journal files are evidence, never an arbitrary
ROM restore input. Calls perform I/O and belong on the application's worker.
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from trainer_core import Trainer
from wild_encounter_data import build_wild_patches, wild_layout


class WildEncounterControl:
    def __init__(self, mem, profile, backup_dir=None):
        self.mem = mem
        self.profile = profile
        self.backup_dir = Path(backup_dir or "backups") / "wild-encounters"
        self._lock = threading.RLock()
        self._generation = mem.connection_generation
        self._nonce = None
        self._journal = None
        self.active = False
        self.last_status = {"state": "inactive", "reason": "idle"}

    def _record(self, record):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._journal.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._journal)

    def _check_connection(self):
        if self.mem.connection_generation != self._generation:
            self.active = False
            self._nonce = None
            self.last_status = {"state": "error", "reason": "connection_changed"}
            raise OSError("野生遭遇控制连接已失效，请重新连接")

    def arm(self, species, level):
        patches = build_wild_patches(self.profile, species, level)
        layout = wild_layout(self.profile)
        with self._lock:
            self._check_connection()
            if "WILDNATIVE1" not in self.mem.capabilities:
                raise OSError("请重新加载支持野生遭遇控制的 mercury_bridge.lua")
            if self._nonce:
                self.stop()
            Trainer(self.mem, self.profile, self.backup_dir).verify(full_compatibility=True)
            flag = self.profile["battle_flag"]
            if self.mem.read(flag["address"], 1)[0] & flag["mask"]:
                raise ValueError("战斗中不能开启野生遭遇控制，请先结束战斗")
            for address, before, _ in patches:
                if self.mem.read_rom(address, len(before)) != before:
                    raise OSError("野生遭遇原始代码已变化，未启用")
            nonce = uuid4().hex
            crc = self.profile["rom_crc32"].lower()
            record = {"schema": 1, "kind": "temporary-wild-overlay", "status": "prepared",
                      "created": datetime.now(timezone.utc).isoformat(), "nonce": nonce,
                      "actual_rom_crc32": crc, "rom_identity": self.profile.get("rom_identity"),
                      "layout_sha256": layout["rom_sha256"], "species": species, "level": level,
                      "patches": [{"address": a, "before": b.hex(), "after": c.hex()} for a, b, c in patches]}
            self._journal = self.backup_dir / (nonce + ".json")
            self._record(record)
            self._nonce = nonce
            try:
                reply = self.mem.command(f"WILDON {crc} {layout['rom_sha256']} {species} {level} {nonce}")
                if reply != b"OK":
                    raise OSError("野生遭遇覆盖未返回成功确认")
                self.mem._wild_overlay = {"nonce": nonce, "crc": crc, "patches": patches,
                                          "generation": self.mem.connection_generation}
                for address, _, after in patches:
                    if self.mem.read_rom(address, len(after)) != after:
                        raise OSError("野生遭遇覆盖读回失败")
                record["status"] = "active"
                self._record(record)
                return self.status()
            except Exception as exc:
                self.active = False
                self.last_status = {"state": "error", "reason": str(exc)}
                record["status"] = "error"
                record["error"] = str(exc)
                try:
                    self.mem.command("WILDOFF " + nonce)
                    self.mem._wild_overlay = None
                    self._nonce = None
                except (OSError, ValueError):
                    pass
                self._record(record)
                raise

    def status(self):
        with self._lock:
            self._check_connection()
            parts = self.mem.command("WILDSTATE").decode("ascii").split()
            if len(parts) == 6 and parts[0] == "ACTIVE":
                if parts[1] != self._nonce or parts[2] != self.profile["rom_crc32"].lower():
                    self.active = False
                    self.last_status = {"state": "error", "reason": "ownership_changed"}
                    raise OSError("野生遭遇控制所有权不匹配")
                self.active = True
                self.last_status = {"state": "active", "nonce": parts[1], "species": int(parts[3]),
                                    "level": int(parts[4]), "lease_frames": int(parts[5])}
            elif len(parts) == 2 and parts[0] == "INACTIVE":
                self.active = False
                owner = self.mem._wild_overlay
                if owner:
                    for address, before, _ in owner["patches"]:
                        if self.mem.read_rom(address, len(before)) != before:
                            self.last_status = {"state": "error", "reason": "restore_mismatch"}
                            raise OSError("停用后原始代码未恢复，拒绝清除覆盖所有权")
                self._nonce = None
                self.mem._wild_overlay = None
                self.last_status = {"state": "inactive", "reason": parts[1]}
            else:
                self.active = False
                self.last_status = {"state": "error", "reason": " ".join(parts)}
            return dict(self.last_status)

    def heartbeat(self):
        with self._lock:
            self._check_connection()
            if self._nonce:
                try:
                    self.mem.command("WILDKEEP " + self._nonce)
                except OSError:
                    self.active = False
                    try:
                        self.status()
                    except OSError:
                        self.last_status = {"state": "error", "reason": "heartbeat_failed"}
                    raise
            return self.status()

    def stop(self):
        with self._lock:
            self._check_connection()
            if self._nonce:
                try:
                    self.mem.command("WILDOFF " + self._nonce)
                except OSError:
                    self.active = False
                    self.status()
                    raise
            result = self.status()
            if result["state"] != "inactive":
                raise OSError("野生遭遇覆盖尚未安全恢复：" + result.get("reason", result["state"]))
            if self._journal:
                record = json.loads(self._journal.read_text(encoding="utf-8"))
                record["status"] = "stopped"
                self._record(record)
            return result

    close = stop
