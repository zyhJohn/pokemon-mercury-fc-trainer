"""Build the offline sidequest catalog from the wiki's index and detail HTML.

Research tool only. The application reads the generated JSON without network or
BeautifulSoup. Run with --fetch to download missing detail pages into work/.
"""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError as exc:
    raise SystemExit("导入网页需安装 beautifulsoup4；应用运行不依赖它") from exc


BASE = "https://sum-light.github.io/azoth-wiki/sidequests/"
ROOT = Path(__file__).resolve().parents[1]


def clean(node):
    return node.get_text(" ", strip=True).removesuffix(" ¶").strip()


def table_rows(table):
    rows = []
    for row in table.select("tbody tr"):
        rows.append([clean(cell) for cell in row.find_all(["td", "th"], recursive=False)])
    return rows


def section(head):
    """Yield siblings until the next heading at the same or higher level."""
    level = int(head.name[1])
    for node in head.find_next_siblings():
        if re.fullmatch(r"h[1-6]", node.name or "") and int(node.name[1]) <= level:
            break
        yield node


def detail(page, ident):
    soup = BeautifulSoup(page, "html.parser")
    article = soup.select_one("article.md-content__inner")
    if article is None or article.select_one("h1") is None:
        raise ValueError(f"{ident}: 缺少详情正文")
    heading = clean(article.select_one("h1"))
    if not heading.startswith("任务" + ident + " · "):
        raise ValueError(f"{ident}: 详情标题与目录编号不符：{heading}")
    journal = article.select_one("#journal")
    rewards = article.select_one("#rewards")
    locations = article.select_one("#locations")
    story = article.select_one("#story")
    if any(x is None for x in (journal, rewards, locations, story)):
        raise ValueError(f"{ident}: 缺少详情章节")

    flag_line = next(
        (clean(n) for n in section(journal) if n.name == "p" and "Flag" in clean(n)),
        "",
    )
    match = re.fullmatch(r"接取 Flag (0x[0-9A-Fa-f]+) · 完成 Flag (0x[0-9A-Fa-f]+) 。", flag_line)
    if not match:
        raise ValueError(f"{ident}: 接取/完成标志格式未知：{flag_line}")
    stage_table = next((n for n in section(journal) if n.name == "table"), None)
    location_table = next((n if n.name == "table" else n.find("table")
                           for n in section(locations) if n.name == "table" or n.find("table")), None)
    if stage_table is None or location_table is None:
        raise ValueError(f"{ident}: 缺少阶段或地点表")

    stage_rows = table_rows(stage_table)
    stages = []
    for number, title, unlock in stage_rows:
        stage_number = int(re.match(r"\d+", number).group())
        stage_id = f"journal-{stage_number:02d}"
        stage_head = article.find("h3", id=stage_id)
        if stage_head is None:
            raise ValueError(f"{ident}: 缺少阶段 {stage_id}")
        objective = next((n for n in section(stage_head) if "sq-journal" in n.get("class", [])), None)
        stages.append({"number": stage_number, "title": title, "unlock": unlock,
                       "optional": "可选" in number,
                       "journal": clean(objective) if objective else ""})

    reward_groups = []
    for heading in section(rewards):
        if heading.name != "h3":
            continue
        table = next((n for n in section(heading) if n.name == "table"), None)
        if table is None:
            raise ValueError(f"{ident}: 奖励组缺少表格")
        reward_groups.append({"condition": clean(heading),
                              "items": [{"name": row[0], "kind": row[1]}
                                        for row in table_rows(table)]})

    scene_list = []
    for heading in section(story):
        if heading.name == "h3" and (heading.get("id") or "").startswith("scene-"):
            scene_list.append({"title": clean(heading),
                               "text": "\n".join(clean(n) for n in section(heading) if clean(n))})

    objective = article.select_one(".sq-objective:not(.sq-journal) p")
    notes = [clean(n) for n in article.select(".admonition") if clean(n)]
    return {
        "objective": clean(objective) if objective else "",
        "accept_flag": int(match.group(1), 16),
        "complete_flag": int(match.group(2), 16),
        "stages": stages,
        "rewards": reward_groups,
        "locations": [dict(zip(("number", "place", "trigger", "scenes"), row))
                      for row in table_rows(location_table)],
        "story": scene_list,
        "notes": notes,
        "details_text": clean(article),
    }


def build(index_html, pages_dir):
    soup = BeautifulSoup(index_html, "html.parser")
    rows = soup.select("[data-sq-entry]")
    if len(rows) != 96:
        raise ValueError(f"目录应有96项，实际{len(rows)}项")
    output = []
    for expected, row in enumerate(rows, 1):
        ident = clean(row.select_one(".sq-number"))
        title = clean(row.select_one(".sq-title"))
        summary = clean(row.select_one(".sq-row-main p"))
        if ident != f"{expected:03d}":
            raise ValueError(f"任务顺序异常：第{expected}项是{ident}")
        path = pages_dir / f"{ident}.html"
        page = path.read_bytes()
        info = detail(page, ident)
        output.append({
            "id": ident,
            "title": title,
            "summary": summary,
            "tags": [clean(x) for x in row.select(".sq-tag")],
            "source_url": BASE + ident + "/",
            "source_sha256": hashlib.sha256(page).hexdigest(),
            **info,
        })
        if title != clean(BeautifulSoup(page, "html.parser").select_one("h1")).split(" · ", 1)[1]:
            raise ValueError(f"{ident}: 目录和详情标题不一致")
    return {"source_url": BASE, "index_sha256": hashlib.sha256(index_html).hexdigest(),
            "count": len(output), "quests": output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "work/sidequests-source.html")
    parser.add_argument("--pages-dir", type=Path, default=ROOT / "work/sidequests-pages")
    parser.add_argument("--output", type=Path, default=ROOT / "sidequests.json")
    parser.add_argument("--fetch", action="store_true", help="下载缺失的详情页到本地缓存")
    args = parser.parse_args()
    index = args.index.read_bytes()
    if args.fetch:
        soup = BeautifulSoup(index, "html.parser")
        args.pages_dir.mkdir(parents=True, exist_ok=True)
        for row in soup.select("[data-sq-entry]"):
            ident = clean(row.select_one(".sq-number"))
            path = args.pages_dir / f"{ident}.html"
            if not path.exists():
                with urllib.request.urlopen(BASE + ident + "/", timeout=30) as response:
                    path.write_bytes(response.read())
    catalog = build(index, args.pages_dir)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导入 {catalog['count']} 项任务至 {args.output}")


if __name__ == "__main__":
    main()
