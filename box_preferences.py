"""Local box locks keyed by ROM and a user-selected save path, never save bytes."""

import hashlib
import json
import os
from pathlib import Path


class BoxPreferences:
    def __init__(self, path):
        self.path = Path(path)

    @staticmethod
    def key(rom_sha256, save_path):
        path = os.path.normcase(str(Path(save_path).resolve()))
        return hashlib.sha256((rom_sha256 + "\0" + path).encode("utf-8")).hexdigest()

    def _read(self):
        if not self.path.exists():
            return {"schema": 1, "saves": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or not isinstance(data.get("saves"), dict):
            raise ValueError("本地盒锁配置格式异常，未加载；请保留文件后检查")
        return data

    def load(self, rom_sha256, save_path):
        values = self._read()["saves"].get(self.key(rom_sha256, save_path), [])
        if not isinstance(values, list) or any(
            type(i) is not int or not 0 <= i < 25 for i in values
        ):
            raise ValueError("本地盒锁配置包含无效盒号")
        return set(values)

    def save(self, rom_sha256, save_path, locked):
        values = sorted(set(locked))
        if any(type(i) is not int or not 0 <= i < 25 for i in values):
            raise ValueError("盒号无效")
        data = self._read()
        data["saves"][self.key(rom_sha256, save_path)] = values
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)
