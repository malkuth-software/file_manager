#!/usr/bin/env python3
"""Build a development distribution using native tools; never install services.

The toolkit is built by GUI.Forms' own tools/ci_build.py in the consumer layout
it defines (gui_forms/ beside .build/native-<platform>/gui-forms/), so every
compilation has the provider CI's configure options, toolchain file and paths,
and a restored provider cache can hit. The frontend is configured with the
same cmake/llvm22.cmake toolchain file against the installed SDK.

--gui-forms-lockstep names the machine's lockstep record instead
(D:/malkuth/cache/lockstep/current-<target>.json, written by template_harness
harness/lockstep.py): GUI.Forms is then not built here. Its installed prefix,
source clone, toolchain file and fonts come from the record, which names one
GUI.Forms commit for every product built on this machine.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from types import ModuleType
from native_build_support import ROOT, BuildValidation, git_output, record_source_state
from native_build_support import output, run, sdk_fingerprint, sha256
from backend_bundle import install_bundle
from fetch_build_inputs import FRONTEND_ONLY_PLATFORMS, MACOS_RUNTIME_LAYOUT, verify_source_pins

PROVIDER_TOOLS: Path = ROOT / 'gui_forms/tools'
# The pinned harness (harness.lock), checked out inside this workspace root by
# template_harness/harness/lock.py or CI's harness-checkout step.
HARNESS_TOOLS: Path = ROOT / 'template_harness/harness'
# The harness tools read their product's names (environment variables, test
# prefixes, source root) from HARNESS_PROJECT. This script and the frontend
# configure it runs are File Manager's; the toolkit build it starts is
# GUI.Forms' own, run as GUI.Forms' CI runs it, in this same workspace.
FILE_MANAGER_PROJECT: Path = ROOT / 'template_harness/projects/file_manager.toml'
GUI_FORMS_PROJECT: Path = ROOT / 'template_harness/projects/gui_forms.toml'
# The submodule's toolchain file; the default ToolkitSource and the CI SDK export use it.
TOOLCHAIN_FILE: Path = ROOT / 'gui_forms/cmake/llvm22.cmake'
# The one frontend test the bootstrap's smoke build runs.
SMOKE_TEST: str = 'file_manager_frontend_model_tests'
# The lockstep record's layout (harness/lockstep.py); another schema is refused.
LOCKSTEP_SCHEMA: int = 1
LOCKSTEP_FIELDS: tuple[str, ...] = ('commit', 'target', 'features', 'compiler', 'prefix', 'package_dir',
                                    'source', 'toolchain_file', 'font_dir', 'llvm_runtime', 'lang')
# Optional record fields: harness is the template_harness commit lockstep.py
# placed beside the source clone ("" when that GUI.Forms commit has no
# harness.lock); lockstep.py checks it, so it is only reported here.
LOCKSTEP_OPTIONAL_FIELDS: tuple[str, ...] = ('harness',)
# The lockstep feature set File Manager links: its native host, CPU Skia and
# the text stack, which only the whole toolkit ("all") provides.
LOCKSTEP_FEATURES: str = 'all'


@dataclass(frozen=True)
class HostConfiguration:
    host: str
    architecture: str


@dataclass(frozen=True)
class ToolkitSource:
    """Where the GUI.Forms pieces of a frontend build come from.

    `source` is a GUI.Forms source tree: its tools/ci_build.py supplies the
    configure record and sanitizer options, and tools/sanitizer/lsan.supp the
    leak baseline. `sdk` is an installed prefix to build against, or None to
    build one from the submodule with ci_build.py. `revision` is the GUI.Forms
    commit the consumption manifest records. `llvm_runtime` is the macOS
    runtime prefix the record names, empty when the environment decides.
    """
    source: Path
    toolchain_file: Path
    font_dir: Path
    revision: str
    sdk: Path | None
    llvm_runtime: str
    description: str


def host_configuration() -> HostConfiguration:
    """The platform name exactly as the provider's ci_build.py derives it, so both use one build directory.

    Anything but arm64/aarch64 is x64: on Windows platform.machine() reads
    PROCESSOR_ARCHITECTURE and is empty in a shell that does not carry it.
    """
    systems: dict[str, str] = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}
    system: str = platform.system()
    machine: str = platform.machine().lower()
    host: str = systems[system]
    architecture: str = 'x64'
    if machine in ('arm64', 'aarch64'):
        architecture = 'arm64'
    result: HostConfiguration = HostConfiguration(host=host, architecture=architecture)
    return result


def submodule_source(supplied_sdk: Path | None) -> ToolkitSource:
    """The pinned gui_forms/ submodule, and an SDK built from it unless one is supplied."""
    revision: str = git_output('-C', str(ROOT / 'gui_forms'), 'rev-parse', 'HEAD')
    description: str = 'provider ci_build.py: native build, CTest and installed reference examples passed'
    sdk: Path | None = None
    if supplied_sdk is not None:
        sdk = supplied_sdk.resolve()
        description = 'externally supplied'
    result: ToolkitSource = ToolkitSource(
        source=ROOT / 'gui_forms', toolchain_file=TOOLCHAIN_FILE, font_dir=ROOT / 'gui_forms/assets/fonts',
        revision=revision, sdk=sdk, llvm_runtime='', description=description)
    return result


def lockstep_source(record_path: Path, configuration: HostConfiguration) -> ToolkitSource:
    """Read and check the lockstep record; nothing is changed if it is refused.

    The record must be schema 1 for this host's target and the whole feature
    set (features is part of the lockstep key), its source clone must
    be at the recorded commit, and the clang on PATH must be the recorded
    compiler, since SDK C++ objects are not ABI compatible across compilers.
    On success LANG is set to the recorded locale, which ccache hashes.
    """
    text: str = record_path.read_text(encoding='utf-8')
    record: dict[str, object] = json.loads(text)
    if record.get('schema') != LOCKSTEP_SCHEMA:
        raise RuntimeError(f'{record_path}: lockstep schema {record.get("schema")!r}, expected {LOCKSTEP_SCHEMA}')
    values: dict[str, str] = {}
    field: str
    for field in LOCKSTEP_FIELDS:
        value: object = record.get(field)
        if not isinstance(value, str):
            raise RuntimeError(f'{record_path}: field {field} is missing or not a string')
        values[field] = value
    for field in LOCKSTEP_OPTIONAL_FIELDS:
        optional: object = record.get(field, '')
        if not isinstance(optional, str):
            raise RuntimeError(f'{record_path}: field {field} is not a string')
        values[field] = optional
    target: str = configuration.host + '-' + configuration.architecture
    if values['target'] != target:
        raise RuntimeError(f'{record_path}: lockstep target {values["target"]}, this host is {target}')
    if values['features'] != LOCKSTEP_FEATURES:
        raise RuntimeError(f'{record_path}: lockstep features {values["features"]!r}; '
                           f'File Manager needs {LOCKSTEP_FEATURES!r}')
    source: Path = Path(values['source'])
    prefix: Path = Path(values['prefix'])
    if Path(values['package_dir']) != prefix / 'lib/cmake/GUIForms':
        raise RuntimeError(f'{record_path}: package_dir is not <prefix>/lib/cmake/GUIForms')
    head: str = git_output('-C', str(source), 'rev-parse', 'HEAD')
    if head != values['commit']:
        raise RuntimeError(f'{source} is at {head}, but the lockstep record names {values["commit"]}')
    located: str | None = shutil.which('clang')
    if located is None or not os.path.samefile(located, values['compiler']):
        raise RuntimeError(f'clang on PATH ({located}) is not the lockstep compiler {values["compiler"]}')
    if values['lang']:
        os.environ['LANG'] = values['lang']
    result: ToolkitSource = ToolkitSource(
        source=source, toolchain_file=Path(values['toolchain_file']), font_dir=Path(values['font_dir']),
        revision=values['commit'], sdk=prefix, llvm_runtime=values['llvm_runtime'],
        description='lockstep ' + values['commit'] + ' ' + values['features'] + ' harness '
        + (values['harness'] or 'none') + ' (' + str(record_path) + ')')
    return result


def provider_build_module(toolkit: ToolkitSource) -> ModuleType:
    """Import the toolkit source's ci_build.py; its configure and sanitizer rules stay single-sourced."""
    location: str = str(toolkit.source / 'tools')
    if location not in sys.path:
        sys.path.insert(0, location)
    import ci_build
    module: ModuleType = ci_build
    return module


def select_macos_runtime(host: str, toolkit: ToolkitSource) -> None:
    """Point cmake/llvm22.cmake at the LLVM 22.1.8 macOS 14 runtime and verify it byte for byte.

    The provider cache archive carries the runtime at MACOS_RUNTIME_LAYOUT
    (tools/fetch_build_inputs.py restores it). A lockstep record's runtime wins,
    then an explicit GUI_FORMS_LLVM_RUNTIME.
    FILE_MANAGER_LLVM_RUNTIME, the harness's name for this project, is set to
    the same prefix; GUI.Forms' toolchain file reads GUI_FORMS_LLVM_RUNTIME.
    """
    if host != 'macos':
        return
    if toolkit.llvm_runtime:
        os.environ['GUI_FORMS_LLVM_RUNTIME'] = toolkit.llvm_runtime
    if not os.environ.get('GUI_FORMS_LLVM_RUNTIME'):
        os.environ['GUI_FORMS_LLVM_RUNTIME'] = str(ROOT / MACOS_RUNTIME_LAYOUT)
    runtime: str = os.environ['GUI_FORMS_LLVM_RUNTIME']
    os.environ['FILE_MANAGER_LLVM_RUNTIME'] = runtime
    run(sys.executable, HARNESS_TOOLS / 'macos_runtime_manifest.py', runtime)


def build_toolkit(jobs: int, test_jobs: int) -> None:
    """Build, test and install into .build/native-<platform>/gui-forms-sdk exactly as provider CI does."""
    environment: dict[str, str] = dict(os.environ)
    environment['HARNESS_PROJECT'] = str(GUI_FORMS_PROJECT)
    environment['HARNESS_WORKSPACE'] = str(ROOT)
    run(sys.executable, PROVIDER_TOOLS / 'ci_build.py', '--jobs', jobs, '--test-jobs', test_jobs,
        environment=environment)


def toolchain_identity(toolkit: ToolkitSource) -> dict[str, str]:
    """The compiler that built this consumer; a cache hit needs this exact binary."""
    version_text: str = output('clang', '--version')
    first_line: str = version_text.splitlines()[0]
    located: str | None = shutil.which('clang')
    if located is None:
        raise RuntimeError('clang is missing from PATH')
    compiler_path: Path = Path(located)
    identity: dict[str, str] = {'clang_version': first_line, 'clang_sha256': sha256(compiler_path),
                                'toolchain_file': toolkit.toolchain_file.as_posix()}
    return identity


def configure_frontend(toolkit: ToolkitSource, frontend: Path, sdk: Path, build: Path, manifest: Path,
                       extra_options: list[str]) -> None:
    """Configure through the provider's configure record, which skips an unchanged reconfigure."""
    ci_build: ModuleType = provider_build_module(toolkit)
    configure: list[str] = [
        'cmake', '-S', str(ROOT / 'frontend'), '-B', str(frontend), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_TOOLCHAIN_FILE=' + str(toolkit.toolchain_file),
        '-DCMAKE_NO_SYSTEM_FROM_IMPORTED=ON', '-DCMAKE_PREFIX_PATH=' + str(sdk),
        '-DGUIForms_DIR=' + str(sdk / 'lib/cmake/GUIForms'),
        '-DFILE_MANAGER_GUI_FORMS_MANIFEST=' + str(manifest),
        '-DCMAKE_INSTALL_PREFIX=' + str(build / 'frontend-sdk')]
    configure.extend(extra_options)
    ci_build.configure_tree(frontend, configure)


def build_sanitized_frontend(toolkit: ToolkitSource, host: str, build: Path, sdk: Path, manifest: Path,
                             jobs: int, test_jobs: int) -> None:
    """Build and test the frontend under the provider's sanitizer profile; install and package nothing.

    Only File Manager code is instrumented: the Release SDK is linked
    unchanged, as provider CI links its prebuilt Skia. Every report stops
    its test. The Windows MinGW runtime has no LeakSanitizer.
    """
    ci_build: ModuleType = provider_build_module(toolkit)
    # Provider sanitizer leak baseline (macOS framework allocations; see its building docs).
    leak_suppressions: Path = toolkit.source / 'tools/sanitizer/lsan.supp'
    options: list[str]
    environment: dict[str, str]
    options, environment = ci_build.sanitizer_options(host, leak_suppressions, 'lines')
    os.environ.update(environment)
    # The MinGW sanitizer runtime is a DLL; this tree is tested, never packaged.
    options.append('-DFILE_MANAGER_CHECK_RUNTIME_IMPORTS=OFF')
    if host == 'windows':
        # Sanitized, the deprecated Web.Forms generator's make_native_form()
        # and button-recipe frames need about 1.1 MB, over Windows' 1 MB main
        # thread (gui_forms tools/stack_frames.py). Reserve the 8 MB Linux and
        # macOS give main, in this tree only, until the generator is replaced;
        # packaged programs keep the default (notes:planning/GUI_FORMS_0_2_CONSUMPTION_2026-10-26.md).
        options.append('-DCMAKE_EXE_LINKER_FLAGS=' + ci_build.SANITIZER_LINK_FLAGS + ' -Wl,--stack,8388608')
    frontend: Path = build / 'frontend-sanitize'
    configure_frontend(toolkit, frontend, sdk, build, manifest, options)
    run('cmake', '--build', frontend, '--parallel', jobs)
    os.environ['GUI_FORMS_FONT_DIR'] = str(toolkit.font_dir)
    run('ctest', '--test-dir', frontend, '--output-on-failure', '--timeout', '900', '--parallel', test_jobs)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--test-jobs', type=int, default=1, help='tests ctest runs at once (default 1)')
    parser.add_argument('--gui-forms-sdk', type=Path, help='Reuse an already tested native SDK')
    parser.add_argument('--gui-forms-lockstep', type=Path, metavar='RECORD',
                        help="build against the machine's lockstep GUI.Forms "
                             '(D:/malkuth/cache/lockstep/current-<target>.json): prefix, source clone, toolchain '
                             'file and fonts from the record; GUI.Forms is not built here')
    parser.add_argument('--backend-bundle', type=Path, help='Pinned backend archive; defaults to .build/inputs/<platform>')
    parser.add_argument('--skip-components', action='store_true', help='Explicit frontend-only development package')
    parser.add_argument('--sanitize', action='store_true',
                        help='build and test the frontend in frontend-sanitize under ASan/UBSan (LSan on '
                             'Linux and macOS) against an already built SDK; no install or package')
    parser.add_argument('--smoke', action='store_true',
                        help='the harness bootstrap smoke build: the toolkit through the provider ci_build.py, '
                             'then only ' + SMOKE_TEST + '; no backend, install or package')
    arguments: argparse.Namespace = parser.parse_args()
    jobs: int = arguments.jobs
    test_jobs: int = arguments.test_jobs
    supplied_sdk: Path | None = arguments.gui_forms_sdk
    lockstep_record: Path | None = arguments.gui_forms_lockstep
    skip_components: bool = arguments.skip_components
    sanitize: bool = arguments.sanitize
    smoke: bool = arguments.smoke
    if jobs < 1 or test_jobs < 1:
        parser.error('--jobs and --test-jobs must be positive')
    if smoke and sanitize:
        parser.error('--smoke and --sanitize are separate builds')
    if smoke:
        # The smoke build needs no backend: it packages nothing.
        skip_components = True
    if lockstep_record is not None and supplied_sdk is not None:
        parser.error('--gui-forms-lockstep names its own SDK; leave out --gui-forms-sdk')
    # CI's harness-checkout sets these; a local run gets the same identity
    # before the provider's ci_build module is imported.
    os.environ.setdefault('HARNESS_PROJECT', str(FILE_MANAGER_PROJECT))
    os.environ.setdefault('HARNESS_WORKSPACE', str(ROOT))
    configuration: HostConfiguration = host_configuration()
    host: str = configuration.host
    architecture: str = configuration.architecture
    if host + '-' + architecture in FRONTEND_ONLY_PLATFORMS and not skip_components and not sanitize:
        parser.error(host + '-' + architecture + ' has no pinned backend bundle; pass --skip-components')
    toolkit: ToolkitSource
    if lockstep_record is not None:
        toolkit = lockstep_source(lockstep_record.resolve(), configuration)
    else:
        toolkit = submodule_source(supplied_sdk)
    build: Path = ROOT / '.build' / f'native-{host}-{architecture}'
    build.mkdir(parents=True, exist_ok=True)
    record_source_state(build, 'before-build')
    lock: dict[str, dict[str, object]] = {}
    if not skip_components:
        lock = json.loads((ROOT / 'dependencies.lock.json').read_text(encoding='utf-8'))
        verify_source_pins(lock, ROOT)
    sdk: Path = build / 'gui-forms-sdk'
    toolkit_validation: str = 'passed'
    if toolkit.sdk is not None:
        sdk = toolkit.sdk
        toolkit_validation = 'externally supplied SDK: ' + toolkit.description
    os.environ['BUILD_JOBS'] = str(jobs)
    os.environ.setdefault('CC', 'clang')
    os.environ.setdefault('CXX', 'clang++')
    os.environ.setdefault('OBJCXX', 'clang++')
    select_macos_runtime(host, toolkit)
    manifest: Path = build / 'gui-forms-consumption.json'
    if sanitize:
        if not (sdk / 'lib/cmake/GUIForms').is_dir() or not manifest.is_file():
            parser.error('--sanitize needs an SDK and consumption manifest from an earlier build')
        build_sanitized_frontend(toolkit, host, build, sdk, manifest, jobs, test_jobs)
        return
    if not skip_components:
        target: str = host + '-' + architecture
        backend: dict[str, object] = lock['backend']
        hashes: object = backend['sha256']
        if not isinstance(hashes, dict):
            raise RuntimeError('Backend hashes missing from dependency lock')
        bundle: Path = ROOT / '.build/inputs' / target / ('backend-' + target + '.tar.gz')
        if arguments.backend_bundle is not None:
            bundle = arguments.backend_bundle
        install_bundle(bundle, build / 'components', target,
                       str(backend['revision']), str(hashes[target]))
    if toolkit.sdk is None:
        build_toolkit(jobs, test_jobs)
    revision: str = git_output('rev-parse', 'HEAD')
    toolkit_revision: str = toolkit.revision
    manifest_record: dict[str, object] = {
        'identity': {'id': f'gui-forms-development-{host}-{architecture}-{toolkit_revision[:12]}',
                     'state': 'development', 'source_revision': toolkit_revision},
        'validation': {'sdk': toolkit.description},
        'toolchain': toolchain_identity(toolkit),
        'limits': ['No platform promotion or installed service claim; see package receipt']}
    manifest_text: str = json.dumps(manifest_record, indent=2) + '\n'
    manifest.write_text(manifest_text, encoding='utf-8')
    frontend: Path = build / 'frontend'
    sdk_identity: str = sdk_fingerprint(sdk)
    configure_frontend(toolkit, frontend, sdk, build, manifest, [])
    sdk_stamp: Path = frontend / 'sdk-fingerprint.txt'
    previous_identity: str = ''
    if sdk_stamp.is_file():
        previous_text: str = sdk_stamp.read_text(encoding='utf-8')
        previous_identity = previous_text.strip()
    if previous_identity != sdk_identity:
        # Installed header mtimes alone cannot protect a changed public C++ layout.
        run('cmake', '--build', frontend, '--target', 'clean')
    if smoke:
        # At this GUI.Forms pin ci_build.py has already built and tested the
        # whole toolkit (or the lockstep prefix was tested where it was
        # built); the frontend contributes one test.
        run('cmake', '--build', frontend, '--target', SMOKE_TEST, '--parallel', jobs)
        os.environ['GUI_FORMS_FONT_DIR'] = str(toolkit.font_dir)
        run('ctest', '--test-dir', frontend, '--output-on-failure', '--timeout', '120',
            '--tests-regex', '^' + SMOKE_TEST + '$')
        sdk_stamp.write_text(sdk_identity + '\n', encoding='utf-8')
        return
    run('cmake', '--build', frontend, '--parallel', jobs)
    os.environ['GUI_FORMS_FONT_DIR'] = str(toolkit.font_dir)
    run('ctest', '--test-dir', frontend, '--output-on-failure', '--timeout', '120', '--parallel', test_jobs)
    tested_identity: str = sdk_fingerprint(sdk)
    if tested_identity != sdk_identity:
        raise RuntimeError('GUI.Forms SDK changed during frontend build/tests; rebuild against a stable SDK')
    sdk_stamp.write_text(sdk_identity + '\n', encoding='utf-8')
    run('cmake', '--install', frontend)
    validation: BuildValidation = {
        'source_revision': revision, 'frontend_ctest': 'passed',
        'gui_forms_sdk_sha256': sdk_identity, 'gui_forms_ctest': toolkit_validation}
    validation_path: Path = build / 'build-validation.json'
    validation_text: str = json.dumps(validation, indent=2) + '\n'
    validation_path.write_text(validation_text, encoding='utf-8')
    package_options: list[str] = []
    if skip_components:
        package_options.append('--skip-components')
    run(sys.executable, ROOT / 'tools/package_native.py', '--build', build,
        '--gui-forms-sdk', sdk, *package_options)


if __name__ == '__main__':
    main()
