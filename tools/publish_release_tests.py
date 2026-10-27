#!/usr/bin/env python3
"""publish_release.py accepts exactly one clean, hash-checked archive per platform."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_release  # noqa: E402
from fetch_build_inputs import PLATFORMS  # noqa: E402
from native_build_support import sha256  # noqa: E402

VERSION: str = '0.1.7'
COMMIT: str = '0123456789abcdef0123456789abcdef01234567'


class CollectArchivesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.scratch: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.dist: Path = Path(self.scratch.name)
        platform: str
        for platform in PLATFORMS:
            extension: str = '.zip' if platform.startswith('windows-') else '.tar.gz'
            name: str = publish_release.archive_name(VERSION, platform, COMMIT) + extension
            self.write_archive(name, platform.encode('utf-8'))

    def tearDown(self) -> None:
        self.scratch.cleanup()

    def write_archive(self, name: str, content: bytes) -> None:
        archive: Path = self.dist / name
        archive.write_bytes(content)
        checksum_text: str = sha256(archive) + '  ' + name + '\n'
        (self.dist / (name + '.sha256')).write_text(checksum_text, encoding='utf-8')

    def test_accepts_one_archive_per_platform(self) -> None:
        archives: list[publish_release.ReleaseArchive] = publish_release.collect_archives(
            self.dist, VERSION, COMMIT)
        platforms: list[str] = []
        entry: publish_release.ReleaseArchive
        for entry in archives:
            platforms.append(entry.platform)
        self.assertEqual(platforms, list(PLATFORMS))
        notes: str = publish_release.release_notes(VERSION, COMMIT, 'owner/name', archives)
        self.assertIn('File Manager 0.1.7', notes)
        self.assertIn('linux-arm64', notes)

    def test_refuses_a_changed_archive(self) -> None:
        name: str = publish_release.archive_name(VERSION, 'linux-x64', COMMIT) + '.tar.gz'
        (self.dist / name).write_bytes(b'changed after hashing')
        with self.assertRaises(RuntimeError):
            publish_release.collect_archives(self.dist, VERSION, COMMIT)

    def test_refuses_a_missing_platform(self) -> None:
        name: str = publish_release.archive_name(VERSION, 'macos-arm64', COMMIT) + '.tar.gz'
        (self.dist / name).unlink()
        with self.assertRaises(RuntimeError):
            publish_release.collect_archives(self.dist, VERSION, COMMIT)

    def test_refuses_a_dirty_or_other_version_archive(self) -> None:
        dirty: str = publish_release.archive_name(VERSION, 'linux-x64', COMMIT) + '-dirty.tar.gz'
        self.write_archive(dirty, b'dirty tree')
        with self.assertRaises(RuntimeError):
            publish_release.collect_archives(self.dist, VERSION, COMMIT)
        with self.assertRaises(RuntimeError):
            publish_release.collect_archives(self.dist, '0.1.8', COMMIT)


if __name__ == '__main__':
    unittest.main()
