"""Verify the installed Uvicorn CLI uses the image's graceful-drain setting."""
import importlib
import os
from pathlib import Path
import runpy
import shlex
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner


class CoreShutdownConfigurationTests(unittest.TestCase):
    def image_environment(self):
        dockerfile = Path(__file__).resolve().parents[1] / "services/core/Dockerfile"
        environment = {}
        for line in dockerfile.read_text().splitlines():
            if line.startswith("ENV "):
                environment.update(value.split("=", 1) for value in shlex.split(line[4:]))
        return {"UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": environment["UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN"]}

    def test_console_cli_uses_image_environment_and_explicit_argument_can_override(self):
        cli = importlib.import_module("uvicorn.main")
        for arguments, expected in (([], 2), (["--timeout-graceful-shutdown", "7"], 7)):
            with self.subTest(arguments=arguments), patch.object(cli, "run") as run:
                result = CliRunner().invoke(cli.main, ["reign.core:app", *arguments],
                                            env=self.image_environment())
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(run.call_args.kwargs["timeout_graceful_shutdown"], expected)

    def test_module_cli_uses_the_same_image_environment_without_starting_server(self):
        cli = importlib.import_module("uvicorn.main")
        with patch.dict(os.environ, self.image_environment()), \
                patch.object(sys, "argv", ["uvicorn", "reign.core:app"]), \
                patch.object(cli, "run") as run:
            with self.assertRaises(SystemExit) as exited:
                runpy.run_module("uvicorn", run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(run.call_args.kwargs["timeout_graceful_shutdown"], 2)


if __name__ == "__main__": unittest.main()
