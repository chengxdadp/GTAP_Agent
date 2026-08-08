from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gtap_scenario import (  # noqa: E402
    GTAP_CHECK_ON_READ_LINES,
    build_scenario_cmf,
    load_aggregation_aliases,
    normalize_closure_patches,
    resolve_baseline_context,
)


def load_shock_module():
    path = SCRIPTS_DIR / "06_apply_policy_shock_modifications.py"
    spec = importlib.util.spec_from_file_location("gtap_shock_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScenarioContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = resolve_baseline_context("original_2014")
        cls.aliases = load_aggregation_aliases(cls.baseline["aggregation_mapping"])
        cls.shocks = load_shock_module()

    def test_baseline_is_explicit_and_mapping_bound(self) -> None:
        self.assertEqual(self.baseline["baseline_id"], "original_2014")
        self.assertEqual(self.baseline["baseline_year"], 2014)
        self.assertTrue(Path(self.baseline["basedata"]).is_file())
        self.assertEqual(self.aliases["region"][0]["unitedstates"], "NAmerica")
        self.assertEqual(self.aliases["sector"][0]["soybeans"], "GrainsCrops")

    def test_closure_patch_resolves_region_and_embeds_context(self) -> None:
        patches = normalize_closure_patches(
            [{"type": "gdp_target_tfp", "regions": ["China"]}],
            *self.aliases["region"],
        )
        cmf, context = build_scenario_cmf("GDP target", self.baseline, patches)
        self.assertIn('Swap avareg("EastAsia") = qgdp("EastAsia");', cmf)
        self.assertEqual(context["baseline_id"], "original_2014")
        self.assertEqual(context["closure_id"], "standard_policy_patched")

    def test_cmf_checks_structure_without_legacy_coefficient_aliases(self) -> None:
        cmf, _ = build_scenario_cmf("Read checks", self.baseline, [])
        for line in GTAP_CHECK_ON_READ_LINES:
            self.assertIn(line, cmf)
        self.assertNotIn("check-on-read all", cmf)

    def test_expanded_shock_codes_resolve_dimensions(self) -> None:
        cases = [
            (
                {"type": "gtap_variable", "code": "tx", "commodity": "osd", "exporter": "usa", "value": 2},
                'Shock tx("GrainsCrops","NAmerica") = 2.000000;',
            ),
            (
                {"type": "gtap_variable", "code": "aoall", "sector": "osd", "region": "China", "value": 1.5},
                'Shock aoall("GrainsCrops","EastAsia") = 1.500000;',
            ),
            (
                {"type": "gtap_variable", "code": "qo", "factor": "Capital", "region": "China", "value": 3},
                'Shock qo("Capital","EastAsia") = 3.000000;',
            ),
        ]
        for modification, expected in cases:
            with self.subTest(code=modification["code"]):
                item = self.shocks.resolve_explicit_modification(modification, self.aliases, {}, set())
                self.assertEqual(item["shock_line"], expected)

    def test_closure_sensitive_shock_requires_swap(self) -> None:
        modification = {"type": "gtap_variable", "code": "qgdp", "region": "China", "value": 1}
        with self.assertRaisesRegex(ValueError, "gdp_target_tfp"):
            self.shocks.resolve_explicit_modification(modification, self.aliases, {}, set())

        swaps = {'Swap avareg("EastAsia") = qgdp("EastAsia");'}
        item = self.shocks.resolve_explicit_modification(modification, self.aliases, {}, swaps)
        self.assertEqual(item["shock_line"], 'Shock qgdp("EastAsia") = 1.000000;')

        with self.assertRaisesRegex(ValueError, "avareg is endogenous"):
            self.shocks.resolve_explicit_modification(
                {"type": "gtap_variable", "code": "avareg", "region": "China", "value": 1},
                self.aliases,
                {},
                swaps,
            )


if __name__ == "__main__":
    unittest.main()
