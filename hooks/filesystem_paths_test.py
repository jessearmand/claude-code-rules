#!/usr/bin/env python3
"""Protected-path regressions for case-sensitive and case-insensitive volumes."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import config_guard
import filesystem_paths


class CaseSemanticsTests(unittest.TestCase):
    def test_protected_aliases_follow_target_volume(self) -> None:
        commands = (
            "printf x > ~/.CLAUDE/settings.json",
            "printf x > ~/.claude/SETTINGS.JSON",
            "printf x > ~/.CODEX/CONFIG.TOML",
            "printf x > ~/.SSH/CONFIG",
            "printf x > ~/library/launchagents/job.plist",
            "printf x > ~/.CODEX/agents.md",
            "rm -r ~/.CLAUDE",
        )
        with (
            tempfile.TemporaryDirectory() as cwd,
            patch("filesystem_paths.sys.platform", "darwin"),
        ):
            for sensitive in (0, 1):
                with patch("filesystem_paths.os.pathconf", return_value=sensitive):
                    for command in commands:
                        with self.subTest(sensitive=sensitive, command=command):
                            result = config_guard.run(
                                {
                                    "tool_name": "Bash",
                                    "cwd": cwd,
                                    "tool_input": {"command": command},
                                }
                            )
                            self.assertEqual(
                                result.decision if result else None,
                                None if sensitive else "deny",
                            )
                    result = config_guard.run(
                        {
                            "tool_name": "Write",
                            "cwd": cwd,
                            "tool_input": {"file_path": ".CLAUDE/settings.json"},
                        }
                    )
                    self.assertEqual(
                        result.decision if result else None,
                        None if sensitive else "deny",
                    )
                    self.assertIsNone(config_guard.check_path("notes.json", cwd))

    def test_volume_is_queried_at_nearest_existing_ancestor(self) -> None:
        with (
            tempfile.TemporaryDirectory() as cwd,
            patch("filesystem_paths.sys.platform", "darwin"),
            patch("filesystem_paths.os.pathconf", return_value=0) as query,
        ):
            self.assertTrue(
                filesystem_paths.case_insensitive(
                    os.path.join(cwd, ".CLAUDE", "settings.json")
                )
            )
            query.assert_called_once_with(
                cwd, os.pathconf_names.get("PC_CASE_SENSITIVE", 11)
            )

    def test_mixed_volumes_and_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            sensitive, insensitive = (
                os.path.join(cwd, name) for name in ("sensitive", "insensitive")
            )
            os.mkdir(sensitive)
            os.mkdir(insensitive)
            os.symlink(sensitive, os.path.join(cwd, "link"))

            def volume(path: str, _key: int) -> int:
                return int(os.path.realpath(path).startswith(sensitive))

            with (
                patch("filesystem_paths.sys.platform", "darwin"),
                patch("filesystem_paths.os.pathconf", side_effect=volume),
            ):
                self.assertIsNone(
                    config_guard.check_path(".CLAUDE/settings.json", sensitive)
                )
                self.assertIsNotNone(
                    config_guard.check_path(".CLAUDE/settings.json", insensitive)
                )
                self.assertIsNone(
                    config_guard.check_path("link/.CLAUDE/settings.json", cwd)
                )

    def test_unavailable_macos_answer_conservatively_protects_aliases(self) -> None:
        with (
            patch("filesystem_paths.sys.platform", "darwin"),
            patch("filesystem_paths.os.pathconf", side_effect=OSError),
        ):
            self.assertIsNotNone(
                config_guard.check_path("~/.CLAUDE/settings.json", os.getcwd())
            )


if __name__ == "__main__":
    unittest.main()
