from __future__ import annotations

import unittest
from pathlib import Path

from web.agent import BASE_SYSTEM_PROMPT, TOOLS


PROJECT_DIR = Path(__file__).resolve().parents[1]


def tool_function(name: str) -> dict:
    for tool in TOOLS:
        function = tool.get("function") or {}
        if function.get("name") == name:
            return function
    raise AssertionError(f"Tool not found: {name}")


class AgentPromptContractTests(unittest.TestCase):
    def test_standard_closure_routes_directly_to_shock_tool(self) -> None:
        prompt = BASE_SYSTEM_PROMPT.lower()
        self.assertIn("do not call modify_gtap_closure", prompt)
        self.assertIn("modify_shock_cmf directly", prompt)
        self.assertIn("omit base_cmf", prompt)

        closure = tool_function("modify_gtap_closure")
        self.assertIn("do not call for a standard", closure["description"].lower())
        self.assertEqual(closure["parameters"]["properties"]["modifications"]["minItems"], 1)

        shock = tool_function("modify_shock_cmf")
        self.assertIn("when base_cmf is omitted", shock["description"].lower())
        self.assertEqual(shock["parameters"]["properties"]["modifications"]["minItems"], 1)

    def test_existing_aggregation_and_name_collision_are_safe(self) -> None:
        aggregate = tool_function("aggregate_gtap_model")["description"].lower()
        custom = tool_function("aggregate_custom_gtap_model")
        overwrite = custom["parameters"]["properties"]["overwrite"]["description"].lower()
        self.assertIn("never call", aggregate)
        self.assertIn("current or existing aggregation", aggregate)
        self.assertIn("name collision", overwrite)
        self.assertIn("explicit user authorization", overwrite)

    def test_paths_must_come_from_tool_outputs(self) -> None:
        run = tool_function("run_gtap_scenario")
        read = tool_function("read_gtap_results")
        self.assertIn("exact output_cmf", run["description"])
        self.assertIn("exact result_dir", read["description"])
        self.assertIn("never guess or reconstruct", read["parameters"]["properties"]["result_dir"]["description"])

    def test_shock_catalog_distinguishes_scope_and_shipping(self) -> None:
        shock = tool_function("modify_shock_cmf")
        description = shock["description"].lower()
        self.assertIn("code-specific schema branch", description)
        self.assertIn("atd(importer)", description)
        self.assertIn("one shock, not one shock per commodity", description)

        variants = shock["parameters"]["properties"]["modifications"]["items"]["oneOf"]
        by_code = {variant["properties"]["code"]["enum"][0]: variant for variant in variants}
        common = {"type", "code", "value", "value_mode", "note"}
        self.assertEqual(set(by_code["atd"]["properties"]), common | {"importer"})
        self.assertEqual(set(by_code["aoall"]["properties"]), common | {"sector", "region"})
        self.assertEqual(by_code["atd"]["properties"]["value_mode"]["enum"], ["percent_change"])
        self.assertEqual(
            by_code["tms"]["properties"]["value_mode"]["enum"],
            ["target_rate", "rate_change", "power_change"],
        )

    def test_no_tools_prompt_distinguishes_ambiguity_from_uncertainty(self) -> None:
        prompt = (PROJECT_DIR / "benchmark" / "prompts" / "no_tools_system.md").read_text(encoding="utf-8")
        self.assertIn("changes the identity of the experiment", prompt)
        self.assertIn("Do not ask for clarification merely because exact calibration data", prompt)
        self.assertIn('"original database" must produce `estimated`', prompt)
        self.assertIn('merely saying "base data" must produce `needs_clarification`', prompt)


if __name__ == "__main__":
    unittest.main()
