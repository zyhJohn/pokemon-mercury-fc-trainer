"""Catalog parsing and state safety checks independent of private ROM files."""

import json
import unittest
from pathlib import Path

from sidequest_data import filter_quests, load_catalog, read_snapshot


SHA = "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821"


class Memory:
    def __init__(self, data, fail=False):
        self.data = data
        self.fail = fail

    def read(self, address, length):
        if self.fail:
            raise OSError("bridge disconnected")
        return bytes(self.data.get(address + offset, 0) for offset in range(length))


class SidequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_catalog_has_only_96_ordered_tasks_and_all_detail_fields(self):
        self.assertEqual(len(self.catalog), 96)
        self.assertEqual([q["id"] for q in self.catalog], [f"{i:03d}" for i in range(1, 97)])
        for quest in self.catalog:
            self.assertTrue(quest["title"] and quest["summary"] and quest["objective"])
            self.assertTrue(quest["stages"] and quest["locations"] and quest["story"])
            self.assertTrue(quest["source_url"].endswith(quest["id"] + "/"))
            self.assertTrue(0x900 <= quest["accept_flag"] <= 0x18FF)
            self.assertTrue(0x900 <= quest["complete_flag"] <= 0x18FF)

    def test_complete_priority_progress_and_unread(self):
        catalog = [
            {"id": "001", "accept_flag": 0x900, "complete_flag": 0x901},
            {"id": "002", "accept_flag": 0x902, "complete_flag": 0x903},
            {"id": "003", "accept_flag": 0x904, "complete_flag": 0x905},
        ]
        layout = {"releases": {SHA: {"quest_flags": {q["id"]: q for q in catalog},
                                         "flag_ranges": [
            {"kind": "fixed_ram", "first_flag": 0x900, "last_flag": 0x18FF,
             "address": 0x0203B174}]}}}
        # 001 completed despite acceptance being clear; 002 in progress; 003 untouched.
        mem = Memory({0x0203B174: 0b00000110})
        result = read_snapshot(mem, {"rom_sha256": SHA}, layout, catalog)
        self.assertEqual(result, {"001": "complete", "002": "in_progress", "003": "incomplete"})
        self.assertEqual(read_snapshot(Memory({}, fail=True), {"rom_sha256": SHA}, layout, catalog),
                         {q["id"]: "unknown" for q in catalog})
        self.assertEqual(read_snapshot(mem, {"rom_sha256": "unknown"}, layout, catalog),
                         {q["id"]: "unknown" for q in catalog})

    def test_filters_keep_unknown_out_of_status_categories(self):
        quests = [{"id": f"{i:03d}", "title": f"任务{i}", "summary": "线索"}
                  for i in range(1, 5)]
        status = {"001": "complete", "002": "in_progress", "003": "incomplete"}
        self.assertEqual([q["id"] for q in filter_quests(quests, status, "all")],
                         ["001", "002", "003", "004"])
        self.assertEqual([q["id"] for q in filter_quests(quests, status, "incomplete")],
                         ["002", "003"])
        self.assertEqual([q["id"] for q in filter_quests(quests, status, "in_progress")],
                         ["002"])
        self.assertEqual([q["id"] for q in filter_quests(quests, status, "all", "任务3")],
                         ["003"])

    def test_version_specific_flags_and_extra_acceptance(self):
        layout = json.loads((Path(__file__).resolve().parents[1] / "sidequests_layout.json")
                            .read_text(encoding="utf-8"))
        v10 = layout["releases"][SHA]["quest_flags"]
        v12sha = "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896"
        v12 = layout["releases"][v12sha]["quest_flags"]
        self.assertEqual((v10["003"]["accept_flag"], v10["003"]["complete_flag"]),
                         (0xB99, 0xB9A))
        self.assertEqual((v12["003"]["accept_flag"], v12["003"]["complete_flag"]),
                         (0xB9A, 0xB9E))
        self.assertEqual(v10["091"]["accept_flag"], 0xACA)
        self.assertEqual(v12["091"]["accept_flag"], 0xACB)
        self.assertEqual(v12["001"]["extra_accept_flags"], [0x1044])
        self.assertEqual(v12["042"]["extra_accept_flags"], [0x1686])

        quest = [self.catalog[0]]
        address = 0x0203B174 + (0x1044 - 0x900) // 8
        mem = Memory({address: 1 << (0x1044 & 7)})
        self.assertEqual(read_snapshot(mem, {"rom_sha256": v12sha}, layout, quest),
                         {"001": "in_progress"})
        self.assertEqual(read_snapshot(mem, {"rom_sha256": SHA}, layout, quest),
                         {"001": "incomplete"})


if __name__ == "__main__":
    unittest.main()
