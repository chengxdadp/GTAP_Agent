from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web import agent


class OpenRouterAgentTests(unittest.TestCase):
    def test_reset_session_discards_only_requested_context(self) -> None:
        target = "test-reset-target"
        survivor = "test-reset-survivor"
        agent.SESSIONS[target] = [{"role": "user", "content": "old context"}]
        agent.SESSIONS[survivor] = [{"role": "user", "content": "keep"}]
        try:
            self.assertTrue(agent.reset_session(target))
            self.assertNotIn(target, agent.SESSIONS)
            self.assertIn(survivor, agent.SESSIONS)
            self.assertFalse(agent.reset_session(target))
        finally:
            agent.SESSIONS.pop(target, None)
            agent.SESSIONS.pop(survivor, None)

    def test_key_file_uses_second_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "key.txt"
            key_file.write_text("legacy-key\nopenrouter-key\n", encoding="utf-8")
            with patch.object(agent, "KEY_FILE", key_file), patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENROUTER_API_KEY", None)
                self.assertEqual(agent.load_api_key(), "openrouter-key")

    def test_environment_key_has_priority(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": " environment-key "}):
            self.assertEqual(agent.load_api_key(), "environment-key")

    def test_normalization_preserves_reasoning_details_unmodified(self) -> None:
        details = [
            {
                "type": "reasoning.text",
                "text": "check the scenario",
                "id": "reasoning-1",
                "format": "openai-responses-v1",
                "index": 0,
            }
        ]
        tool_calls = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_gtap_results", "arguments": "{}"},
            }
        ]
        normalized = agent.normalize_assistant_message(
            {"role": "assistant", "content": None, "reasoning_details": details, "tool_calls": tool_calls}
        )
        self.assertIs(normalized["reasoning_details"], details)
        self.assertEqual(normalized["tool_calls"], tool_calls)

    def test_stream_collects_reasoning_details_in_chunk_order(self) -> None:
        first = {
            "type": "reasoning.text",
            "text": "first ",
            "id": "reasoning-1",
            "format": "openai-responses-v1",
            "index": 0,
        }
        second = {
            "type": "reasoning.text",
            "text": "second",
            "id": "reasoning-1",
            "format": "openai-responses-v1",
            "index": 0,
        }
        chunks = [
            {"choices": [{"delta": {"reasoning_details": [first]}}]},
            {"choices": [{"delta": {"reasoning_details": [second], "content": "done"}}]},
        ]
        with patch.object(agent, "iter_openrouter_stream", return_value=iter(chunks)):
            events = list(agent.stream_assistant_message([{"role": "user", "content": "test"}]))

        self.assertEqual([event["content"] for event in events[:-1]], ["first ", "second", "done"])
        message = events[-1]["message"]
        self.assertEqual(message["reasoning_details"], [first, second])
        self.assertEqual(message["content"], "done")


if __name__ == "__main__":
    unittest.main()
