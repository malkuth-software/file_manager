#!/usr/bin/env python3
"""Download exact release inputs; missing compiler seeds fall back to compilation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
from typing import BinaryIO
from native_build_support import sha256

ROOT: Path = Path(__file__).resolve().parents[1]
CACHE_MANIFEST_SCHEMA: int = 2
# The provider moved to malkuth-software; GitHub redirects the former name, and
# provider manifests written before the move (and its cache_manifest.py) still
# record it. Both names identify the one repository the lock pins.
PROVIDER_REPOSITORIES: tuple[str, ...] = ('malkuth-software/gui_forms', 'falseywinchnet/gui_forms')
# The macOS archive also carries the LLVM 22.1.8 runtime built for macOS 14,
# which cmake/llvm22.cmake links through GUI_FORMS_LLVM_RUNTIME.
MACOS_RUNTIME_LAYOUT: str = '.build/toolchain/llvm-22.1.8-macos14'
PLATFORMS: tuple[str, ...] = ('macos-arm64', 'linux-x64', 'windows-x64')


def allowed_member(member: tarfile.TarInfo, platform: str) -> bool:
    """Whether a cache archive member lies inside the declared cache tree for platform.

    Only regular files and directories under .ccache/ and the manifest are
    admitted; the macOS runtime prefix may also hold the dylib symlinks,
    whose targets the data extraction filter keeps inside the destination.
    """
    member_path: PurePosixPath = PurePosixPath(member.name)
    if member_path.is_absolute() or '..' in member_path.parts or not member_path.parts:
        return False
    if member.name == 'cache-manifest.json':
        return member.isfile()
    if member_path.parts[0] == '.ccache':
        return member.isfile() or member.isdir()
    runtime: PurePosixPath = PurePosixPath(MACOS_RUNTIME_LAYOUT)
    in_runtime: bool = member_path == runtime or runtime in member_path.parents
    if platform == 'macos-arm64' and in_runtime:
        return member.isfile() or member.isdir() or member.issym()
    # The runtime prefix's parent directories (.build, .build/toolchain).
    if platform == 'macos-arm64' and member_path in runtime.parents:
        return member.isdir()
    return False


def restore_cache_seed(path: Path, destination: Path, platform: str, revision: str) -> dict[str, object]:
    """Validate the complete member set and provider identity, then extract; returns the manifest."""
    archive: tarfile.TarFile
    with tarfile.open(path, 'r:gz') as archive:
        member: tarfile.TarInfo
        for member in archive.getmembers():
            if not allowed_member(member, platform):
                raise ValueError('Invalid compiler-cache archive member: ' + member.name)
        stream: BinaryIO | None = archive.extractfile('cache-manifest.json')
        if stream is None:
            raise ValueError('Compiler cache manifest is missing')
        with stream:
            manifest: dict[str, object] = json.load(stream)
        if (manifest.get('schema') != CACHE_MANIFEST_SCHEMA or
                manifest.get('repository') not in PROVIDER_REPOSITORIES or
                manifest.get('platform') != platform or
                manifest.get('revision') != revision):
            raise ValueError('Compiler cache provider identity does not match lock')
        archive.extractall(destination, filter='data')
    return manifest


def report_compiler_match(manifest: dict[str, object]) -> None:
    """Say whether the clang on PATH is the binary that filled the cache.

    CCACHE_COMPILERCHECK=content hashes the compiler binary, so a different
    one only misses. This never loosens a check; it names the cause early.
    """
    expected: object = manifest.get('compiler_sha256')
    located: str | None = shutil.which('clang')
    if located is None:
        print('Compiler cache restored; clang is not on PATH yet, so its identity is unchecked')
        return
    actual: str = sha256(Path(located))
    if actual == expected:
        print('Compiler cache restored for this exact clang binary (' + actual[:12] + '); hits are possible')
        return
    print('Compiler cache restored, but clang ' + located + ' (' + actual[:12] + ') is not the producer\'s ' +
          str(expected)[:12] + ': every compilation will miss. Use the toolchain the cache manifest names.')


def verify_source_pins(lock: dict[str, dict[str, object]], root: Path) -> None:
    component: str
    for component in ['backend', 'gui_forms']:
        current: str = subprocess.check_output(
            ['git', '-C', str(root / component), 'rev-parse', 'HEAD'], text=True).strip()
        if current != lock[component]['revision']:
            raise ValueError('Source submodule differs from dependency lock: ' + component)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, choices=PLATFORMS)
    parser.add_argument('--skip-cache', action='store_true')
    args: argparse.Namespace = parser.parse_args()
    lock: dict[str, dict[str, object]] = json.loads(
        (ROOT / 'dependencies.lock.json').read_text(encoding='utf-8'))
    verify_source_pins(lock, ROOT)
    output: Path = ROOT / '.build/inputs' / args.platform
    output.mkdir(parents=True, exist_ok=True)
    component: str
    for component in ['backend', 'gui_forms']:
        if component == 'gui_forms' and args.skip_cache:
            continue
        entry: dict[str, object] = lock[component]
        prefix: str = 'backend-'
        if component == 'gui_forms':
            prefix = 'gui-forms-cache-'
        name: str = prefix + args.platform + '.tar.gz'
        command: list[str] = ['gh', 'release', 'download', str(entry['release']),
                              '--repo', str(entry['repository']), '--pattern', name,
                              '--dir', str(output), '--clobber']
        result: subprocess.CompletedProcess[bytes] = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            if component == 'backend':
                raise RuntimeError('Required pinned backend bundle is unavailable')
            print('Compiler cache seed unavailable; normal compilation remains enabled')
            continue
        hashes: object = entry['sha256']
        if not isinstance(hashes, dict):
            raise ValueError('Missing archive hashes in dependency lock')
        actual: str = sha256(output / name)
        if actual != hashes.get(args.platform):
            raise ValueError('Downloaded archive does not match dependency lock: ' + name)
        if component == 'gui_forms':
            manifest: dict[str, object] = restore_cache_seed(
                output / name, ROOT, args.platform, str(entry['revision']))
            report_compiler_match(manifest)


if __name__ == '__main__':
    main()
