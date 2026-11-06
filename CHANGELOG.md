# 变更记录 / Changelog

## 1.0.0 — 学习预览安装包

项目采用 MIT 许可证，Copyright (c) 2026 Hsws9999。

### 安装与分发

- 提供包含 Windows Maya 2022、2025、2026 自编 `.mll` 的完整学习预览 ZIP，使用者无需自行编译。
- 增加 `BraidTools_Install.py` 拖放安装入口及 `BraidTools.runtime.json` 校验清单；确认目标和 Shelf 影响后，使用 Maya 用户目录安装模块。首次正常安装打开面板，并添加 BraidTools 工具架上的 Braid 按钮。
- 安装无需管理员权限，不修改 `Maya.env`、`userSetup.py` 或系统环境变量。相同完整安装可重复运行；不同、不完整或非本安装器管理的目标拒绝覆盖，不自动升级或卸载。已载其他包时完成磁盘安装并提示重启后再拖入一次，不覆盖同名用户 Shelf。
- README 顶部提供完整包下载入口，并区分安装 ZIP 与 GitHub Code → Download ZIP 源码快照。
- 源码中的编译产物继续忽略；仓库允许跟踪 `downloads/BraidTools_Windows_Maya2022_2025_2026_Learning.zip`。

### 功能

- 三股编织生成节点、中文可停靠面板、多曲线创建和批量参数编辑。
- 固定节距／固定周期、尺寸与旋转 Ramp、朝向、相位及精度控制。
- 曲线重建与重新绑定、独立源导向提取、根部圆盘和网格烘焙。
- 场景保存重开、源对象身份关联及撤销支持。
- 二进制按 Maya 年份分开；Python 面板适配 Maya 自带的 Qt5/PySide2 和 Qt6/PySide6。

### 当前验证

- Maya 2022、2025、2026 各通过 18/18 项隔离 `mayapy` 功能验收。
- 三版的模块发现、按版本加载、面板构建、参数修改撤销、离屏 Ramp 绘制与鼠标编辑、对话框调用检查通过。
- 三版在含中文和空格的隔离用户目录中通过自动模块发现、实际 `.mll` 加载、27 条导向生成，以及中文场景路径保存重开。不要求全英文路径，其他任意 Unicode 组合未逐一验证。
- Maya 2022 仅覆盖 Python 3；Maya 2023、2024 没有二进制且未验证。真实多版本 GUI 拖动、视口及长时间运行尚未逐版复验。
- 三版使用 Visual Studio 2022 / MSVC 19.44 编译；Maya 2022 官方基线为 Visual Studio 2019.7，实际测试不代表官方工具链认证。

### 节点 ID

学习预览使用未注册开发 ID `0x0007B7A1`，可能与其他同 ID 插件冲突。`BRAIDTOOLS_PUBLIC_RELEASE` 仍保留为注册 ID 的正式发行构建模式，当前包使用默认开发构建。未来更换 ID 需要重新构建、验证并迁移已有场景。

### English summary

MIT-licensed learning preview with a complete Windows Maya 2022/2025/2026 installation ZIP, drag-and-drop installer and dedicated Shelf button. Each version passed 18 isolated functional tests and additional module/offscreen Qt checks. Chinese-character and space-containing paths passed actual plugin loading, 27-guide generation and scene save/reopen checks. Maya 2023/2024 and real cross-version viewport/extended-session behavior remain unverified. The package uses unregistered development node ID 0x0007B7A1; a future registered-ID version requires scene migration.
