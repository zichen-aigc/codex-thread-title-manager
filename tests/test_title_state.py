from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "codex-thread-title-manager" / "scripts" / "title_state.py"


class TitleStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["CODEX_TITLE_MANAGER_STATE_DIR"] = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, payload: dict) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "request"],
            input=json.dumps(payload, ensure_ascii=False) + "\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=self.env,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_unmanaged_then_recorded_and_changed(self) -> None:
        code, value = self.call({"command": "inspect", "thread_id": "example-1", "current_title": "打开这个链接"})
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "unmanaged")

        code, value = self.call(
            {
                "command": "record",
                "thread_id": "example-1",
                "old_title": "打开这个链接",
                "new_title": "示例项目｜企业片文字分镜",
                "project": "示例项目",
                "reason": "current task became concrete",
                "source": "current",
            }
        )
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])

        _, value = self.call({"command": "inspect", "thread_id": "example-1", "current_title": "示例项目｜企业片文字分镜"})
        self.assertEqual(value["status"], "managed")
        _, value = self.call({"command": "inspect", "thread_id": "example-1", "current_title": "我的手动标题"})
        self.assertEqual(value["status"], "changed_since_manager")

    def test_lock_unlock_and_history(self) -> None:
        payload = {
            "command": "record",
            "thread_id": "example-2",
            "old_title": "虚构的初始占位标题",
            "new_title": "Codex｜协作技能安装验收",
            "project": "Codex",
            "reason": "approved batch",
            "source": "batch",
        }
        self.assertEqual(self.call(payload)[0], 0)
        self.assertEqual(self.call({"command": "lock", "thread_id": "example-2"})[0], 0)
        _, value = self.call({"command": "inspect", "thread_id": "example-2", "current_title": payload["new_title"]})
        self.assertEqual(value["status"], "locked")
        self.assertEqual(self.call({"command": "unlock", "thread_id": "example-2"})[0], 0)
        _, value = self.call({"command": "history", "thread_id": "example-2", "limit": 10})
        self.assertEqual(len(value["entries"]), 1)
        self.assertEqual(value["entries"][0]["old_title"], "虚构的初始占位标题")

    def test_rejects_control_characters_and_bad_source(self) -> None:
        code, value = self.call({"command": "inspect", "thread_id": "example-3", "current_title": "bad\ntitle"})
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        code, value = self.call(
            {
                "command": "record",
                "thread_id": "example-3",
                "old_title": "old",
                "new_title": "new",
                "project": "example",
                "reason": "test",
                "source": "invalid",
            }
        )
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])

    def test_stats_has_no_state_side_effect(self) -> None:
        code, value = self.call({"command": "stats"})
        self.assertEqual(code, 0)
        self.assertEqual(value["thread_count"], 0)
        self.assertFalse((Path(self.temporary.name) / "state.json").exists())


if __name__ == "__main__":
    unittest.main()

