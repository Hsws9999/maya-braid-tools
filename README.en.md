# BraidTools

[中文](README.md) · [Installation and update tutorial (Chinese)](安装与更新教程.txt) · [Chinese user guide](docs/使用说明.md) · [Changelog](CHANGELOG.md) · [Validation report (Chinese)](docs/验证记录.md)

**[Download the complete learning-preview installer: Windows Maya 2022 / 2023 / 2024 / 2025 / 2026](https://github.com/Hsws9999/maya-braid-tools/raw/refs/heads/main/downloads/BraidTools1.0.zip)**

Download the ZIP above → extract the complete folder → drag `BraidTools_Install.py` into Maya's viewport. The package includes all five version-specific `.mll` files; no compilation is needed. **The new 2023/2024 binaries are compatibility previews with compilation checks only; Maya host and GUI testing is pending.** Use this complete package: GitHub's **Code → Download ZIP** is a source snapshot, not the ready-to-install plugin package.

BraidTools generates editable three-strand braids along open NURBS curves in Windows Maya. It provides a Chinese dockable panel, standalone guide curves for subsequent XGen setup, and baked meshes.

**Status: 1.1.0 learning preview.** Three independent strand-control curves retain the live master path and parameters. Windows Maya 2022–2026 binaries are included; the filename stays `BraidTools1.0.zip`. The MIT-licensed project uses the unregistered development node ID `0x0007B7A1`.

## Compatibility and verification

Windows x64 only. Every Maya major version requires its own `plug-ins/<year>/braidTools.mll`; copying or renaming another version's binary does not provide compatibility.

| Maya | Tested Python / Qt | Build | Isolated functional test record |
| --- | --- | --- | --- |
| 2022 | Python 3.7.7 / Qt 5.15.2 | Passed | 18/18 passed; Python 3 mode only |
| 2023 | Not tested | Passed; compatibility preview | Not tested |
| 2024 | Not tested | Passed; compatibility preview | Not tested |
| 2025 | Python 3.11.4 / Qt 6.5.3 | Passed | 18/18 passed |
| 2026 | Python 3.11.9 / Qt 6.5.3 | Passed | 18/18 passed |

All five binaries were rebuilt for this version. Maya 2022, 2025 and 2026 each passed fresh isolated tests: 18 base integration, 18 strand-control, 12 deletion API, 8 deletion UI and 7 strand UI cases, plus module discovery/loading and offscreen Qt smoke checks. Maya 2023/2024 remain compile/static-only previews. Offscreen checks do not establish viewport interaction, editing feel or long-session acceptance.

Earlier validation also covered module discovery, plugin loading, 27 guide curves and scene save/reopen through Chinese and space-containing paths on those three versions. English-only paths are not required; arbitrary Unicode combinations have not all been tested.

The panel uses Maya's bundled PySide2 or PySide6; do not install a separate Qt binding for this tool. Maya 2022 Python 2 mode is unsupported. Maya 2023 and 2024 are not installed on the build machine. Their new binaries have only been compiled; loading, Python/Qt, functionality and GUI behavior remain untested. The five versions have not all passed runtime validation.

## Features and limits

- Three independent editable strand curves, per-strand/all reset, persistent CV offsets over the live master path, and matching newly extracted guides/baked meshes.

- Live generation from one or multiple open curves; geometry keeps updating with the panel closed.
- Fixed pitch or fixed cycle count, size, flatness, strand shape, orientation and phase.
- Size and local rotation ramps; batch edits affect only the selected parameter.
- Automatic or manual longitudinal resolution, separate cross-section resolution, rebuild and explicit rebind.
- 27 standalone guide curves per braid, optional root disks, and independent baked meshes.
- Confirmed deletion of one or multiple list-selected live braids, preserving source curves and independent snapshots; one-step undo and redo.
- Undo support for creation, deletion, edits, rebuild, rebind, guide extraction and baking.

Closed paths, other strand counts, collision solving, automatic XGen Descriptions, hair materials and UE Groom export are outside this version's scope. Extracted curves are source curves for later XGen conversion.

<img src="docs/images/panel.png" alt="BraidTools Chinese parameter panel" width="420">

This is an offscreen Qt preview of the 1.1.0 panel and strand-control section, not an interactive Maya viewport acceptance capture.

## Drag-and-drop installation

1. Download the complete ZIP linked above and extract all files, keeping the directory structure.
2. Open Windows Maya 2022, 2023, 2024, 2025 or 2026 and drag the extracted `BraidTools_Install.py` into the viewport. Maya 2022 must use Python 3 mode.
3. Review the target directory and Shelf changes in the installer dialog, then click Install. The same dialog includes a compatibility-preview note for 2023/2024. Handle Maya's script-permission prompts as requested. A normal first installation opens the panel and creates a **gold braid icon** on the dedicated **BraidTools** Shelf.

The geometric gold braid icon is supplied as transparent 32, 64 and 128 px PNGs for Maya to choose according to the button size. It is used only for newly created buttons; existing Shelves and buttons are preserved. Old icons and existing installations are not upgraded automatically.

The installer uses Maya's actual user directory, normally `Documents/maya`, and installs to `modules/BraidTools` with a sibling `modules/BraidTools.mod`. It also saves a dedicated Shelf button. Redirected Documents folders and custom `MAYA_APP_DIR` locations follow the path reported by Maya. No administrator access, `Maya.env` edit or `userSetup.py` edit is needed.

```text
Documents/maya/modules/
├── BraidTools.mod
└── BraidTools/
    ├── BraidTools_Open.py
    ├── icons/
    ├── scripts/braidtools/
    └── plug-ins/
        ├── 2022/braidTools.mll
        ├── 2023/braidTools.mll
        ├── 2024/braidTools.mll
        ├── 2025/braidTools.mll
        └── 2026/braidTools.mll
```

An identical, complete installation managed by this installer can be run again without copying files. Different versions, incomplete installations and unowned same-name targets are rejected; this version does not upgrade existing packages. If another copy is already loaded, disk installation completes and Maya asks you to save and restart, then drag the installer from the original extracted folder again. A same-name user Shelf is not overwritten; use the installed `BraidTools_Open.py` when prompted.

For manual installation, place the complete package in `modules/BraidTools`, copy its `.mod` into the parent `modules` directory beside the folder, then restart Maya. The module root is `./BraidTools`. `C:/Program Files/Autodesk/Maya2026/modules` is also a valid location for Maya 2026; the installer uses the user directory for a shared multi-version installation.

Besides the Shelf button, the panel can be opened from the Script Editor's **Python** tab:

```python
import braidtools
braidtools.show()
```

Open the tool before loading scenes that contain its editable generator nodes. `BraidTools_Open.py` is a session-only launcher from an extracted package; use `BraidTools_Install.py` for persistent installation.

Select open curves, click “从所选曲线创建” (create from selected curves), then adjust the parameters. Move the source curve to move the braid. “提取 XGen 导向” extracts guide snapshots; “烘焙独立网格” creates independent mesh copies.

For an update, close Maya and move the old package and module descriptor to a backup outside the module search directories before installing the new complete package. To disable it, close Maya, move both outside the module search directories and remove its Shelf button if desired. Keep a compatible plugin or baked copies for scenes that still depend on generator nodes. See [Autodesk module documentation](https://help.autodesk.com/cloudhelp/2026/CHS/Maya-DEVHELP/files/Distributing-Maya-Plug-ins/DistributingUsingModules/Maya_DEVHELP_Distributing_Maya_Plug_ins_DistributingUsingModules_ModuleDescriptionFiles_html.html).

## Build and test (developers)

Users of the complete installer package do not need to compile the plugin.

The project declares CMake 3.21 as its minimum; also follow the target Maya toolchain requirements (CMake 3.22.1+ for Maya 2024 and 3.27.3+ for Maya 2025/2026). A Microsoft C++ toolchain supporting C++17 and headers/libraries for the target Maya version are required. Consult that Maya version's Autodesk development requirements and the [Autodesk Maya API / DevKit page](https://aps.autodesk.com/developer/overview/maya-api). The SDK, headers, development libraries and third-party tools are not redistributed.

Run from the project root in PowerShell to build a local Maya 2026 binary at `plug-ins/2026/braidTools.mll`:

```powershell
cmake -S . -B build/2026 -A x64 -DMAYA_VERSION=2026 -DMAYA_LOCATION="C:/Program Files/Autodesk/Maya2026"
cmake --build build/2026 --config Release
```

Change the version, SDK path and build directory together for another year. CMake checks that the selected SDK matches `MAYA_VERSION`. Do not reuse a build cache across versions or replace a binary loaded in Maya.

All five binaries were rebuilt with Visual Studio 2022 / MSVC 19.44. Maya 2022 documents a Visual Studio 2019.7 baseline; local tests are not Autodesk certification. [Autodesk Maya 2022 devkit notes](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/What-s-New-What-s-Changed-/2022-Whats-New-in-API.html)

The 2023/2024 builds link their corresponding official base-release DevKits, using Release x64, MSVC 19.44.35229.0 and Windows SDK 10.0.26100.0. Autodesk specifies [VS2019 for Maya 2023](https://help.autodesk.com/cloudhelp/2023/ENU/Maya-SDK/files/Maya_SDK_MinimumRequirementsAllPlatforms_html.html) and [VS2022 for Maya 2024](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-SDK/files/Maya_SDK_MinimumRequirementsAllPlatforms_html.html). The 2023 toolchain differs from that baseline; both still require host validation.

`tests/integration_maya.py` must run in a new target-version `mayapy` process. Provide `--plugin`, `--scripts`, `--output` and `--profile`; the profile must be a dedicated child of the output directory. Set `BRAIDTOOLS_ISOLATED_TEST=1` and `MAYA_APP_DIR` to that profile. `tests/module_qt_smoke.py` adds module-discovery and offscreen UI checks. Do not execute these tests in an existing interactive Maya session. Sanitize local paths in raw reports before publishing them.

## Node ID and registered-ID release builds

Autodesk's technical documentation requires globally unique IDs for shared dependency-graph plugins. This learning preview still uses **unregistered** development ID `0x0007B7A1` for `braidToolsGenerator`. It may conflict with another plugin using the same ID; it is not an assigned project ID. See [NOTICE.txt](NOTICE.txt) and [Autodesk's node ID guidance](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/Maya-Python-API/Dependency-Graph-Plug-in-Basics/Dependency-Graph-Plug-ins.html).

The learning preview uses the default development build. The project separately retains `BRAIDTOOLS_PUBLIC_RELEASE` for **formal release builds using a registered ID**. That mode requires `BRAIDTOOLS_PUBLIC_RELEASE=ON`, the assigned `BRAIDTOOLS_NODE_ID`, and `BRAIDTOOLS_REGISTERED_NODE_ID_CONFIRMED=ON` after verifying ownership. The confirmation switch does not perform online registration checks. Rebuild each target and test scene migration, save and reopen after changing the ID; retain the development version for existing scenes until migration is verified.

The complete learning-preview ZIP is in the repository's `downloads` directory, linked above. A future registered-ID version needs a fresh build and validation, including migration of scenes made with the development ID.

## License

Source is licensed under the [MIT License](LICENSE). Copyright (c) 2026 Hsws9999. Use, modification and commercial redistribution are permitted subject to preservation of the copyright and permission notices.

Autodesk Maya, its SDK and bundled dependencies remain subject to their own terms. Users need their own Maya installation and applicable license.
