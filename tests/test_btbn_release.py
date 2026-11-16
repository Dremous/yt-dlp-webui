"""BtbN 解析与 registry cache 告警的行为测试。"""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
RESOLVER_PATH = ROOT / ".github/scripts/resolve_btbn_release.py"
CACHE_SCRIPT = ROOT / ".github/scripts/verify_registry_cache.sh"


def load_resolver():
    spec = importlib.util.spec_from_file_location("resolve_btbn_release", RESOLVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def stable_asset(version, arch, digest="sha256:" + "a" * 64):
    return {
        "name": f"ffmpeg-n{version}-latest-{arch}-gpl-{version}.tar.xz",
        "digest": digest,
    }


class BtbNResolverBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_resolver()

    def resolve(self, payload):
        with mock.patch.object(
            self.module, "urlopen", return_value=FakeResponse(payload)
        ):
            return self.module.resolve_release(token="test-token")

    def test_resolves_highest_stable_version_and_digests(self):
        payload = {
            "assets": [
                stable_asset("8.1", "linux64", "sha256:" + "b" * 64),
                stable_asset("8.1", "linuxarm64", "sha256:" + "c" * 64),
                stable_asset("9.0", "linux64", "sha256:" + "d" * 64),
                stable_asset("9.0", "linuxarm64", "sha256:" + "e" * 64),
                {"name": "ffmpeg-master-latest-linux64-gpl.tar.xz"},
            ]
        }
        result = self.resolve(payload)
        self.assertEqual(result["version"], "9.0")
        self.assertEqual(result["sha256_amd64"], "d" * 64)
        self.assertEqual(result["sha256_arm64"], "e" * 64)

    def test_api_failure_is_fatal(self):
        with mock.patch.object(self.module, "urlopen", side_effect=URLError("boom")):
            with self.assertRaises(self.module.ResolutionError) as ctx:
                self.module.resolve_release(token="test-token")
        self.assertIn("GitHub API 不可用", str(ctx.exception))

    def test_no_stable_asset_is_fatal(self):
        payload = {"assets": [{"name": "ffmpeg-master-latest-linux64-gpl.tar.xz"}]}
        with self.assertRaises(self.module.ResolutionError) as ctx:
            self.resolve(payload)
        self.assertIn("没有 nX.Y 稳定 GPL 资产", str(ctx.exception))

    def test_missing_arm64_asset_is_fatal(self):
        payload = {"assets": [stable_asset("9.0", "linux64")]}
        with self.assertRaises(self.module.ResolutionError) as ctx:
            self.resolve(payload)
        self.assertIn("缺少资产", str(ctx.exception))

    def test_missing_digest_is_fatal(self):
        payload = {
            "assets": [
                stable_asset("9.0", "linux64", digest=""),
                stable_asset("9.0", "linuxarm64", digest=""),
            ]
        }
        with self.assertRaises(self.module.ResolutionError) as ctx:
            self.resolve(payload)
        self.assertIn("digest", str(ctx.exception))

    def test_main_writes_github_output(self):
        payload = {
            "assets": [
                stable_asset("9.0", "linux64", "sha256:" + "d" * 64),
                stable_asset("9.0", "linuxarm64", "sha256:" + "e" * 64),
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github-output"
            with mock.patch.object(
                self.module, "urlopen", return_value=FakeResponse(payload)
            ), mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GH_TOKEN": "x"}):
                self.assertEqual(self.module.main(), 0)
            lines = output.read_text(encoding="utf-8").splitlines()
        self.assertIn("version=9.0", lines)
        self.assertIn("sha256_amd64=" + "d" * 64, lines)
        self.assertIn("sha256_arm64=" + "e" * 64, lines)


class RegistryCacheBehaviorTest(unittest.TestCase):
    def run_script(self, docker_exit):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(f"#!/bin/sh\nexit {docker_exit}\n", encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            return subprocess.run(
                ["bash", str(CACHE_SCRIPT), "ghcr.io/example:cache"],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_unreadable_cache_warns_but_does_not_fail(self):
        result = self.run_script(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::registry cache", result.stdout)

    def test_readable_cache_is_reported(self):
        result = self.run_script(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Registry cache verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
