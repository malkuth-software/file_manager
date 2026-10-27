#!/usr/bin/env python3
"""native_installers.py fills every template placeholder and compares installed trees exactly."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_installers  # noqa: E402
from native_build_support import sha256  # noqa: E402


class TemplateTests(unittest.TestCase):

    def test_templates_fill_completely(self) -> None:
        nsis: str = native_installers.fill_template('windows_installer.nsi.in', {
            '@DISPLAY_NAME@': 'File Manager', '@OUTFILE@': 'out.exe', '@INSTALL_DIR@': 'dir',
            '@VERSION@': '0.1.7', '@VERSION4@': '0.1.7.0', '@VENDOR@': 'Vendor', '@BUNDLE@': 'bundle',
            '@EXECUTABLE@': 'File Manager.exe', '@UNINSTALL_KEY@': 'key', '@UNINSTALL_FILES@': '',
            '@UNINSTALL_DIRECTORIES@': ''})
        self.assertIn('OutFile "out.exe"', nsis)
        control: str = native_installers.fill_template('deb_control.in', {
            '@PACKAGE@': 'p', '@VERSION@': '0.1.7', '@ARCH@': 'arm64', '@MAINTAINER@': 'm',
            '@REPOSITORY@': 'r', '@DEPENDS@': 'd', '@INSTALL_DIR@': '/opt/file-manager'})
        self.assertIn('Architecture: arm64', control)

    def test_missing_value_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            native_installers.fill_template('linux_desktop.in', {'@DISPLAY_NAME@': 'File Manager'})

    def test_versions_and_architectures(self) -> None:
        self.assertEqual(native_installers.windows_product_version('0.1.7'), '0.1.7.0')
        self.assertEqual(native_installers.windows_product_version('0.1-dev'), '0.0.0.0')
        self.assertEqual(native_installers.debian_architecture('arm64'), 'arm64')
        self.assertEqual(native_installers.debian_architecture('x64'), 'amd64')


class CompareTreeTests(unittest.TestCase):

    def test_compare_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root: Path = Path(directory)
            (root / 'fonts').mkdir()
            packaged: Path = root / 'fonts/face.ttf'
            packaged.write_bytes(b'face')
            files: dict[str, str] = {'fonts/face.ttf': sha256(packaged)}
            native_installers.compare_tree(root, files, 'fixture')
            packaged.write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                native_installers.compare_tree(root, files, 'fixture')
            packaged.unlink()
            with self.assertRaises(RuntimeError):
                native_installers.compare_tree(root, files, 'fixture')


if __name__ == '__main__':
    unittest.main()
