import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from reign.release_acceptance import main


class ReleaseGateTests(unittest.TestCase):
    def test_voice_then_worker_isolation_then_team_provider_must_all_succeed(self):
        with patch("reign.release_acceptance.subprocess.run", side_effect=[SimpleNamespace(returncode=0)] * 3) as run:
            self.assertEqual(main(), 0)
            self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                             ["reign.voice_acceptance", "reign.team_browser_acceptance", "reign.team_acceptance"])
            self.assertTrue(all(call.kwargs == {"timeout": 210, "check": False} for call in run.call_args_list))

    def test_a_failed_or_timed_out_gate_cannot_be_promoted_as_success(self):
        for outcomes in ([SimpleNamespace(returncode=1)], [SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)],
                         [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)],
                         [subprocess.TimeoutExpired("synthetic", 210)],
                         [SimpleNamespace(returncode=0), subprocess.TimeoutExpired("synthetic", 210)],
                         [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0), subprocess.TimeoutExpired("synthetic", 210)]):
            with self.subTest(count=len(outcomes)), patch("reign.release_acceptance.subprocess.run", side_effect=outcomes) as run, patch("builtins.print"):
                self.assertEqual(main(), 1)
                self.assertEqual(run.call_count, len(outcomes), "a failed worker gate must not continue to the team provider")
