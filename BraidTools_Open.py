"""Drag into Windows Maya to open this package's matching compiled version."""
import os
import sys


def onMayaDroppedPythonFile(*_args):
    root = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.join(root, "scripts")
    from maya import cmds
    major = int(cmds.about(apiVersion=True)) // 10000
    if sys.platform != "win32":
        raise RuntimeError("此 BraidTools 包需要 Windows Maya。")
    plugin = os.path.join(root, "plug-ins", str(major), "braidTools.mll")
    if not os.path.isfile(plugin):
        raise RuntimeError("当前包缺少 Maya %s 的插件。\n需要文件: %s\n请获取对应发布包或编译该版本源码。"
                           % (major, plugin))
    existing = sys.modules.get("braidtools")
    if existing is not None:
        expected = os.path.normcase(os.path.realpath(os.path.join(scripts, "braidtools", "__init__.py")))
        actual = os.path.normcase(os.path.realpath(getattr(existing, "__file__", "") or ""))
        if actual != expected:
            raise RuntimeError("已导入其他目录的 BraidTools 脚本。请保存工作并重启 Maya，再拖入目标安装包的入口。\n"
                               "已导入: %s\n当前包: %s" % (actual, expected))
    if scripts in sys.path:
        sys.path.remove(scripts)
    sys.path.insert(0, scripts)
    import braidtools
    return braidtools.show()


if __name__ == "__main__":
    onMayaDroppedPythonFile()
