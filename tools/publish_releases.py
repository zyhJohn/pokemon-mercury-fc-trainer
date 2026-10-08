"""Audit original portable archives, then optionally publish the release registry.

Defaults to a local audit. --publish uses cached Git credentials in memory only.
Existing assets are reused only when size and GitHub SHA-256 agree; mismatches stop.
"""

import argparse
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(directories, tags=None):
    registry = json.loads((ROOT / "docs/releases/assets.json").read_text("utf-8"))
    releases = registry["releases"]
    assert len({r["tag"] for r in releases}) == len(releases)
    assert sum(bool(r["latest"]) for r in releases) == 1
    if tags:
        requested = set(tags)
        missing = requested - {r["tag"] for r in releases}
        if missing:
            raise RuntimeError("Unknown release tag: " + ", ".join(sorted(missing)))
        releases = [r for r in releases if r["tag"] in requested]
    artifacts = []
    for release in releases:
        local_name = release["asset_name"] if release["version"] else "MercuryTrainer-portable.zip"
        matches = [directory / local_name for directory in directories if (directory / local_name).is_file()]
        if not matches:
            raise RuntimeError(f"Missing original archive: {local_name}")
        archive = matches[0]
        data = archive.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != release["sha256"] or len(data) != release["size"]:
            raise RuntimeError(f"Archive differs from registry: {local_name}")
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError(f"Corrupt archive: {local_name}")
            manifest = json.loads(bundle.read("MercuryTrainer/build-info.json"))
            for name, expected in manifest["files"].items():
                if hashlib.sha256(bundle.read("MercuryTrainer/" + name)).hexdigest() != expected:
                    raise RuntimeError(f"Manifest mismatch: {local_name}: {name}")
            if release["version"]:
                if (manifest.get("app_version") != release["version"]
                        or manifest.get("source_revision") != release["source_commit"]
                        or manifest.get("source_dirty") is not False):
                    raise RuntimeError(f"Source identity mismatch: {local_name}")
        subprocess.run(["git", "cat-file", "-e", release["target_commit"] + "^{commit}"],
                       cwd=ROOT, check=True, capture_output=True)
        notes = (ROOT / release["notes"]).resolve()
        if not notes.is_relative_to(ROOT / "docs/releases"):
            raise RuntimeError("Release notes outside registry directory")
        artifacts.append((release, data, notes.read_text("utf-8")))
        print(f"AUDITED {release['tag']} {len(data)} bytes {digest}", flush=True)
    return registry["repository"], artifacts


class GitHub:
    def __init__(self, repository, proxy):
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n", text=True, capture_output=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
            cwd=ROOT,
        )
        if result.returncode:
            raise RuntimeError("No cached GitHub credential available")
        credential = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        token = credential.get("password")
        if not token:
            raise RuntimeError("Cached GitHub credential has no token")
        self.headers = {"Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2026-03-10",
                        "User-Agent": "MercuryTrainer-release-archive"}
        self.base = "https://api.github.com/repos/" + repository
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": proxy} if proxy else {}))

    def request(self, method, path, payload=None, binary=False, missing_ok=False):
        url = path if path.startswith("https://") else self.base + path
        if urllib.parse.urlsplit(url).hostname not in {"api.github.com", "uploads.github.com"}:
            raise RuntimeError("Unexpected GitHub API host")
        headers = dict(self.headers)
        data = None
        if payload is not None:
            data = payload if binary else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/octet-stream" if binary else "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"GitHub {method} failed: HTTP {error.code}; rerun to reconcile") from None

    def tag_commit(self, tag):
        ref = self.request("GET", "/git/ref/tags/" + urllib.parse.quote(tag, safe=""), missing_ok=True)
        if ref is None:
            return None
        obj = ref["object"]
        while obj["type"] == "tag":
            obj = self.request("GET", "/git/tags/" + obj["sha"])["object"]
        return obj["sha"] if obj["type"] == "commit" else "not-a-commit"

    def upload(self, release, name, data):
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        assets = self.request("GET", f"/releases/{release['id']}/assets?per_page=100")
        existing = next((asset for asset in assets if asset["name"] == name), None)
        if existing is not None:
            asset = existing
        else:
            url = release["upload_url"].split("{", 1)[0] + "?" + urllib.parse.urlencode({"name": name})
            asset = self.request("POST", url, data, binary=True)
        if asset["state"] != "uploaded" or asset["size"] != len(data) or asset.get("digest") != digest:
            raise RuntimeError(f"Asset verification failed: {name}; existing asset was not overwritten")
        print(f"VERIFIED ASSET {name} {digest}", flush=True)

    def publish(self, info, data, notes):
        tag = info["tag"]
        target = self.tag_commit(tag)
        if target is not None and target != info["target_commit"]:
            raise RuntimeError(f"Existing tag has a different commit: {tag}")
        release = self.request("GET", "/releases/tags/" + tag, missing_ok=True)
        if release is None:
            # Drafts may not be returned by the tag endpoint; reconcile before creating.
            all_releases = self.request("GET", "/releases?per_page=100")
            release = next((r for r in all_releases if r["tag_name"] == tag), None)
        fields = {"tag_name": tag, "target_commitish": info["target_commit"],
                  "name": info["name"], "body": notes, "prerelease": info["prerelease"],
                  "make_latest": "true" if info["latest"] else "false"}
        if release is None:
            release = self.request("POST", "/releases", {**fields, "draft": True})
        elif release["body"] != notes or release["name"] != info["name"]:
            raise RuntimeError(f"Existing release notes differ: {tag}; review before updating")
        self.upload(release, info["asset_name"], data)
        self.upload(release, "SHA256SUMS.txt",
                    (info["sha256"] + "  " + info["asset_name"] + "\n").encode("utf-8"))
        self.request("PATCH", f"/releases/{release['id']}", {**fields, "draft": False})
        published = self.request("GET", "/releases/tags/" + tag)
        if (published["draft"] or published["body"] != notes
                or published["prerelease"] != info["prerelease"]
                or self.tag_commit(tag) != info["target_commit"]):
            raise RuntimeError(f"Published release verification failed: {tag}")
        print("PUBLISHED " + published["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", action="append", type=Path, required=True)
    parser.add_argument("--proxy", default="http://127.0.0.1:7078")
    parser.add_argument("--tag", action="append", help="Audit/publish only these registered tags (repeatable)")
    parser.add_argument("--publish", action="store_true", help="Create and publish GitHub Releases")
    args = parser.parse_args()
    repository, artifacts = audit(args.archive_dir, args.tag)
    if not args.publish:
        print(f"Local audit passed: {len(artifacts)} original archives; no remote changes")
        return
    api = GitHub(repository, args.proxy)
    for info, data, notes in artifacts:
        api.publish(info, data, notes)
    expected = next((info["tag"] for info, _, _ in artifacts if info["latest"]), None)
    if expected:
        latest = api.request("GET", "/releases/latest")
        if latest["tag_name"] != expected:
            raise RuntimeError("GitHub latest release differs from registry")
    print(f"ALL {len(artifacts)} SELECTED RELEASES VERIFIED")


if __name__ == "__main__":
    main()
