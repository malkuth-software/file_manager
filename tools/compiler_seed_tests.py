"""A cache seed may only restore its declared cache tree and provider identity."""
from __future__ import annotations
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from fetch_build_inputs import MACOS_RUNTIME_LAYOUT, restore_cache_seed


class CompilerSeedTests(unittest.TestCase):
    def make_seed(self, root: Path, member_name: str = '.ccache/a/result',
                  platform: str = 'linux-x64', repository: str = 'falseywinchnet/gui_forms',
                  schema: int = 2) -> Path:
        manifest: dict[str, object] = {
            'schema': schema, 'repository': repository, 'platform': platform, 'revision': 'provider-sha'}
        files: dict[str, bytes] = {
            'cache-manifest.json': json.dumps(manifest).encode('utf-8'),
            member_name: b'cached compile result'}
        path: Path = root / 'seed.tar.gz'
        archive: tarfile.TarFile
        with tarfile.open(path, 'w:gz') as archive:
            name: str
            for name in files:
                member: tarfile.TarInfo = tarfile.TarInfo(name)
                member.size = len(files[name])
                archive.addfile(member, io.BytesIO(files[name]))
        return path

    def test_seed_restores_only_after_identity_validation(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = self.make_seed(root)
            destination: Path = root / 'consumer'
            manifest: dict[str, object] = restore_cache_seed(path, destination, 'linux-x64', 'provider-sha')
            self.assertEqual((destination / '.ccache/a/result').read_bytes(),
                             b'cached compile result')
            self.assertEqual(manifest['revision'], 'provider-sha')

    def test_renamed_provider_repository_is_the_same_provider(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = self.make_seed(root, repository='malkuth-software/gui_forms')
            destination: Path = root / 'consumer'
            restore_cache_seed(path, destination, 'linux-x64', 'provider-sha')
            self.assertTrue((destination / '.ccache/a/result').is_file())

    def test_wrong_provider_and_platform_leave_destination_absent(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = self.make_seed(root)
            destination: Path = root / 'consumer'
            with self.assertRaisesRegex(ValueError, 'provider identity'):
                restore_cache_seed(path, destination, 'linux-x64', 'wrong-sha')
            with self.assertRaisesRegex(ValueError, 'provider identity'):
                restore_cache_seed(path, destination, 'windows-x64', 'provider-sha')
            self.assertFalse(destination.exists())
            other: Path = self.make_seed(root, repository='someone-else/gui_forms')
            with self.assertRaisesRegex(ValueError, 'provider identity'):
                restore_cache_seed(other, destination, 'linux-x64', 'provider-sha')
            old_schema: Path = self.make_seed(root, schema=1)
            with self.assertRaisesRegex(ValueError, 'provider identity'):
                restore_cache_seed(old_schema, destination, 'linux-x64', 'provider-sha')
            self.assertFalse(destination.exists())

    def test_outside_cache_member_is_rejected_before_extraction(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = self.make_seed(root, '.ccache/../../outside')
            destination: Path = root / 'consumer'
            with self.assertRaisesRegex(ValueError, 'Invalid compiler-cache'):
                restore_cache_seed(path, destination, 'linux-x64', 'provider-sha')
            self.assertFalse(destination.exists())

    def test_macos_runtime_prefix_is_admitted_only_on_macos(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            runtime_member: str = MACOS_RUNTIME_LAYOUT + '/lib/libc++.1.dylib'
            mac_seed: Path = self.make_seed(root, runtime_member, platform='macos-arm64')
            destination: Path = root / 'consumer'
            restore_cache_seed(mac_seed, destination, 'macos-arm64', 'provider-sha')
            self.assertTrue((destination / runtime_member).is_file())
            linux_seed: Path = self.make_seed(root, runtime_member)
            linux_destination: Path = root / 'linux-consumer'
            with self.assertRaisesRegex(ValueError, 'Invalid compiler-cache'):
                restore_cache_seed(linux_seed, linux_destination, 'linux-x64', 'provider-sha')
            self.assertFalse(linux_destination.exists())

    def test_other_build_paths_are_rejected_on_macos(self) -> None:
        temporary: str
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = self.make_seed(root, '.build/native-macos-arm64/gui-forms/CMakeCache.txt',
                                        platform='macos-arm64')
            destination: Path = root / 'consumer'
            with self.assertRaisesRegex(ValueError, 'Invalid compiler-cache'):
                restore_cache_seed(path, destination, 'macos-arm64', 'provider-sha')
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
