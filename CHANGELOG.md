# 变更记录 / Changelog

## Unreleased — 1.0.0 开发预览

源码采用 MIT 许可证；目前尚无公开二进制 Release。

### 功能

- 三股编织生成节点、中文可停靠面板、多曲线创建和批量参数编辑。
- 固定节距／固定周期、尺寸与旋转 Ramp、朝向、相位及精度控制。
- 曲线重建与重新绑定、独立源导向提取、根部圆盘和网格烘焙。
- 场景保存重开、源对象身份关联及撤销支持。

### 多版本与安装准备

- 二进制按 `plug-ins/<Maya年份>/braidTools.mll` 分开，CMake 检查 SDK 年份。
- Python 面板适配 Maya 自带的 Qt5/PySide2 与 Qt6/PySide6。
- 启动入口按 Maya 年份选择插件，并检查已加载脚本／插件的路径一致性。
- 准备用户目录模块安装描述；`.mod` 复制到 `modules` 顶层，与 `BraidTools` 文件夹并排。
- 增加公开构建的注册节点 ID 确认要求；默认开发 ID 禁止用于公开构建。
- 整理中英文 README、中文使用说明和发布边界；源仓库忽略编译产物、缓存与原始测试输出。

### 当前验证

- Maya 2022、2025、2026 的本地构建通过，每版 18/18 项隔离 `mayapy` 功能验收通过。
- Maya 2022 仅覆盖 Python 3；2023、2024 未验证且没有二进制。
- 三版的模块自动发现、按版本加载、完整面板构建、参数修改撤销、离屏 Ramp 绘制与鼠标编辑、对话框调用检查均通过；当前多版本包的真实 GUI 拖动、视口与长时间运行尚未逐版复验。
- 三版均使用 Visual Studio 2022 / MSVC 19.44 编译；Maya 2022 的官方基线为 Visual Studio 2019.7，实际测试结果不等于官方工具链认证。

### 公开二进制发布前待完成

- 获得项目专用的注册节点 ID，重建公开二进制并验证场景兼容性。
- 汇总公开版本的验证记录，生成安装 ZIP 与 SHA256 校验文件。

### English summary

Unreleased preparation for a Windows Maya three-strand braid plugin. Includes version-specific native builds, Qt5/Qt6 panel support, module installation instructions, and public-release node ID checks. Maya 2022, 2025 and 2026 each passed all 18 isolated functional tests. Maya 2023/2024 and cross-version real GUI interaction remain unverified. Source uses the MIT License, Copyright (c) 2026 Hsws9999. A registered node ID and public binary release remain pending.
