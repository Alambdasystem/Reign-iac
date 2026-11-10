"""Static safety contract for the optional authenticated live AX runner.

No network requests, browser login, or production effects happen in these tests.
"""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "live_prompt_acceptance.py"
spec = importlib.util.spec_from_file_location("reign_live_prompt_acceptance", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LivePromptRunnerContractTests(unittest.TestCase):
    def test_exact_origin_is_required(self):
        self.assertEqual(module.validate_origin(module.AX_ORIGIN),module.AX_ORIGIN)
        for bad in (
            "http://reign-ax-pilot-d934.onrender.com",
            "https://reign-ax-pilot-d934.onrender.com.evil.example",
            "https://user:password@reign-ax-pilot-d934.onrender.com",
            "https://reign-ax-pilot-d934.onrender.com/?auth=secret",
            "https://reign-ax-pilot-d934.onrender.com:1234/",
            "https://reign-ax-pilot-d934.onrender.com/other",
        ):
            with self.subTest(url=bad),self.assertRaises(ValueError):
                module.validate_origin(bad)

    def test_only_nonsecret_safe_cases_automated(self):
        ids=[case[0] for case in module.SAFE_CASES]
        self.assertEqual(ids,["L01","L02","L03","L04","L05","L10","L11"])
        self.assertEqual(len(module.ALL_CASES),21)
        self.assertEqual(len(set(ids)),len(ids))
        self.assertTrue(set(ids).issubset(module.ALL_CASES))
        self.assertEqual({tool for _,_,names in module.SAFE_CASES for tool in names},
                         {"workspace_status","browser_status","open_browser",
                          "navigate_browser","browser_observe","vault_status",
                          "vault_list_accounts"})

    def test_no_unattended_credential_entry_or_human_takeover(self):
        for _,prompt,tools in module.SAFE_CASES:
            self.assertNotIn("password",prompt.lower())
            self.assertNotIn("click",prompt.lower())
            self.assertNotIn("submit",prompt.lower())
            self.assertTrue(all("fill" not in tool and "use_account" not in tool
                                for tool in tools))
