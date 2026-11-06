# BraidTools

[English](README.en.md) · [使用说明](docs/使用说明.md) · [变更记录](CHANGELOG.md) · [验证记录](docs/验证记录.md)

BraidTools 是面向 Windows Maya 的三股麻花辫建模插件。它沿开放 NURBS 曲线生成可编辑的编织模型，提供中文可停靠面板，并能提取独立的 XGen 源导向曲线和烘焙网格。

**当前状态：开发预览，暂未提供公开二进制 Release。** 仓库提供源码、构建脚本和验证记录。每个 Maya 版本需单独编译；当前默认节点 ID 用于本地开发，取得本项目的注册 ID 并重新验证后才能发布安装包。

## 兼容性与验证范围

仅面向 Windows x64。每个 Maya 主版本都需要对应的 `plug-ins/<年份>/braidTools.mll`，不能通过复制或改名跨版本使用。

| Maya | Python / Qt 实测环境 | 编译 | 当前源码的隔离功能验收 |
| --- | --- | --- | --- |
| 2022 | Python 3.7.7 / Qt 5.15.2 | 通过 | 18/18 通过；仅支持 Python 3 模式 |
| 2023 | 未验证 | 无产物 | 未验证 |
| 2024 | 未验证 | 无产物 | 未验证 |
| 2025 | Python 3.11.4 / Qt 6.5.3 | 通过 | 18/18 通过 |
| 2026 | Python 3.11.9 / Qt 6.5.3 | 通过 | 18/18 通过 |

上述功能验收在每个版本独立的 `mayapy` 进程与测试设置目录中运行。三版还通过了模块自动发现、按版本加载、完整面板构建、参数修改与撤销、离屏 Ramp 绘制及鼠标编辑、对话框调用的补充检查。当前多版本包尚未完成各版本真实 GUI 拖动、视口及长时间运行复验；离屏检查不等于真实视口验收。

界面使用 Maya 自带的 PySide2 或 PySide6，无需另外安装 Qt。Maya 2022 的 Python 2 模式不受支持。2023、2024 的模块条目与构建参数只是预留，不能作为兼容性结论。

## 功能

- 一条或多条开放路径生成三股模型，编辑路径 CV 时由依赖图更新；关闭面板后关联仍保留。
- 固定节距／固定周期、整体尺寸、辫子与单股扁平度、朝向和相位。
- 尺寸分布与局部旋转 Ramp，支持多选时仅修改当前参数。
- 自动／手动纵向精度、独立截面精度、曲线重建和显式重新绑定。
- 每条辫子提取 27 条独立源导向曲线，可附带三个根部圆盘；支持烘焙独立网格。
- 创建、调参、重建、重新绑定、提取和烘焙提供撤销操作。

不支持闭合路径、其他股数或发束碰撞求解；不自动创建 XGen Description，不包含毛发材质或 UE Groom 导出。输出源导向仍需在 XGen 中继续设置。

<img src="docs/images/panel.png" alt="BraidTools 中文参数面板" width="420">

截图展示面板布局，不代表所有 Maya 版本的界面均已通过复验。

## 长期安装到用户目录

以下为通用手动安装说明。执行后，该用户的 Maya 会在启动时发现 BraidTools 的脚本与匹配版本插件；无需修改 `Maya.env` 或 `userSetup.py`。面板仍由用户主动打开。

`C:/Program Files/Autodesk/Maya2026/modules` 也是 Maya 2026 的有效模块目录。本说明推荐用户的 `Documents/maya/modules`，方便多个 Maya 年份共用安装，并避免向程序安装目录写入文件；不是说程序目录不能安装。

在通常的 Windows 文档目录中，安装结构为：

```text
Documents/maya/modules/
├── BraidTools.mod
└── BraidTools/
    ├── BraidTools_Open.py
    ├── scripts/braidtools/
    └── plug-ins/
        ├── 2022/braidTools.mll
        ├── 2025/braidTools.mll
        └── 2026/braidTools.mll
```

将完整包放入 `BraidTools` 文件夹，再把包内 `BraidTools.mod` **复制到上一级 `modules` 目录**，与 `BraidTools` 文件夹并排。包内 `.mod` 是待复制的安装描述，模块根路径为 `./BraidTools`；仅把它留在子文件夹内不构成完整安装。安装目录若已存在，应先关闭相关 Maya 并保留旧版本备份，再替换完整包。

如果 Windows 将“文档”重定向到其他位置，或 Maya 使用自定义 `MAYA_APP_DIR`，以实际 Maya 用户目录为准。源码仓库不跟踪 `.mll`，从源码取得文件后需要先编译所需版本。

重新启动 Maya，在 Script Editor 的 **Python** 页签运行：

```python
import braidtools
braidtools.show()
```

这会加载匹配版本的插件并打开面板。可编辑场景需要对应插件；重新启动后，先打开工具，再打开含生成节点的场景。想临时试用时，可直接把完整包中的 `BraidTools_Open.py` 拖入 Maya 视口，该方式只为当前会话添加路径。

更新、卸载与详细操作见[使用说明](docs/使用说明.md)。模块路径规则见 [Autodesk 模块文档](https://help.autodesk.com/cloudhelp/2026/CHS/Maya-DEVHELP/files/Distributing-Maya-Plug-ins/DistributingUsingModules/Maya_DEVHELP_Distributing_Maya_Plug_ins_DistributingUsingModules_ModuleDescriptionFiles_html.html)。

## 从源码编译

项目声明的 CMake 最低版本为 3.21；实际构建还应遵循目标 Maya 的官方工具链要求（Maya 2025/2026 为 CMake 3.27.3 或更新版本）。另外需要支持 C++17 的 Microsoft C++ 工具链，以及目标 Maya 版本的开发头文件和库。使用该 Maya 版本的 Autodesk 开发环境要求选择工具链。SDK 不随本项目提供。

以下 PowerShell 命令在项目根目录生成 Maya 2026 的本地开发构建，输出为 `plug-ins/2026/braidTools.mll`：

```powershell
cmake -S . -B build/2026 -A x64 -DMAYA_VERSION=2026 -DMAYA_LOCATION="C:/Program Files/Autodesk/Maya2026"
cmake --build build/2026 --config Release
```

构建其他年份时，同时更换 `MAYA_VERSION`、`MAYA_LOCATION` 和构建目录；CMake 会检查年份与 SDK 是否一致。不要复用不同年份的构建缓存，也不要覆盖正在被 Maya 加载的 `.mll`。

本次三个版本的本地构建均使用 Visual Studio 2022 / MSVC 19.44。Maya 2022 的官方工具链基线为 Visual Studio 2019.7；这里记录的是实际编译及运行结果，不代表 Autodesk 对该工具链组合的认证。[Autodesk Maya 2022 devkit 更新](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/What-s-New-What-s-Changed-/2022-Whats-New-in-API.html)

`tests/integration_maya.py` 提供隔离验收。使用目标版本的 `mayapy`，为每次运行设置独立的 `--output` 和位于其中的 `--profile`，同时设置 `BRAIDTOOLS_ISOLATED_TEST=1` 和匹配的 `MAYA_APP_DIR`。`tests/module_qt_smoke.py` 补充验证模块发现和离屏界面行为。不要在已有交互 Maya 会话中执行这些测试。原始报告可能包含本机路径，公开前需要清理。

## 公开发布条件

当前节点 `braidToolsGenerator` 默认使用开发 ID `0x0007B7A1`。公开分发前需要取得属于本项目的 Autodesk 注册节点 ID；不能随意选择另一数值冒充已注册 ID。详见 [NOTICE.txt](NOTICE.txt) 和 [Autodesk 节点 ID 说明](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/Maya-Python-API/Dependency-Graph-Plug-in-Basics/Dependency-Graph-Plug-ins.html)。

公开构建同时要求设置：

| CMake 参数 | 要求 |
| --- | --- |
| `BRAIDTOOLS_PUBLIC_RELEASE` | `ON` |
| `BRAIDTOOLS_NODE_ID` | 本项目实际获分配的节点 ID |
| `BRAIDTOOLS_REGISTERED_NODE_ID_CONFIRMED` | 核实注册归属后设为 `ON` |

确认开关仅记录维护者的确认，构建系统不会联网验证注册。变更 ID 后需重新编译各版本并验证场景保存、重开与迁移；不要直接替换仍在使用开发 ID 场景的安装。

源码与文档位于本仓库。取得注册节点 ID 并完成发布验证后，计划通过 Releases 提供安装 ZIP 与 SHA256 校验文件。当前没有公开二进制下载。

## 许可证

本项目源码采用 [MIT 许可证](LICENSE)，版权署名为 2026 Hsws9999。允许使用、修改和商业再分发，需保留版权和许可声明。

Autodesk Maya、其 SDK 及随 Maya 提供的依赖仍适用各自条款。使用本工具需要用户自己的 Maya 安装及相应许可。
