"""Offline Azoth index: preserve detail IDs separately from national dex numbers."""

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

BASE = "https://sum-light.github.io/azoth-wiki/"
CATEGORIES = ("pokemon", "abilities", "items", "moves")


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.href = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row, self.href = [], None
        elif tag == "td" and self.row is not None:
            self.cell = []
        elif tag == "a" and self.cell is not None:
            self.href = dict(attrs).get("href")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row and self.href:
                self.rows.append((self.row, self.href))
            self.row = None


def parse_index(category, html):
    parser = IndexParser()
    parser.feed(html)
    rows, seen = [], set()
    for cells, href in parser.rows:
        match = re.match(r"^(\d+)_", href)
        if not match:
            continue
        ident = int(match[1])
        if ident in seen:
            raise ValueError(f"{category}: duplicate detail ID {ident}")
        seen.add(ident)
        offset = 1 if category == "pokemon" else 0
        if len(cells) < offset + 3:
            raise ValueError(f"{category}: incomplete row {ident}")
        display_id = int(cells[offset]) if cells[offset].isdigit() else None
        if category != "pokemon" and display_id != ident:
            raise ValueError(f"{category}: ID mismatch {ident}")
        rows.append(
            dict(
                id=ident,
                dex=display_id if category == "pokemon" else None,
                name=cells[offset + 1],
                fields=cells[offset + 2 :],
                url=urljoin(BASE + category + "/", href),
            )
        )
    if not rows:
        raise ValueError(f"{category}: empty index")
    return sorted(rows, key=lambda row: row["id"])


def build_catalog(directory):
    result = {"schema": 1, "sources": {}, "categories": {}}
    for category in CATEGORIES:
        raw = (Path(directory) / (category + ".html")).read_bytes()
        result["categories"][category] = parse_index(category, raw.decode("utf-8-sig"))
        result["sources"][category] = dict(
            url=BASE + category + "/", sha256=hashlib.sha256(raw).hexdigest()
        )
    return result


def search_rows(rows, query):
    terms = query.casefold().split()
    return [
        row
        for row in rows
        if all(
            term
            in " ".join(
                [
                    str(row["id"]),
                    f"{row['id']:04d}",
                    str(row.get("dex") or ""),
                    row["name"],
                    *row["fields"],
                ]
            ).casefold()
            for term in terms
        )
    ]


def merge_names(names, catalog, overrides=None):
    result = dict(names)
    for category, key in [
        ("pokemon", "breeds"),
        ("items", "items"),
        ("moves", "skills"),
        ("abilities", "specs"),
    ]:
        # Replace legacy tables; do not carry unrelated IDs into this ROM catalog.
        result[key] = {
            "0": "无",
            **{str(r["id"]): r["name"] for r in catalog["categories"][category]},
        }
    for key, values in (overrides or {}).items():
        if key not in ("breeds", "items", "skills", "specs"):
            raise ValueError(f"unknown name override category: {key}")
        result.setdefault(key, {}).update(values)
    return result


def load_catalog(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
