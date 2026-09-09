"""An unchanged launcher/version cannot hide a changed native installation."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.release_identity import NPM_SHIM, codex_launch_identity


class ReleaseExecutableIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shr-id-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shim = self.root / 'node_modules/.bin/codex.cmd'
        self.launcher = self.root / 'node_modules/@openai/codex/bin/codex.js'
        self.platform = self.root / 'node_modules/@openai/codex-win32-x64/package.json'
        self.vendor = self.platform.parent / 'vendor/x86_64-pc-windows-msvc'
        self.native = self.vendor / 'bin/codex.exe'
        self.helper = self.vendor / 'codex-resources/codex-windows-sandbox-setup.exe'
        self.path_node = self.root / 'node.exe'
        self.comspec = self.root / 'cmd.exe'
        for path, content in ((self.shim, NPM_SHIM), (self.launcher, '// installed launcher'),
            (self.launcher.parent.parent / 'package.json', '{"name":"@openai/codex"}'),
            (self.platform, '{}'), (self.vendor / 'codex-package.json', '{}'),
            (self.native, 'native'), (self.helper, 'helper'), (self.path_node, 'node'), (self.comspec, 'cmd')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')

    def capture(self):
        def run(argv, **kwargs):
            if Path(argv[0]) == self.comspec:
                return SimpleNamespace(stdout=str(self.path_node), stderr='')
            launch = dict(node=argv[0], nodeVersion='same-version', arch='x64',
                          native=str(self.native), platformPackage=str(self.platform))
            return SimpleNamespace(stdout='same-version', stderr='CODEX_LAUNCH_IDENTITY=' + json.dumps(launch) + '\n')
        with mock.patch('tools.release_identity.shutil.which', side_effect=lambda name:
                        str(self.path_node if name == 'node' else self.comspec)), \
             mock.patch('tools.release_identity._run', side_effect=run):
            return codex_launch_identity(self.shim, cwd=self.root)

    def test_unchanged_chain_passes_but_native_helpers_launcher_or_package_changes_invalidate(self):
        before = self.capture()
        self.assertEqual(before, self.capture())
        for path in (self.native, self.helper, self.launcher, self.platform):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b' altered')
                self.assertNotEqual(before, self.capture())
                path.write_bytes(original)
        extra = self.vendor / 'bin/new-helper.exe'
        extra.write_bytes(b'new')
        self.assertNotEqual(before, self.capture())

    def test_path_node_and_new_shim_local_node_are_resolved_again(self):
        before = self.capture()
        alternate = self.root / 'alternate/node.exe'
        alternate.parent.mkdir()
        alternate.write_bytes(self.path_node.read_bytes())
        self.path_node = alternate
        self.assertNotEqual(before, self.capture())
        path_before = self.capture()
        (self.shim.parent / 'node.exe').write_bytes(alternate.read_bytes())
        self.assertNotEqual(path_before, self.capture())

    def test_same_bytes_in_another_platform_target_invalidate(self):
        before = self.capture()
        import shutil
        target = self.root / 'another-install'
        shutil.copytree(self.platform.parent, target)
        self.platform = target / 'package.json'
        self.native = target / 'vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        self.assertNotEqual(before, self.capture())

    def test_unknown_wrapper_cannot_claim_exact_identity(self):
        self.shim.write_text('@echo off\ncodex-other.exe %*\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported Codex wrapper'):
            self.capture()


if __name__ == '__main__':
    unittest.main()
