"""Atomic JSON research reports which cannot overwrite their input files."""

import json
import os
import tempfile
from pathlib import Path


def write_report(path, data, *, inputs=()):
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("研究报告必须使用JSON文件")
    for original in map(Path, inputs):
        if path.resolve() == original.resolve() or (
            path.exists() and original.exists() and path.samefile(original)
        ):
            raise ValueError("研究报告不能覆盖输入文件或其链接")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
