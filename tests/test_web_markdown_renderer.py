from __future__ import annotations

import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class WebNewTaskContractTests(unittest.TestCase):
    def test_new_task_resets_context_chat_and_trace(self) -> None:
        html = (PROJECT_DIR / "web" / "index.html").read_text(encoding="utf-8")
        source = (PROJECT_DIR / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="newTask"', html)
        self.assertIn('fetch("/api/session/reset"', source)
        self.assertIn("sessionId = createSessionId();", source)
        self.assertIn("messagesEl.replaceChildren();", source)
        self.assertIn("clearExecutionTrace();", source)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the frontend renderer regression test")
class WebMarkdownRendererTests(unittest.TestCase):
    def test_indented_artifact_paths_stay_with_their_list_labels(self) -> None:
        script = textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            const source = fs.readFileSync("web/app.js", "utf8");
            const rendererStart = source.indexOf("function escapeHtml");
            const rendererEnd = source.indexOf("function handleEvent");
            assert.ok(rendererStart >= 0 && rendererEnd > rendererStart);
            const context = vm.createContext({window: {}});
            vm.runInContext(source.slice(rendererStart, rendererEnd), context);

            const sample = [
              "- Aggregation mapping:  ",
              "  `D:\\RD\\GTAPAgent\\config\\aggregations\\china_us_soy_split.txt`",
              "- Scenario CMF:  ",
              "  `D:\\RD\\GTAPAgent\\result\\scenario.cmf`",
              "- Solved results:  ",
              "  `D:\\RD\\GTAPAgent\\result\\run`",
              "- Detailed soybean export query:  ",
              "  `D:\\RD\\GTAPAgent\\result\\query.json`",
              "Following paragraph.",
            ].join("\n");
            context.sample = sample;
            const html = vm.runInContext("renderMarkdownContent(sample)", context);

            assert.equal((html.match(/<li>/g) || []).length, 4);
            assert.match(html, /<li>Aggregation mapping:<br><code>D:\\RD\\GTAPAgent/);
            assert.match(html, /<li>Scenario CMF:<br><code>D:\\RD\\GTAPAgent/);
            assert.match(html, /<li>Solved results:<br><code>D:\\RD\\GTAPAgent/);
            assert.match(html, /<li>Detailed soybean export query:<br><code>D:\\RD\\GTAPAgent/);
            assert.ok(html.indexOf("<ul>") < html.indexOf("<p>Following paragraph.</p>"));
            """
        )
        completed = subprocess.run(
            [shutil.which("node") or "node", "-e", script],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
