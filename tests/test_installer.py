# -*- coding: utf-8 -*-
"""Filesystem and mocked-confirmation tests for the BraidTools Windows installer.

Run with ordinary system Python, never mayapy:
    python work/BraidTools-AllMaya/tests/test_installer.py -v

All installs use disposable directories beside this package under work/.
No Maya session, user profile, registry, shelf, or real modules folder is used.
The fixtures copy the package's real five-version binaries without loading them.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = PACKAGE_ROOT.parent.resolve()
INSTALLER_PATH = PACKAGE_ROOT / 'BraidTools_Install.py'
MANIFEST_NAME = 'BraidTools.runtime.json'
DESCRIPTOR_NAME = 'BraidTools.mod'
EXPECTED_SOURCE_FILES = (
    'BraidTools.mod', 'BraidTools_Open.py', 'LICENSE', 'NOTICE.txt',
    'icons/braidTools.png', 'icons/braidTools_64.png', 'icons/braidTools_128.png',
    'scripts/braidtools/__init__.py', 'scripts/braidtools/_qt.py',
    'scripts/braidtools/api.py', 'scripts/braidtools/lifecycle.py',
    'scripts/braidtools/ui.py',
    'plug-ins/2022/braidTools.mll', 'plug-ins/2023/braidTools.mll',
    'plug-ins/2024/braidTools.mll', 'plug-ins/2025/braidTools.mll',
    'plug-ins/2026/braidTools.mll',
)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_state(root, metadata=False):
    """Observe paths and bytes, including empty directories; ignore access times."""
    root = Path(root)
    if not root.exists():
        return None
    result = {}
    for path in [root] + sorted(root.rglob('*')):
        relative = '.' if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            entry = ('link', os.readlink(str(path)))
        elif path.is_dir():
            entry = ('directory',)
        else:
            entry = ('file', path.stat().st_size, file_hash(path))
        if metadata:
            entry += (path.lstat().st_mtime_ns,)
        result[relative] = entry
    return result


def write_manifest(source, value):
    (Path(source) / MANIFEST_NAME).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@unittest.skipUnless(os.name == 'nt', 'The delivered installer targets Windows only.')
class InstallerAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not INSTALLER_PATH.is_file():
            raise RuntimeError('Installer is missing: %s' % INSTALLER_PATH)
        before_maya = {name for name in sys.modules if name == 'maya' or name.startswith('maya.')}
        previous_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location('braidtools_installer_under_test', str(INSTALLER_PATH))
            cls.installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.installer)
        finally:
            sys.dont_write_bytecode = previous_bytecode
        after_maya = {name for name in sys.modules if name == 'maya' or name.startswith('maya.')}
        if after_maya != before_maya:
            raise AssertionError('Importing the filesystem installer imported Maya modules.')
        print('INSTALLER_SHA256 ' + file_hash(INSTALLER_PATH), flush=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='BraidTools-installer-test-', dir=str(WORK_ROOT))
        self.root = Path(self.temporary.name).resolve()
        # Cleanup is limited to this newly allocated child of work/.
        self.assertEqual(self.root.parent, WORK_ROOT)
        self.assertTrue(self.root.name.startswith('BraidTools-installer-test-'))
        self.source = self.make_source(self.root / 'source')
        self.target = self.root / 'modules'

    def tearDown(self):
        resolved = Path(self.temporary.name).resolve()
        if resolved.parent != WORK_ROOT or not resolved.name.startswith('BraidTools-installer-test-'):
            raise RuntimeError('Refusing cleanup outside the disposable test directory.')
        self.temporary.cleanup()

    def make_source(self, destination):
        """Copy real runtime files and prepare an independently hashed manifest.

        Regenerating this fixture's manifest avoids races with documentation
        edits in the packaging directory. No installer validation/copy helper
        is used to construct expected results.
        """
        destination = Path(destination)
        destination.mkdir(parents=True)
        records = {}
        for relative in EXPECTED_SOURCE_FILES:
            original = PACKAGE_ROOT / relative
            self.assertTrue(original.is_file(), 'Package fixture file is missing: ' + str(original))
            copied = destination / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(original), str(copied))
            data = copied.read_bytes()
            records[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
        write_manifest(destination, {
            'schema': 1, 'identity': 'BraidTools.runtime.v1', 'package': 'BraidTools',
            'version': '1.0.3', 'platform': 'win64', 'maya_versions': [2022, 2023, 2024, 2025, 2026],
            'files': records,
        })
        # Extracted packages can contain user notes and development files;
        # these must never be swept into the installed runtime.
        (destination / 'private-note.txt').write_text('fixture only; do not install', encoding='utf-8')
        return destination

    def install(self, source=None, target=None):
        return self.installer.install_package(str(source or self.source), str(target or self.target))

    def assert_complete_install(self, source, target, result):
        source, target = Path(source), Path(target)
        package = target / 'BraidTools'
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(Path(result['package_root']).resolve(), package.resolve())
        self.assertEqual(Path(result['target_root']).resolve(), target.resolve())
        self.assertEqual(Path(result['descriptor']).resolve(), (target / DESCRIPTOR_NAME).resolve())
        self.assertEqual((target / DESCRIPTOR_NAME).read_bytes(), (source / DESCRIPTOR_NAME).read_bytes())
        expected = set(EXPECTED_SOURCE_FILES) - {DESCRIPTOR_NAME}
        expected.add(MANIFEST_NAME)
        actual = {path.relative_to(package).as_posix() for path in package.rglob('*') if path.is_file()}
        self.assertEqual(actual, expected)
        for relative in expected:
            self.assertEqual(file_hash(package / relative), file_hash(source / relative), relative)
        self.assertFalse(any(path.name.startswith('.BraidTools-install-') for path in target.iterdir()))

    def assert_rejected_without_change(self, source, target, observed_root=None):
        observed_root = Path(observed_root or self.root)
        before = tree_state(observed_root)
        with self.assertRaises(self.installer.InstallError):
            self.install(source, target)
        self.assertEqual(tree_state(observed_root), before, 'Refusal changed files or left partial directories.')

    def test_new_install_contains_exact_runtime_and_real_binaries(self):
        source_before = tree_state(self.source)
        result = self.install()
        self.assert_complete_install(self.source, self.target, result)
        self.assertEqual(tree_state(self.source), source_before)
        for year in (2022, 2023, 2024, 2025, 2026):
            binary = self.target / 'BraidTools' / 'plug-ins' / str(year) / 'braidTools.mll'
            self.assertEqual(binary.read_bytes()[:2], b'MZ')
            self.assertLess(binary.stat().st_size, 300 * 1024)

    def test_supported_versions_reach_confirmation_with_preview_notice_only_where_needed(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            with self.subTest(year=year):
                cmds = mock.Mock()
                cmds.about.return_value = year * 10000
                cmds.internalVar.return_value = str(self.root / 'disposable-user-app')
                cmds.confirmDialog.return_value = '取消'
                utils = mock.Mock()
                maya_stub = mock.Mock(cmds=cmds, utils=utils)
                with mock.patch.dict(sys.modules, {'maya': maya_stub}):
                    self.assertIsNone(self.installer.install_in_maya(str(self.source)))
                cmds.confirmDialog.assert_called_once()
                message = cmds.confirmDialog.call_args[1]['message']
                if year in (2023, 2024):
                    self.assertIn('Maya %s 为兼容预览' % year, message)
                    self.assertIn('尚未完成对应 Maya 的运行验证', message)
                else:
                    self.assertNotIn('兼容预览', message)
                    self.assertNotIn('尚未完成', message)
                utils.executeDeferred.assert_not_called()
                self.assertFalse((self.root / 'disposable-user-app').exists())

    def test_identical_reinstall_is_noop_including_modification_times(self):
        self.install()
        before = tree_state(self.target, metadata=True)
        result = self.install()
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(tree_state(self.target, metadata=True), before)

    def test_identical_reinstall_preserves_generated_python_cache(self):
        self.install()
        cache = self.target / 'BraidTools' / 'scripts' / 'braidtools' / '__pycache__' / 'api.cpython-311.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'test-generated cache; preserve byte-for-byte')
        before = tree_state(self.target, metadata=True)
        result = self.install()
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(tree_state(self.target, metadata=True), before)

    def test_changed_existing_runtime_is_never_overwritten(self):
        self.install()
        changed = self.target / 'BraidTools' / 'scripts' / 'braidtools' / 'api.py'
        changed.write_bytes(changed.read_bytes() + b'\n# user-owned local change\n')
        self.assert_rejected_without_change(self.source, self.target)

    def test_existing_conflicting_descriptor_is_never_overwritten(self):
        self.target.mkdir()
        (self.target / DESCRIPTOR_NAME).write_text('+ MyLegacyTool 9.9 C:/user-owned\n', encoding='utf-8')
        self.assert_rejected_without_change(self.source, self.target)
        self.assertFalse((self.target / 'BraidTools').exists())

    def test_legacy_same_name_file_or_directory_is_preserved(self):
        for kind in ('file', 'directory'):
            with self.subTest(kind=kind):
                target = self.root / ('legacy-' + kind)
                target.mkdir()
                collision = target / 'BraidTools'
                if kind == 'file':
                    collision.write_bytes(b'user-owned file named BraidTools')
                else:
                    collision.mkdir()
                    (collision / 'keep.txt').write_bytes(b'user-owned old installation')
                self.assert_rejected_without_change(self.source, target)

    def test_extra_user_file_in_existing_package_is_not_deleted(self):
        self.install()
        (self.target / 'BraidTools' / 'my-settings.txt').write_text('private settings', encoding='utf-8')
        self.assert_rejected_without_change(self.source, self.target)

    def test_unrelated_user_modules_and_preferences_are_untouched(self):
        self.target.mkdir()
        other = self.target / 'OtherTool'
        other.mkdir()
        (other / 'keep.py').write_bytes(b'# user content\n')
        (self.target / 'OtherTool.mod').write_bytes(b'+ OtherTool 1.0 ./OtherTool\n')
        (self.target / 'Maya.env').write_bytes(b'USER_SETTING=keep\n')
        previous = {p: p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        result = self.install()
        self.assert_complete_install(self.source, self.target, result)
        self.assertEqual({p: p.read_bytes() for p in previous}, previous)

    def test_missing_package_files_fail_before_creating_target(self):
        for index, relative in enumerate((MANIFEST_NAME, 'plug-ins/2025/braidTools.mll', 'scripts/braidtools/ui.py')):
            with self.subTest(missing=relative):
                source = self.make_source(self.root / ('missing-source-%d' % index))
                (source / relative).unlink()
                target = self.root / ('new-parent-%d' % index) / 'modules'
                self.assert_rejected_without_change(source, target)
                self.assertFalse(target.parent.exists())

    def test_corrupted_binary_fails_before_creating_target(self):
        binary = self.source / 'plug-ins/2026/braidTools.mll'
        binary.write_bytes(binary.read_bytes() + b'corruption')
        self.assert_rejected_without_change(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_missing_source_directory_is_rejected_without_target(self):
        self.assert_rejected_without_change(self.root / 'does-not-exist', self.target)

    def test_invalid_manifest_json_or_top_level_type_is_safe(self):
        for index, content in enumerate(('{invalid-json', 'null', '[]')):
            with self.subTest(content=content):
                source = self.make_source(self.root / ('bad-json-%d' % index))
                (source / MANIFEST_NAME).write_text(content, encoding='utf-8')
                self.assert_rejected_without_change(source, self.root / ('bad-json-target-%d' % index))

    def test_manifest_paths_cannot_escape_or_add_unlisted_files(self):
        sentinel = self.root / 'outside-sentinel.txt'
        sentinel.write_bytes(b'outside data must remain unchanged')
        malicious_paths = ('../outside-sentinel.txt', '..\\outside-sentinel.txt',
                           sentinel.as_posix(), 'extra/unlisted.py')
        for index, relative in enumerate(malicious_paths):
            with self.subTest(relative=relative):
                source = self.make_source(self.root / ('bad-path-%d' % index))
                manifest = json.loads((source / MANIFEST_NAME).read_text(encoding='utf-8'))
                manifest['files'][relative] = {'sha256': file_hash(sentinel), 'size': sentinel.stat().st_size}
                write_manifest(source, manifest)
                self.assert_rejected_without_change(source, self.root / ('bad-path-target-%d' % index))
        self.assertEqual(sentinel.read_bytes(), b'outside data must remain unchanged')

    def test_equal_and_overlapping_source_target_paths_are_rejected(self):
        for target in (self.source, self.source / 'nested-modules', self.root):
            with self.subTest(target=str(target)):
                self.assert_rejected_without_change(self.source, target)

    def test_chinese_and_space_paths_install_correctly(self):
        source = self.make_source(self.root / '包 源文件' / '麻花辫 工具')
        target = self.root / '测试 用户文档' / 'maya 模块'
        self.assert_complete_install(source, target, self.install(source, target))

    def test_target_that_is_a_user_file_is_not_replaced(self):
        self.target.write_bytes(b'important user file at requested modules path')
        self.assert_rejected_without_change(self.source, self.target)

    def test_copy_failure_rolls_back_without_touching_user_content(self):
        self.target.mkdir()
        (self.target / 'keep.txt').write_bytes(b'keep this existing content')
        before = tree_state(self.root)
        real_copy = shutil.copyfile
        copied = [0]
        def failing_copy(source, destination, *args, **kwargs):
            copied[0] += 1
            if copied[0] == 3:
                raise OSError('injected disk-copy failure')
            return real_copy(source, destination, *args, **kwargs)
        with mock.patch.object(self.installer.shutil, 'copyfile', side_effect=failing_copy):
            with self.assertRaises(self.installer.InstallError):
                self.install()
        self.assertEqual(copied[0], 3, 'Failure injection did not reach partial staging.')
        self.assertEqual(tree_state(self.root), before)

    def test_descriptor_publish_race_preserves_new_user_file_and_rolls_back_package(self):
        self.target.mkdir()
        (self.target / 'keep.txt').write_bytes(b'pre-existing user data')
        real_publish = self.installer._rename_new
        publications = [0]
        user_descriptor = b'+ UserCreatedDuringInstall 1.0 ./MyTool\n'
        def conflicting_publish(source, destination):
            publications[0] += 1
            if publications[0] == 2:
                destination = Path(destination)
                self.assertEqual(destination.resolve(), (self.target / DESCRIPTOR_NAME).resolve())
                destination.write_bytes(user_descriptor)
            return real_publish(source, str(destination))
        with mock.patch.object(self.installer, '_rename_new', side_effect=conflicting_publish):
            with self.assertRaises(self.installer.InstallError):
                self.install()
        self.assertEqual(publications[0], 2, 'Test did not reach descriptor publication.')
        self.assertEqual((self.target / DESCRIPTOR_NAME).read_bytes(), user_descriptor)
        self.assertEqual((self.target / 'keep.txt').read_bytes(), b'pre-existing user data')
        self.assertFalse((self.target / 'BraidTools').exists())
        self.assertEqual({p.name for p in self.target.iterdir()}, {DESCRIPTOR_NAME, 'keep.txt'})


if __name__ == '__main__':
    unittest.main()
