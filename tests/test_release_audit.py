"""Selective publishing must retain the archive and clean-source checks."""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import publish_releases


class ReleaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs/releases").mkdir(parents=True)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.old = self.make_release("0.2.14", False, archive=False)
        self.new = self.make_release("0.2.15", True)
        (self.root / "docs/releases/assets.json").write_text(json.dumps({
            "repository": "owner/repo", "releases": [self.old, self.new],
        }), encoding="utf-8")
        root_patch = patch.object(publish_releases, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        git_patch = patch.object(publish_releases.subprocess, "run")
        self.git = git_patch.start()
        self.addCleanup(git_patch.stop)

    def make_release(self, version, latest, archive=True, dirty=False):
        name = "MercuryTrainer-" + version + "-portable.zip"
        notes = "docs/releases/v" + version + ".md"
        (self.root / notes).write_text("notes " + version, encoding="utf-8")
        manifest = {"app_version": version, "source_revision": "a" * 40,
                    "source_dirty": dirty,
                    "files": {"README.md": hashlib.sha256(b"readme").hexdigest()}}
        path = self.archives / name
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("MercuryTrainer/build-info.json", json.dumps(manifest))
            bundle.writestr("MercuryTrainer/README.md", b"readme")
        data = path.read_bytes()
        if not archive:
            path.unlink()
        return {"version": version, "tag": "v" + version, "latest": latest,
                "asset_name": name, "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data), "source_commit": "a" * 40,
                "target_commit": "a" * 40, "notes": notes}

    def test_selected_release_does_not_require_old_local_archives(self):
        repository, artifacts = publish_releases.audit([self.archives], ["v0.2.15"])
        self.assertEqual(repository, "owner/repo")
        self.assertEqual([row[0]["tag"] for row in artifacts], ["v0.2.15"])
        self.git.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "Missing original archive"):
            publish_releases.audit([self.archives])

    def test_unknown_selection_is_refused_before_accessing_archives(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown release tag"):
            publish_releases.audit([self.archives], ["v0.2.99"])
        self.git.assert_not_called()

    def test_selected_archive_still_requires_exact_bytes_and_clean_source(self):
        path = self.archives / self.new["asset_name"]
        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        with self.assertRaisesRegex(RuntimeError, "differs from registry"):
            publish_releases.audit([self.archives], [self.new["tag"]])
        dirty = self.make_release("0.2.15", True, dirty=True)
        (self.root / "docs/releases/assets.json").write_text(json.dumps({
            "repository": "owner/repo", "releases": [self.old, dirty],
        }), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Source identity mismatch"):
            publish_releases.audit([self.archives], [dirty["tag"]])
        self.git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
