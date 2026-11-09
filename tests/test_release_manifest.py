import unittest
from scripts.release_manifest import manifest, workspace_acceptance, SERVICES

SHA = "a" * 40
SMOKE = {"status": "passed", "source_sha": SHA}


class ReleaseManifestTests(unittest.TestCase):
    def test_workspace_gate_requires_both_matching_phases_and_restart_durability(self):
        initial = {"passed": True, "phase": "initial", "source_sha": SHA,
                   "evidence_kind": "controlled_workspace_acceptance", "model": "scripted_model", "website": "controlled_site"}
        restart = {**initial, "phase": "restart", "checks": {"account_survived_process_restart": True}}
        self.assertEqual(workspace_acceptance(SHA, initial, restart)["restart"], restart)
        for changed in ({"passed": False}, {"source_sha": "c" * 40}, {"phase": "initial"},
                        {"checks": {"account_survived_process_restart": False}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                workspace_acceptance(SHA, initial, {**restart, **changed})

    def inspect(self, image):
        return [image.rsplit(":", 1)[0] + "@sha256:" + "b" * 64]

    def test_records_exact_four_tested_digests_without_provisioning(self):
        release = manifest("alambdasystem", SHA, SMOKE, self.inspect)
        self.assertFalse(release["provision"])
        self.assertEqual(set(release["images"]), set(SERVICES))
        self.assertTrue(all(v["digest"] == "sha256:" + "b" * 64 for v in release["images"].values()))

    def test_rejects_missing_or_wrong_source_evidence(self):
        for smoke in ({}, {"status": "passed", "source_sha": "c" * 40},
                      {"status": "awaiting_restart_check", "source_sha": SHA}):
            with self.assertRaises(ValueError):
                manifest("alambdasystem", SHA, smoke, self.inspect)

    def test_rejects_missing_or_foreign_digest(self):
        for digests in ([], ["ghcr.io/other/reign-core@sha256:" + "b" * 64],
                        ["ghcr.io/alambdasystem/reign-core@latest"]):
            with self.assertRaises(ValueError):
                manifest("alambdasystem", SHA, SMOKE, lambda _: digests)


if __name__ == "__main__":
    unittest.main()
