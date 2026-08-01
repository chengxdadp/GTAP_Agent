from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from gtap_aggregation import build_custom_mapping, parse_mapping_sections  # noqa: E402


DEFAULT_MAPPING = PROJECT_DIR / "runtime" / "gtapagg" / "GTAP10A" / "GTAP" / "default.txt"


class CustomAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_text = DEFAULT_MAPPING.read_text(encoding="utf-8", errors="replace")

    def test_can_split_china_without_changing_other_dimensions(self) -> None:
        text, metadata = build_custom_mapping(
            self.base_text,
            {
                "aggregation_name": "china_split",
                "region_groups": [{"name": "China", "description": "China", "members": ["China"]}],
            },
        )
        sections = parse_mapping_sections(text)
        china_line = next(line for line in sections[3] if line.lower().startswith("chn "))
        japan_line = next(line for line in sections[3] if line.lower().startswith("jpn "))

        self.assertEqual(china_line.rsplit("&", 1)[1].strip(), "China")
        self.assertEqual(japan_line.rsplit("&", 1)[1].strip(), "EastAsia")
        self.assertEqual(metadata["region_count"], 11)
        self.assertEqual(metadata["sector_count"], 10)

    def test_can_split_oil_seeds_by_original_code(self) -> None:
        text, metadata = build_custom_mapping(
            self.base_text,
            {
                "aggregation_name": "oilseeds_split",
                "sector_groups": [{"name": "OilSeeds", "members": ["osd"]}],
            },
        )
        sections = parse_mapping_sections(text)
        oilseed_line = next(line for line in sections[1] if line.lower().startswith("osd "))

        self.assertEqual(oilseed_line.rsplit("&", 1)[1].strip(), "OilSeeds")
        self.assertEqual(metadata["region_count"], 10)
        self.assertEqual(metadata["sector_count"], 11)

    def test_rejects_duplicate_member_assignment(self) -> None:
        with self.assertRaisesRegex(ValueError, "assigned more than once"):
            build_custom_mapping(
                self.base_text,
                {
                    "aggregation_name": "invalid",
                    "region_groups": [
                        {"name": "China", "members": ["chn"]},
                        {"name": "China2", "members": ["China"]},
                    ],
                },
            )

    def test_rejects_unsafe_group_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "contain only"):
            build_custom_mapping(
                self.base_text,
                {
                    "aggregation_name": "invalid",
                    "region_groups": [{"name": "China/USA", "members": ["chn"]}],
                },
            )


if __name__ == "__main__":
    unittest.main()
