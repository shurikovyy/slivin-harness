"""Exact installed Codex launch chain for native Windows qualification only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


# Known npm cmd-shim shape. Unknown wrappers cannot claim an exact native identity.
NPM_SHIM = r'''@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\..\@openai\codex\bin\codex.js" %*
'''

# Observe the installed launcher's actual spawn while executing only --version.
# No installation files, user settings or environment values are written/emitted.
LAUNCH_PROBE = r'''
const cp = require('node:child_process');
const {syncBuiltinESMExports, createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const fs = require('node:fs');
const launcher = process.argv[1];
const nativeSpawn = cp.spawn;
cp.spawn = function(command, args, options) {
  const req = createRequire(pathToFileURL(launcher));
  let platformPackage = null;
  try { platformPackage = req.resolve('@openai/codex-win32-' + process.arch + '/package.json'); } catch {}
  process.stderr.write('CODEX_LAUNCH_IDENTITY=' + JSON.stringify({
    node: process.execPath, nodeVersion: process.version, arch: process.arch,
    native: fs.realpathSync(command), platformPackage
  }) + '\n');
  return nativeSpawn.call(this, command, args, options);
};
syncBuiltinESMExports();
process.argv = [process.execPath, launcher, '--version'];
import(pathToFileURL(launcher).href);
'''


def file_identity(path: Path) -> dict:
    path = path.resolve(strict=True)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), sha256=digest)


def payload_identity(root: Path) -> dict:
    root = root.resolve(strict=True)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise RuntimeError('Linked native payload cannot be qualified: ' + str(path))
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_identity(path)
    if not files:
        raise RuntimeError('Empty native payload')
    return dict(root=str(root), files=files)


def _run(argv: list[str], *, cwd: Path):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', check=True, timeout=30)


def codex_launch_identity(codex: Path, *, cwd: Path) -> dict:
    codex = codex.resolve(strict=True)
    comspec = shutil.which(os.environ.get('COMSPEC', 'cmd.exe'))
    if comspec is None:
        raise RuntimeError('Windows command processor cannot be resolved')
    record = dict(schema_version='codex-launch-identity.v1', cwd=str(cwd.resolve()),
                  configured=file_identity(codex), command_processor=file_identity(Path(comspec)))
    if codex.suffix.lower() == '.exe':
        record['kind'] = 'NATIVE'
        if codex.parent.name == 'bin' and (codex.parent.parent / 'codex-package.json').is_file():
            record['payload'] = payload_identity(codex.parent.parent)
        else:
            # Direct native distributions put their known helpers beside the executable.
            record['helpers'] = {name: file_identity(codex.parent / name) for name in
                ('codex-code-mode-host.exe', 'codex-command-runner.exe', 'codex-windows-sandbox-setup.exe', 'rg.exe')
                if (codex.parent / name).is_file()}
            record['resources'] = {name: payload_identity(codex.parent / name) for name in
                ('codex-resources', 'codex-path') if (codex.parent / name).is_dir()}
        return record
    if codex.suffix.lower() != '.cmd' or codex.read_text(encoding='utf-8').strip() != NPM_SHIM.strip():
        raise RuntimeError('Unsupported Codex wrapper; exact native launch identity is unavailable')
    record['kind'] = 'NPM_CMD'
    launcher = (codex.parent / '../@openai/codex/bin/codex.js').resolve(strict=True)
    package = launcher.parent.parent / 'package.json'
    if json.loads(package.read_text(encoding='utf-8')).get('name') != '@openai/codex':
        raise RuntimeError('Unexpected Codex launcher package')
    local_node = codex.parent / 'node.exe'
    if local_node.is_file():
        node = local_node.resolve(strict=True)
    else:
        # Match cmd's current-directory/PATH search instead of the assertions Node.
        command = subprocess.list2cmdline(['node', '-p', 'process.execPath'])
        node = Path(_run([comspec, '/d', '/s', '/c', command], cwd=cwd).stdout.strip()).resolve(strict=True)
        selected_node = shutil.which('node')
        if selected_node is None or Path(selected_node).resolve(strict=True) != node:
            raise RuntimeError('Indirect PATH Node wrapper cannot claim exact launch identity')
    observed = _run([str(node), '-e', LAUNCH_PROBE, str(launcher)], cwd=cwd)
    rows = [line.removeprefix('CODEX_LAUNCH_IDENTITY=') for line in observed.stderr.splitlines()
            if line.startswith('CODEX_LAUNCH_IDENTITY=')]
    if len(rows) != 1:
        raise RuntimeError('Codex launcher did not expose exactly one native spawn')
    launch = json.loads(rows[0])
    native = Path(launch['native']).resolve(strict=True)
    if native.name != 'codex.exe' or native.parent.name != 'bin':
        raise RuntimeError('Unexpected Codex native payload layout')
    record.update(node=file_identity(Path(launch['node'])), node_version=launch['nodeVersion'],
                  arch=launch['arch'], launcher=file_identity(launcher), package=file_identity(package),
                  native=file_identity(native), payload=payload_identity(native.parent.parent))
    platform_package = launch['platformPackage']
    if platform_package is not None:
        record['platform_package'] = file_identity(Path(platform_package))
    return record
