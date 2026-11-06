# -*- coding: utf-8 -*-
"""Offline, non-overwriting installer for Windows Maya 2022 through 2026.

Drag this file from the fully extracted package into Maya. Importing it is
read-only. install_package(sourceRoot, targetRoot) is the Maya-independent
copy core; targetRoot is a modules directory, never a package directory.
"""
import hashlib
import importlib
import inspect
import json
import os
import re
import runpy
import shutil
import stat
import sys
import tempfile


SUPPORTED_MAYA = (2022, 2023, 2024, 2025, 2026)
PACKAGE_NAME = "BraidTools"
DESCRIPTOR_NAME = "BraidTools.mod"
MANIFEST_NAME = "BraidTools.runtime.json"
MANIFEST_IDENTITY = "BraidTools.runtime.v1"
SHELF_TAG = "BraidTools/installer/v1"
RUNTIME_FILES = (
    "BraidTools_Open.py", "LICENSE", "NOTICE.txt",
    "icons/braidTools.png", "icons/braidTools_64.png", "icons/braidTools_128.png",
    "scripts/braidtools/__init__.py", "scripts/braidtools/_qt.py",
    "scripts/braidtools/api.py", "scripts/braidtools/lifecycle.py",
    "scripts/braidtools/ui.py",
    "plug-ins/2022/braidTools.mll", "plug-ins/2023/braidTools.mll",
    "plug-ins/2024/braidTools.mll", "plug-ins/2025/braidTools.mll",
    "plug-ins/2026/braidTools.mll",
)
SOURCE_FILES = (DESCRIPTOR_NAME,) + RUNTIME_FILES
_CACHE_PATTERN = re.compile(
    r"^scripts/braidtools/__pycache__/(?:__init__|_qt|api|lifecycle|ui)"
    r"\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc$")


class InstallError(RuntimeError):
    """A safe preflight refusal or an installation failure."""


def _absolute(path):
    path = os.fspath(path)
    if not path or not isinstance(path, str):
        raise InstallError("安装路径必须是非空 Unicode 路径。")
    return os.path.abspath(path)


def _canonical(path):
    return os.path.normcase(os.path.realpath(_absolute(path)))


def _within(path, parent):
    try:
        return os.path.commonpath([_canonical(path), _canonical(parent)]) == _canonical(parent)
    except ValueError:
        return False


def _is_link(path):
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _reject_link_ancestors(path):
    # Python 3.7 on Windows does not resolve junctions as newer realpath does.
    # Reject redirected ancestors rather than misidentify an overlap/rollback.
    current = _absolute(path)
    while True:
        if os.path.lexists(current) and _is_link(current):
            raise InstallError("路径包含链接或目录联接，请使用普通文件夹：%s" % current)
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


def _regular_file(root, relative):
    """Every relative name comes from the fixed whitelist, never the manifest."""
    current = root
    if not os.path.isdir(root) or _is_link(root):
        raise InstallError("目录不存在或是链接，请使用普通文件夹：%s" % root)
    for component in relative.split("/"):
        current = os.path.join(current, component)
        if not os.path.lexists(current) or _is_link(current):
            raise InstallError("安装包缺少文件或含链接：%s" % current)
    if not os.path.isfile(current):
        raise InstallError("需要普通文件：%s" % current)
    return current


def _digest(path):
    result = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            result.update(chunk)
            size += len(chunk)
    return {"sha256": result.hexdigest(), "size": size}


def runtime_manifest(sourceRoot):
    """Read-only packaging helper; the installer never regenerates checksums."""
    source = _absolute(sourceRoot)
    return {
        "schema": 1, "identity": MANIFEST_IDENTITY, "package": PACKAGE_NAME,
        "version": "1.0.3", "platform": "win64", "maya_versions": list(SUPPORTED_MAYA),
        "files": {name: _digest(_regular_file(source, name)) for name in SOURCE_FILES},
    }


def validate_source(sourceRoot):
    """Check the entire five-version runtime before creating target files."""
    source = _absolute(sourceRoot)
    manifest_path = _regular_file(source, MANIFEST_NAME)
    with open(manifest_path, "rb") as stream:
        manifest_bytes = stream.read()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise InstallError("运行文件清单无法读取，请重新完整解压安装包。") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("identity") != MANIFEST_IDENTITY
            or manifest.get("package") != PACKAGE_NAME or manifest.get("platform") != "win64"
            or manifest.get("maya_versions") != list(SUPPORTED_MAYA)
            or not isinstance(manifest.get("files"), dict)
            or set(manifest["files"]) != set(SOURCE_FILES)):
        raise InstallError("运行文件清单与此安装器不匹配，请使用同一个完整安装包。")
    for relative in SOURCE_FILES:
        expected = manifest["files"][relative]
        if (not isinstance(expected, dict) or set(expected) != {"sha256", "size"}
                or not re.match(r"^[0-9a-f]{64}$", str(expected.get("sha256", "")))
                or not isinstance(expected.get("size"), int) or isinstance(expected["size"], bool) or expected["size"] <= 0):
            raise InstallError("运行文件清单中的记录无效：%s" % relative)
        if _digest(_regular_file(source, relative)) != expected:
            raise InstallError("文件缺损或与运行清单不一致，请重新完整解压：%s" % relative)
    return manifest, manifest_bytes


def _payload_matches(package_root, manifest, manifest_bytes):
    expected_files = set(RUNTIME_FILES) | {MANIFEST_NAME}
    if not os.path.isdir(package_root) or _is_link(package_root):
        return False
    found = set()
    for directory, folders, files in os.walk(package_root, followlinks=False):
        for name in folders + files:
            if _is_link(os.path.join(directory, name)):
                return False
        for name in files:
            relative = os.path.relpath(os.path.join(directory, name), package_root).replace("\\", "/")
            if not _CACHE_PATTERN.match(relative):
                found.add(relative)
    if found != expected_files:
        return False
    with open(_regular_file(package_root, MANIFEST_NAME), "rb") as stream:
        if stream.read() != manifest_bytes:
            return False
    return all(_digest(_regular_file(package_root, relative)) == manifest["files"][relative]
               for relative in RUNTIME_FILES)


def _rename_new(source, destination):
    # On Windows os.rename fails when destination exists: it never overwrites.
    # The pure filesystem test core intentionally has the same Windows scope.
    if os.name != "nt":
        raise InstallError("此安装核心仅支持 Windows。")
    if os.path.lexists(destination):
        raise InstallError("安装目标在复制期间出现，已停止且不会覆盖：%s" % destination)
    os.rename(source, destination)


def _remove_stage(stage, target_root):
    if not stage or not os.path.exists(stage):
        return
    if (os.path.dirname(_canonical(stage)) != _canonical(target_root)
            or not os.path.basename(stage).startswith(".BraidTools-install-") or _is_link(stage)):
        raise InstallError("临时目录边界检查失败，未删除：%s" % stage)
    shutil.rmtree(stage)


def install_package(sourceRoot, targetRoot):
    """Install runtime + descriptor, or return unchanged; never overwrite.

    Source is the extracted package root. Target is a caller-provided modules
    directory, so tests can use disposable profiles without importing Maya.
    """
    source, target = _absolute(sourceRoot), _absolute(targetRoot)
    _reject_link_ancestors(source)
    _reject_link_ancestors(target)
    package = os.path.join(target, PACKAGE_NAME)
    descriptor = os.path.join(target, DESCRIPTOR_NAME)
    if _within(target, source) or _within(source, target):
        raise InstallError("源包与安装目标重合或相互包含；请从独立解压目录运行安装器。")
    manifest, manifest_bytes = validate_source(source)
    if os.path.lexists(target) and (not os.path.isdir(target) or _is_link(target)):
        raise InstallError("modules 目标不是普通文件夹：%s" % target)
    result = {"status": "installed", "target_root": target, "package_root": package,
              "descriptor": descriptor}
    if os.path.lexists(package) or os.path.lexists(descriptor):
        if (os.path.isdir(package) and os.path.isfile(descriptor) and not _is_link(descriptor)
                and _payload_matches(package, manifest, manifest_bytes)
                and _digest(descriptor) == manifest["files"][DESCRIPTOR_NAME]):
            result["status"] = "unchanged"
            return result
        raise InstallError("目标已有不同、不完整或非本安装器管理的同名内容，未覆盖。\n%s\n%s\n"
                           "本安装器不执行升级；请保留旧包，并先由您处理同名安装。" % (package, descriptor))

    created_target = not os.path.exists(target)
    stage = None
    published_package = False
    try:
        os.makedirs(target, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".BraidTools-install-", dir=target)
        staged_package = os.path.join(stage, PACKAGE_NAME)
        os.mkdir(staged_package)
        for relative in RUNTIME_FILES:
            destination = os.path.join(staged_package, *relative.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(_regular_file(source, relative), destination)
        with open(os.path.join(staged_package, MANIFEST_NAME), "wb") as stream:
            stream.write(manifest_bytes)
        staged_descriptor = os.path.join(stage, "descriptor.pending")
        shutil.copyfile(_regular_file(source, DESCRIPTOR_NAME), staged_descriptor)
        if (not _payload_matches(staged_package, manifest, manifest_bytes)
                or _digest(staged_descriptor) != manifest["files"][DESCRIPTOR_NAME]):
            raise InstallError("临时副本校验失败，安装未发布。")
        _rename_new(staged_package, package)
        published_package = True
        _rename_new(staged_descriptor, descriptor)  # Publish the discoverable module last.
    except Exception as exc:
        if published_package:
            if (_canonical(os.path.dirname(package)) == _canonical(target)
                    and _payload_matches(package, manifest, manifest_bytes)):
                shutil.rmtree(package)
            else:
                raise InstallError("安装未完成；本次目录随后发生变化，已保留以避免误删：%s" % package) from exc
        if isinstance(exc, InstallError):
            raise
        raise InstallError("安装未完成，已回滚本次已发布的文件；请检查目录写入权限和剩余空间。\n%s" % exc) from exc
    finally:
        _remove_stage(stage, target)
        if created_target and os.path.isdir(target):
            try:
                os.rmdir(target)  # Only removes an empty directory created by this call.
            except OSError:
                pass
    return result


def _session_conflicts(cmds, package_root, major):
    conflicts = []
    expected_python = os.path.join(package_root, "scripts", "braidtools")
    expected_plugin = os.path.join(package_root, "plug-ins", str(major), "braidTools.mll")
    for name, module in list(sys.modules.items()):
        if name == "braidtools" or name.startswith("braidtools."):
            location = getattr(module, "__file__", None)
            paths = list(getattr(module, "__path__", []))
            if not location or not _within(location, expected_python) or any(not _within(path, expected_python) for path in paths):
                conflicts.append("当前已导入其他位置的脚本：%s" % (location or name))
                break
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        path = cmds.pluginInfo(name, query=True, path=True)
        types = cmds.pluginInfo(name, query=True, dependNode=True) or []
        if "braidToolsGenerator" in types or os.path.splitext(os.path.basename(path))[0].lower() == "braidtools":
            if (_canonical(path) != _canonical(expected_plugin)
                    or int(cmds.pluginInfo(name, query=True, apiVersion=True)) // 10000 != major):
                conflicts.append("当前已加载其他位置或年份的插件：%s" % path)
    if PACKAGE_NAME in (cmds.moduleInfo(listModules=True) or []):
        path = cmds.moduleInfo(moduleName=PACKAGE_NAME, path=True)
        if _canonical(path) != _canonical(package_root):
            conflicts.append("当前已发现其他模块目录：%s" % path)
    return conflicts


def _ensure_shelf(cmds, package_root):
    """Add only our own dedicated shelf; never overwrite an existing shelf file."""
    from maya import mel
    shelf_name = "BraidTools"
    launcher = os.path.join(package_root, "BraidTools_Open.py")
    command = "import runpy\nrunpy.run_path(%r)['onMayaDroppedPythonFile']()" % launcher
    if cmds.shelfLayout(shelf_name, exists=True):
        children = cmds.shelfLayout(shelf_name, query=True, childArray=True) or []
        for child in children:
            if (cmds.shelfButton(child, exists=True)
                    and cmds.shelfButton(child, query=True, docTag=True) == SHELF_TAG
                    and cmds.shelfButton(child, query=True, command=True) == command):
                return "existing"
        raise InstallError("已有同名 BraidTools 工具架，未覆盖；可拖入已安装目录的 BraidTools_Open.py 打开。")
    shelf_dirs = cmds.internalVar(userShelfDir=True).split(os.pathsep)
    default_shelves = os.path.join(cmds.internalVar(userPrefDir=True), "shelves")
    for directory in shelf_dirs + [default_shelves]:
        if directory and os.path.lexists(os.path.join(directory, "shelf_BraidTools.mel")):
            raise InstallError("已有同名 shelf_BraidTools.mel，未覆盖；可拖入已安装目录的 BraidTools_Open.py 打开。")
    shelf = mel.eval('addNewShelfTab "BraidTools";')
    cmds.shelfLayout(shelf, edit=True, docTag=SHELF_TAG)
    icons = os.path.join(_absolute(package_root), "icons")
    cmds.shelfButton(parent=shelf, label="BraidTools", annotation="打开已安装的 BraidTools 麻花辫工具",
                     image1=os.path.join(icons, "braidTools.png"),
                     image2=os.path.join(icons, "braidTools_64.png"),
                     image3=os.path.join(icons, "braidTools_128.png"),
                     imageOverlayLabel="", docTag=SHELF_TAG,
                     sourceType="python", command=command)
    os.makedirs(default_shelves, exist_ok=True)
    if not cmds.saveShelf(shelf, os.path.join(default_shelves, "shelf_BraidTools")):
        raise InstallError("工具架按钮已创建，但保存失败；重启后可拖入已安装目录的 BraidTools_Open.py 打开。")
    return "created"


def _activate_installed(result, major):
    """Run after the drop callback returns, avoiding Maya's sys.path cleanup."""
    from maya import cmds
    package = result["package_root"]
    try:
        conflicts = _session_conflicts(cmds, package, major)
        if conflicts:
            message = ("磁盘安装已完成，当前会话继续使用原包。请保存工作并重启 Maya，"
                       "然后再拖入安装器一次，即可打开并创建工具架按钮。\n\n" + "\n".join(conflicts))
            cmds.confirmDialog(title="BraidTools 已安装 · 请重启", message=message, button=["知道了"])
            return
        # Dynamic loadModule can resolve a relative module root against Maya's
        # installation modules directory. Use the verified absolute runtime
        # paths for this session; normal startup discovers the .mod correctly.
        if PACKAGE_NAME in (cmds.moduleInfo(listModules=True) or []):
            actual = cmds.moduleInfo(moduleName=PACKAGE_NAME, path=True)
            if _canonical(actual) != _canonical(package):
                raise InstallError("Maya 当前选择了其他 BraidTools 模块；请保存工作并重启。")
        scripts = os.path.join(package, "scripts")
        sys.path[:] = [path for path in sys.path if _canonical(path or os.curdir) != _canonical(scripts)]
        sys.path.insert(0, scripts)
        importlib.invalidate_caches()
        braidtools = importlib.import_module("braidtools")
        if not _within(braidtools.__file__, os.path.join(scripts, "braidtools")):
            raise InstallError("脚本来源与安装目录不一致；请保存工作并重启 Maya。")
        braidtools.show()
        shortcut_note = "以后点击 BraidTools 工具架中的按钮打开。"
        try:
            _ensure_shelf(cmds, package)
        except Exception as exc:
            shortcut_note = "工具已打开，但快捷按钮未创建：%s" % exc
        title = "BraidTools 安装完成" if result["status"] == "installed" else "BraidTools 已是同一版本"
        cmds.confirmDialog(title=title, message="%s\n\n安装目录：\n%s" % (shortcut_note, package), button=["完成"])
    except Exception as exc:
        cmds.confirmDialog(title="BraidTools 已安装 · 打开未完成",
                           message="运行文件已完整安装，请保存工作并重启 Maya 后重试。\n%s\n\n安装目录：%s" % (exc, package),
                           button=["知道了"])


def install_in_maya(sourceRoot=None):
    """Interactive entry: the user approves the exact disk and shelf changes."""
    from maya import cmds, utils
    if sys.platform != "win32" or sys.version_info < (3, 7):
        raise InstallError("需要 Windows Maya 的 Python 3 模式；支持 Maya 2022 至 2026。")
    major = int(cmds.about(apiVersion=True)) // 10000
    if major not in SUPPORTED_MAYA:
        raise InstallError("此安装包只支持 Windows Maya 2022 至 2026；当前为 Maya %s。" % major)
    source = _absolute(sourceRoot or os.path.dirname(__file__))
    target = os.path.join(cmds.internalVar(userAppDir=True), "modules")
    validate_source(source)
    message = ("将运行文件复制到：\n%s\n\n安装后打开工具，并新增专属 BraidTools 工具架按钮。"
               "同名不同内容会停止安装。需要重启时会明确提示。" % os.path.join(target, PACKAGE_NAME))
    if major in (2023, 2024):
        message += "\n\nMaya %s 为兼容预览，尚未完成对应 Maya 的运行验证。" % major
    message += "\n\n是否安装？"
    choice = cmds.confirmDialog(title="安装 BraidTools", message=message, button=["安装", "取消"],
                                defaultButton="安装", cancelButton="取消", dismissString="取消")
    if choice != "安装":
        return None
    result = install_package(source, target)
    utils.executeDeferred(lambda: _activate_installed(result, major))
    return result


def _drop_file():
    """Read the actual file from Maya's documented drag/drop dispatcher.

    Maya caches imports by filename and does not always prepend the drop
    directory to sys.path. Neither __file__ nor sys.path[0] identifies a later
    dropped copy reliably. Do not guess when the dispatcher is unavailable.
    """
    frame = inspect.currentframe()
    try:
        for unused in range(8):
            if frame is None:
                break
            if (frame.f_globals.get("__name__") == "maya.app.general.executeDroppedPythonFile"
                    and frame.f_code.co_name == "executeDroppedPythonFile"):
                value = frame.f_locals.get("droppedFile")
                if not isinstance(value, str) or os.path.basename(value) != "BraidTools_Install.py":
                    raise InstallError("无法确认拖入的安装文件，请从完整解压包重新拖入安装器。")
                return _regular_file(os.path.dirname(_absolute(value)), "BraidTools_Install.py")
            frame = frame.f_back
    finally:
        del frame
    if __name__ == "__main__":
        return _absolute(__file__)
    raise InstallError("无法确认安装器来源，请将完整包中的 BraidTools_Install.py 直接拖入 Maya 视口。")


def onMayaDroppedPythonFile(*unused):
    """Maya passes the object under the mouse, not the dropped file path."""
    try:
        actual_file = _drop_file()
        source = os.path.dirname(actual_file)
        if _canonical(actual_file) != _canonical(__file__):
            namespace = runpy.run_path(actual_file)
            return namespace["install_in_maya"](source)
        return install_in_maya(source)
    except Exception as exc:
        # Autodesk's drop helper pops sys.path only after a successful return.
        # Report here and return normally so its temporary path is cleaned up.
        from maya import cmds
        cmds.confirmDialog(title="BraidTools 安装未完成", message=str(exc), button=["关闭"])
        return False


if __name__ == "__main__":
    onMayaDroppedPythonFile()
