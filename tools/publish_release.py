#!/usr/bin/env python3
"""Publish File Manager's tested archives as one GitHub prerelease, from CI only.

native-builds.yml's publish job runs this on a push to main once the
"File Manager CI summary" check passed. It:

  1. checks the downloaded files: exactly one archive and one installer per
     platform, named for this version and commit, never a dirty tree, each
     matching its .sha256 file;
  2. finds or creates the DRAFT prerelease v<version> targeting the tested
     commit, uploads every archive and checksum, then publishes it;
  3. verifies the published tag names the tested commit.

A rerun of the same workflow run (same version) finds the release: a draft
is completed, a published release whose tag names the commit is left as it
is, and one naming another commit is an error. Nothing is deleted.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from native_build_support import sha256
from fetch_build_inputs import FRONTEND_ONLY_PLATFORMS, PLATFORMS

VERSION_PATTERN: str = r'[0-9]+\.[0-9]+\.[0-9]+'
COMMIT_PATTERN: str = r'[0-9a-f]{40}'


@dataclass(frozen=True)
class ReleaseArchive:
    """One released file (kind 'archive' or 'installer') and its checksum file."""
    platform: str
    kind: str
    archive: Path
    checksum: Path


def release_extensions(platform: str) -> dict[str, str]:
    """The archive and installer extensions package_native.py and native_installers.py write."""
    if platform.startswith('windows-'):
        return {'archive': '.zip', 'installer': '-setup.exe'}
    if platform.startswith('macos-'):
        return {'archive': '.tar.gz', 'installer': '.pkg'}
    result: dict[str, str] = {'archive': '.tar.gz', 'installer': '.deb'}
    return result


def archive_name(version: str, platform: str, commit: str) -> str:
    """The name package_native.py gives a clean tree's archive, without its extension."""
    result: str = 'file-manager-' + version + '-' + platform + '-' + commit[:12]
    return result


def collect_archives(dist: Path, version: str, commit: str) -> list[ReleaseArchive]:
    """Return each platform's checked archive and installer in PLATFORMS order; raise on anything else."""
    expected: dict[str, tuple[str, str]] = {}
    platform: str
    for platform in PLATFORMS:
        kind: str
        extension: str
        for kind, extension in release_extensions(platform).items():
            expected[archive_name(version, platform, commit) + extension] = (platform, kind)
    present: list[Path] = sorted(path for path in dist.iterdir() if path.is_file())
    names: set[str] = set()
    path: Path
    for path in present:
        names.add(path.name)
    allowed: set[str] = set()
    name: str
    for name in expected:
        allowed.add(name)
        allowed.add(name + '.sha256')
    unexpected: list[str] = sorted(names - allowed)
    if unexpected:
        raise RuntimeError('unexpected files beside the release archives: ' + ', '.join(unexpected))
    missing: list[str] = sorted(allowed - names)
    if missing:
        raise RuntimeError('missing release files: ' + ', '.join(missing))
    archives: list[ReleaseArchive] = []
    identity: tuple[str, str]
    for name, identity in expected.items():
        archive: Path = dist / name
        checksum: Path = dist / (name + '.sha256')
        checksum_text: str = checksum.read_text(encoding='utf-8')
        fields: list[str] = checksum_text.split()
        if len(fields) != 2 or fields[1] != name:
            raise RuntimeError(checksum.name + ' does not name ' + name)
        actual: str = sha256(archive)
        if fields[0] != actual:
            raise RuntimeError(name + ' hashes to ' + actual + ', not its recorded ' + fields[0])
        archives.append(ReleaseArchive(platform=identity[0], kind=identity[1], archive=archive, checksum=checksum))
    return archives


def release_notes(version: str, commit: str, repository: str, archives: list[ReleaseArchive]) -> str:
    """The release body: what was built, from which commit, and each archive's hash."""
    lines: list[str] = [
        'File Manager ' + version + ', built and tested by CI from [commit `' + commit[:12] + '`]'
        '(https://github.com/' + repository + '/commit/' + commit + ').',
        '',
        'A prerelease: macOS builds are ad-hoc signed and not notarized; Windows and Linux '
        'builds are unsigned. Extract the whole archive and read its README.txt.',
        '',
        'Installers: Windows per-user setup (no administrator rights), macOS package into '
        '/Applications/File Manager, Debian package into /opt/file-manager.',
        '',
        '| Platform | File | SHA-256 |',
        '| --- | --- | --- |']
    entry: ReleaseArchive
    for entry in archives:
        digest: str = entry.checksum.read_text(encoding='utf-8').split()[0]
        lines.append('| ' + entry.platform + ' | `' + entry.archive.name + '` | `' + digest + '` |')
    if FRONTEND_ONLY_PLATFORMS:
        lines.append('')
        lines.append('Frontend only, without the bundled search and preview services (the pinned '
                     'backend publishes no bundle for them yet): ' + ', '.join(FRONTEND_ONLY_PLATFORMS) + '.')
    result: str = '\n'.join(lines) + '\n'
    return result


def gh(arguments: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print('+ gh ' + ' '.join(arguments), flush=True)
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        ['gh', *arguments], capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        raise RuntimeError('gh ' + ' '.join(arguments) + ' failed: ' + completed.stderr.strip())
    return completed


def tag_commit(repository: str, tag: str) -> str:
    """The commit a tag names, or '' when the tag does not exist."""
    completed: subprocess.CompletedProcess[str] = gh(
        ['api', 'repos/' + repository + '/commits/refs/tags/' + tag, '--jq', '.sha'], check=False)
    if completed.returncode != 0:
        return ''
    result: str = completed.stdout.strip()
    return result


def publish(version: str, commit: str, repository: str, archives: list[ReleaseArchive], dry_run: bool) -> None:
    tag: str = 'v' + version
    existing_commit: str = tag_commit(repository, tag)
    if existing_commit and existing_commit != commit:
        raise RuntimeError(tag + ' already names ' + existing_commit + ', not the tested ' + commit)
    view: subprocess.CompletedProcess[str] = gh(
        ['release', 'view', tag, '--repo', repository, '--json', 'isDraft'], check=False)
    files: list[str] = []
    entry: ReleaseArchive
    for entry in archives:
        files.append(str(entry.archive))
        files.append(str(entry.checksum))
    if dry_run:
        print('Dry run: would publish ' + tag + ' at ' + commit + ' with ' + str(len(files)) + ' files')
        return
    if view.returncode == 0:
        state: dict[str, object] = json.loads(view.stdout)
        if not state.get('isDraft'):
            print(tag + ' is already published at the tested commit; nothing to do')
            return
        gh(['release', 'upload', tag, '--repo', repository, '--clobber', *files])
    else:
        with tempfile.TemporaryDirectory() as directory:
            notes: Path = Path(directory) / 'notes.md'
            notes.write_text(release_notes(version, commit, repository, archives), encoding='utf-8')
            gh(['release', 'create', tag, '--repo', repository, '--target', commit, '--draft',
                '--prerelease', '--title', 'File Manager ' + version, '--notes-file', str(notes), *files])
    gh(['release', 'edit', tag, '--repo', repository, '--draft=false', '--prerelease'])
    published_commit: str = tag_commit(repository, tag)
    if published_commit != commit:
        raise RuntimeError(tag + ' was published naming ' + published_commit + ', not ' + commit)
    print('Published ' + tag + ' at ' + commit)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True, help='the downloaded archives and .sha256 files')
    parser.add_argument('--version', required=True, help='MAJOR.MINOR.PATCH, as the builds were packaged')
    parser.add_argument('--commit', required=True, help='the tested commit (full SHA)')
    parser.add_argument('--repository', required=True, help='owner/name')
    parser.add_argument('--dry-run', action='store_true', help='check the archives and print the plan only')
    args: argparse.Namespace = parser.parse_args()
    if re.fullmatch(VERSION_PATTERN, args.version) is None:
        parser.error('--version must be MAJOR.MINOR.PATCH')
    if re.fullmatch(COMMIT_PATTERN, args.commit) is None:
        parser.error('--commit must be a full lowercase SHA')
    archives: list[ReleaseArchive] = collect_archives(args.dist, args.version, args.commit)
    publish(args.version, args.commit, args.repository, archives, args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print('error: ' + str(error), file=sys.stderr)
        sys.exit(1)
