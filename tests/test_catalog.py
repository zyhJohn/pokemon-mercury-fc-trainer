import json
import unittest
from pathlib import Path
from wiki_catalog import merge_names, parse_index, search_rows


class CatalogTests(unittest.TestCase):
    def test_form_ids_do_not_collapse_to_national_dex(self):
        html = '<table><tr><td>x</td><td>3</td><td><a href="0003_a/">妙蛙花</a></td><td>草 毒</td></tr><tr><td>x</td><td>3</td><td><a href="0869_b/">超级妙蛙花</a></td><td>草 毒</td></tr></table>'
        rows = parse_index("pokemon", html)
        self.assertEqual([r["id"] for r in rows], [3, 869])
        self.assertEqual([r["dex"] for r in rows], [3, 3])
        self.assertEqual(search_rows(rows, "0869 草")[0]["name"], "超级妙蛙花")

    def test_reimport_keeps_verified_key_item_names(self):
        catalog = json.loads(Path("catalog.json").read_text(encoding="utf-8"))
        overrides = json.loads(Path("name_overrides.json").read_text(encoding="utf-8"))
        names = merge_names({"pers": {"0": "勤奋"}}, catalog, overrides)
        self.assertEqual(names["items"]["365"], "树果袋")
        self.assertEqual(names["items"]["364"], "招式学习器盒")
        self.assertEqual(names["items"]["341"], "HM03 冲浪")
        self.assertEqual(names["pers"]["0"], "勤奋")
        self.assertEqual(names["breeds"]["277"], "木守宫")


if __name__ == "__main__":
    unittest.main()
