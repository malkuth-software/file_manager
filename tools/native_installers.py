#!/usr/bin/env python3
"""Build and check the installer for one verified File Manager package folder.

package_native.py calls build_installer() after the archive is written and
re-read, with the same staged folder, so an installer holds exactly the
files the archive does. Modelled on GUI.Forms' tools/package_programs.py:

  Windows  per-user NSIS installer into %LOCALAPPDATA%\\Programs\\File Manager,
           a Start Menu shortcut and an HKCU uninstall entry
  macOS    a pkgbuild component package into /Applications/File Manager,
           the bundle marked non-relocatable
  Linux    a Debian package into /opt/file-manager, a /usr/bin launcher and
           a desktop entry; Depends from dpkg-shlibdeps when present

Each installer is then re-read before it is admitted: the Debian package and
the macOS package are expanded into scratch and every packaged file's hash is
compared. The Windows installer can only be read back by running it, which
writes a Start Menu shortcut and an HKCU key, so that check (a silent
install into scratch, the comparison, then a silent uninstall) runs only when
the caller asks for it; CI does. The installer and its .sha256 are written
beside the archive; nothing else in the distribution folder is touched.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
from native_build_support import ROOT, sha256

TEMPLATES: Path = ROOT / 'packaging'
DISPLAY_NAME: str = 'File Manager'
NUMERIC_VERSION_PATTERN: str = r'[0-9]+\.[0-9]+\.[0-9]+'
# Debian's fallback when dpkg-shlibdeps is absent: the X11 and accessibility
# libraries the packaged GUI.Forms Linux host links (as GUI.Forms' own list).
LINUX_FALLBACK_DEPENDS: str = ('libc6, libx11-6, libxcursor1, libxfixes3, libxrender1, '
                               'libatk1.0-0t64 | libatk1.0-0, libatk-bridge2.0-0t64 | libatk-bridge2.0-0, '
                               'xdg-utils')


@dataclass(frozen=True)
class InstallerIdentity:
    """The names an installed File Manager is known by (the owner's product vendor identity, 2026-10-27)."""
    vendor: str
    maintainer: str
    repository: str
    bundle_identifier: str
    deb_package: str
    windows_install_dir: str
    windows_uninstall_key: str
    macos_install_location: str
    linux_install_dir: str


IDENTITY: InstallerIdentity = InstallerIdentity(
    vendor='Malkuth Software',
    maintainer='Malkuth Software <maintainer@malkuthsoft.com>',
    repository='malkuth-software/file_manager',
    bundle_identifier='com.malkuthsoft.filemanager',
    deb_package='file-manager',
    windows_install_dir='$LOCALAPPDATA\\Programs\\File Manager',
    windows_uninstall_key='Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MalkuthSoftwareFileManager',
    macos_install_location='/Applications/File Manager',
    linux_install_dir='/opt/file-manager')


@dataclass(frozen=True)
class InstallerResult:
    installer: Path
    checksum: Path
    check: str


def run(command: list[str], cwd: Path | None = None) -> str:
    """Run a command, raise with its output on failure, return its standard output."""
    print('+ ' + ' '.join(command), flush=True)
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(' '.join(command) + ' failed (' + str(completed.returncode) + '):\n'
                           + completed.stdout + completed.stderr)
    return completed.stdout


def fill_template(name: str, values: dict[str, str]) -> str:
    """Replace every @NAME@ in packaging/<name>; raise if one is left unfilled."""
    template_path: Path = TEMPLATES / name
    text: str = template_path.read_text(encoding='utf-8')
    placeholder: str
    value: str
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    lines: list[str] = text.split('\n')
    body: str = '\n'.join(lines[1:]) if name.endswith('.nsi.in') else text
    leftovers: list[str] = sorted(set(re.findall(r'@[A-Z0-9_]+@', body)))
    if leftovers:
        raise RuntimeError(name + ' has unfilled placeholders: ' + ', '.join(leftovers))
    return text


def compare_tree(root: Path, files: dict[str, str], where: str) -> None:
    """Raise unless every packaged file is under root with its recorded hash."""
    relative: str
    for relative in files:
        installed: Path = root / relative
        if not installed.is_file():
            raise RuntimeError(where + ' lacks ' + relative)
        if sha256(installed) != files[relative]:
            raise RuntimeError(where + ' holds a different ' + relative)


def windows_product_version(version: str) -> str:
    """NSIS VIProductVersion needs four numbers; a development version reads 0.0.0.0."""
    if re.fullmatch(NUMERIC_VERSION_PATTERN, version) is None:
        return '0.0.0.0'
    result: str = version + '.0'
    return result


def find_makensis() -> str:
    """The official NSIS first (the Windows runner image ships it); MSYS2's build rejects MUI pages."""
    roots: list[str] = [os.environ.get('NSIS_HOME', ''), os.environ.get('ProgramFiles(x86)', ''),
                        os.environ.get('ProgramFiles', ''), 'C:/Program Files (x86)', 'C:/Program Files']
    root: str
    for root in roots:
        if not root:
            continue
        candidate: Path
        for candidate in (Path(root) / 'makensis.exe', Path(root) / 'NSIS' / 'makensis.exe'):
            if candidate.is_file():
                return str(candidate)
    located: str | None = shutil.which('makensis')
    if located is None:
        raise RuntimeError('makensis is not installed (NSIS 3)')
    return located


def windows_installer(package: Path, installer: Path, version: str, scratch: Path) -> None:
    files: list[str] = []
    directories: list[str] = []
    path: Path
    for path in sorted(package.rglob('*')):
        relative: str = str(path.relative_to(package)).replace('/', '\\')
        if path.is_file():
            files.append('  Delete "$INSTDIR\\' + relative + '"')
        elif path.is_dir():
            directories.append(relative)
    directories.sort(key=len, reverse=True)
    removals: list[str] = []
    directory: str
    for directory in directories:
        removals.append('  RMDir "$INSTDIR\\' + directory + '"')
    values: dict[str, str] = {
        '@DISPLAY_NAME@': DISPLAY_NAME,
        '@OUTFILE@': str(installer).replace('/', '\\'),
        '@INSTALL_DIR@': IDENTITY.windows_install_dir,
        '@VERSION@': version,
        '@VERSION4@': windows_product_version(version),
        '@VENDOR@': IDENTITY.vendor,
        '@BUNDLE@': str(package).replace('/', '\\'),
        '@EXECUTABLE@': DISPLAY_NAME + '.exe',
        '@UNINSTALL_KEY@': IDENTITY.windows_uninstall_key,
        '@UNINSTALL_FILES@': '\n'.join(files),
        '@UNINSTALL_DIRECTORIES@': '\n'.join(removals),
    }
    script: Path = scratch / 'installer.nsi'
    script.write_text(fill_template('windows_installer.nsi.in', values), encoding='utf-8')
    run([find_makensis(), str(script)])


def check_windows_installer(installer: Path, files: dict[str, str], scratch: Path) -> str:
    """Install silently into scratch, compare, then uninstall silently and confirm the removal."""
    target: Path = scratch / 'installed'
    windows_target: str = str(target).replace('/', '\\')
    # /D must be last and unquoted (NSIS); _?= runs the uninstaller in place
    # and waits for it, leaving only Uninstall.exe for the caller to remove.
    run([str(installer), '/S', '/D=' + windows_target])
    compare_tree(target, files, 'the installed Windows tree')
    uninstaller: Path = target / 'Uninstall.exe'
    run([str(uninstaller), '/S', '_?=' + windows_target])
    remaining: list[Path] = []
    path: Path
    for path in target.rglob('*'):
        if path != uninstaller:
            remaining.append(path)
    if remaining:
        raise RuntimeError('the uninstaller left ' + str(len(remaining)) + ' paths, e.g. ' + str(remaining[0]))
    uninstaller.unlink()
    target.rmdir()
    return 'installed silently into scratch, every file matched, uninstalled silently'


def macos_installer(package: Path, installer: Path, version: str, scratch: Path) -> None:
    root: Path = scratch / 'pkg-root'
    shutil.copytree(package, root, symlinks=True)
    components: Path = scratch / 'components.plist'
    run(['pkgbuild', '--analyze', '--root', str(root), str(components)])
    records: list[dict[str, object]] = plistlib.loads(components.read_bytes())
    record: dict[str, object]
    for record in records:
        record['BundleIsRelocatable'] = False
    components.write_bytes(plistlib.dumps(records))
    run(['pkgbuild', '--root', str(root), '--component-plist', str(components),
         '--install-location', IDENTITY.macos_install_location,
         '--identifier', IDENTITY.bundle_identifier, '--version', version, str(installer)])


def check_macos_installer(installer: Path, files: dict[str, str], scratch: Path) -> str:
    expanded: Path = scratch / 'pkg-expanded'
    run(['pkgutil', '--expand-full', str(installer), str(expanded)])
    compare_tree(expanded / 'Payload', files, 'the macOS package payload')
    return 'payload expanded, every file matched'


def debian_architecture(architecture: str) -> str:
    if architecture == 'arm64':
        return 'arm64'
    return 'amd64'


def linux_installer(package: Path, installer: Path, version: str, architecture: str, scratch: Path) -> None:
    root: Path = scratch / 'deb-root'
    installed: Path = root / IDENTITY.linux_install_dir.lstrip('/')
    shutil.copytree(package, installed, symlinks=True)
    executable: str = IDENTITY.linux_install_dir + '/' + DISPLAY_NAME
    launcher: Path = root / 'usr/bin' / IDENTITY.deb_package
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec "' + executable + '" "$@"\n', encoding='utf-8')
    launcher.chmod(0o755)
    desktop: Path = root / 'usr/share/applications' / (IDENTITY.deb_package + '.desktop')
    desktop.parent.mkdir(parents=True)
    desktop.write_text(fill_template('linux_desktop.in', {
        '@DISPLAY_NAME@': DISPLAY_NAME, '@LAUNCHER@': IDENTITY.deb_package}), encoding='utf-8')
    depends: str = LINUX_FALLBACK_DEPENDS
    if shutil.which('dpkg-shlibdeps') is not None:
        metadata: Path = scratch / 'debian/control'
        metadata.parent.mkdir()
        metadata.write_text('Source: ' + IDENTITY.deb_package + '\n\nPackage: ' + IDENTITY.deb_package
                            + '\nArchitecture: any\n', encoding='utf-8')
        binaries: list[str] = ['-e' + str(installed / DISPLAY_NAME)]
        library: Path
        for library in sorted(installed.glob('libgui_forms_*.so*')):
            binaries.append('-e' + str(library))
        shlibs: str = run(['dpkg-shlibdeps', '-O', '-l' + str(installed), '--ignore-missing-info', *binaries],
                          cwd=scratch)
        discovered: str = shlibs.strip().removeprefix('shlibs:Depends=').strip()
        if discovered:
            depends = discovered + ', xdg-utils'
    control: Path = root / 'DEBIAN/control'
    control.parent.mkdir()
    control.write_text(fill_template('deb_control.in', {
        '@PACKAGE@': IDENTITY.deb_package, '@VERSION@': version,
        '@ARCH@': debian_architecture(architecture), '@MAINTAINER@': IDENTITY.maintainer,
        '@REPOSITORY@': IDENTITY.repository, '@DEPENDS@': depends,
        '@INSTALL_DIR@': IDENTITY.linux_install_dir}), encoding='utf-8')
    run(['dpkg-deb', '--root-owner-group', '--build', str(root), str(installer)])


def check_linux_installer(installer: Path, files: dict[str, str], scratch: Path) -> str:
    extracted: Path = scratch / 'deb-extracted'
    run(['dpkg-deb', '-x', str(installer), str(extracted)])
    compare_tree(extracted / IDENTITY.linux_install_dir.lstrip('/'), files, 'the Debian package')
    launcher: Path = extracted / 'usr/bin' / IDENTITY.deb_package
    if not launcher.is_file():
        raise RuntimeError('the Debian package lacks its launcher')
    return 'package extracted, every file matched'


def installer_extension(host: str) -> str:
    if host == 'windows':
        return '-setup.exe'
    if host == 'macos':
        return '.pkg'
    return '.deb'


def build_installer(host: str, architecture: str, package: Path, distribution: Path, name: str,
                    version: str, files: dict[str, str], install_check: bool) -> InstallerResult:
    """Write distribution/<name><extension> and its .sha256 from the verified package folder.

    files maps each packaged path to its hash (package_native.package_file_hashes).
    install_check admits the Windows check that installs into scratch; on
    the other hosts the check never changes the system and always runs.
    """
    installer: Path = distribution / (name + installer_extension(host))
    installer.unlink(missing_ok=True)
    check: str = ''
    with tempfile.TemporaryDirectory(prefix='file-manager-installer-') as directory:
        scratch: Path = Path(directory)
        if host == 'windows':
            windows_installer(package, installer, version, scratch)
            check = 'not re-read: running it is the only reader (pass --installer-install-check)'
            if install_check:
                check = check_windows_installer(installer, files, scratch)
        elif host == 'macos':
            macos_installer(package, installer, version, scratch)
            check = check_macos_installer(installer, files, scratch)
        else:
            linux_installer(package, installer, version, architecture, scratch)
            check = check_linux_installer(installer, files, scratch)
    checksum: Path = distribution / (installer.name + '.sha256')
    checksum.write_text(sha256(installer) + '  ' + installer.name + '\n', encoding='utf-8')
    result: InstallerResult = InstallerResult(installer=installer, checksum=checksum, check=check)
    return result
