"""Offscreen Qt acceptance for per-strand control actions.

Requires a NEW mayapy with BRAIDTOOLS_ISOLATED_TEST=1, QT_QPA_PLATFORM=offscreen,
MAYA_APP_DIR pointing to a disposable profile and BRAIDTOOLS_TEST_REPORT below it.
This exercises real widgets without connecting to an interactive Maya session.
"""
import importlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

if (os.environ.get("BRAIDTOOLS_ISOLATED_TEST") != "1"
        or os.environ.get("QT_QPA_PLATFORM") != "offscreen"
        or Path(sys.executable).name.lower() not in ("mayapy", "mayapy.exe")):
    raise RuntimeError("Use a fresh isolated mayapy with offscreen Qt.")
PROFILE = Path(os.environ["MAYA_APP_DIR"]).resolve()
REPORT_PATH = Path(os.environ["BRAIDTOOLS_TEST_REPORT"]).resolve()
if PROFILE not in REPORT_PATH.parents:
    raise RuntimeError("The test report must remain below the isolated profile.")
PROFILE.mkdir(parents=True, exist_ok=True)
PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
os.environ["MAYA_SKIP_USERSETUP_PY"] = "1"
from maya.app.general import mayaMixin
QtWidgets = importlib.import_module(mayaMixin._qtImported + ".QtWidgets")
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
from maya import cmds, utils
from maya.api import OpenMaya as om
from braidtools import api, ui
from braidtools._qt import QtCore
api.ensure_loaded()
cmds.undoInfo(state=True, infinity=True)
REPORT = {"status": "running", "complete": False, "maya": str(api.maya_major_version()),
          "qt": QtCore.qVersion(), "cases": [],
          "scope": "Fresh isolated offscreen widgets; no interactive Maya or user scene."}
panel = None


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def events():
    for unused in range(3):
        utils.processIdleEvents()
        APP.processEvents()


def fresh():
    global panel
    if panel is not None:
        panel.dispose()
        panel.close()
    cmds.file(new=True, force=True)
    nodes = []
    for index in range(2):
        source = cmds.curve(name="path%d" % index, degree=3,
                            point=[(index * 4, 0, 0), (index * 4 + 1, 3, 0),
                                   (index * 4 - 1, 7, 1), (index * 4, 11, 0)])
        nodes.extend(api.create([source]))
    panel = ui.BraidToolsPanel()
    events()
    return nodes


def select_rows(nodes):
    panel.assets.clearSelection()
    for index in range(panel.assets.count()):
        item = panel.assets.item(index)
        item.setSelected(item.data(QtCore.Qt.ItemDataRole.UserRole) in nodes)
    events()
    check(set(panel._nodes) == set(nodes), "Asset list selection did not resolve public generators.")


def button(name):
    value = panel.findChild(QtWidgets.QPushButton, name)
    check(value is not None, "Missing control action widget: " + name)
    return value


def samples():
    value = panel.findChild(QtWidgets.QSpinBox, "BraidToolsStrandSamples")
    check(value is not None, "Missing control point-count widget.")
    return value


def enable(nodes, count=24):
    select_rows(nodes)
    samples().setValue(count)
    action = button("BraidToolsEnableStrandControls")
    check(action.isEnabled(), "Enable controls action is disabled for a selected legacy asset.")
    action.click()
    events()
    for node in nodes:
        check(len(api.strand_controls(node)) == 3, "Enable button did not create three controls.")


def control_shape(control):
    return cmds.listRelatives(control, shapes=True, noIntermediate=True, fullPath=True, type="nurbsCurve")[0]


def points(node, strand, effective=True):
    generator = api.geometry_generator(node) if effective else node
    data = api._plug(generator, "outMesh").elementByLogicalIndex(strand).asMObject()
    return [(point.x, point.y, point.z) for point in om.MFnMesh(data).getPoints()]


def difference(a, b):
    check(len(a) == len(b), "Mesh point counts differ.")
    return max((math.sqrt(sum((x - y) ** 2 for x, y in zip(first, second)))
                for first, second in zip(a, b)), default=0.0)


def edit(node, strand, amount=0.6):
    control = api.strand_controls(node)[strand]
    cmds.move(amount, 0.1, 0.0, control_shape(control) + ".cv[10]", relative=True, objectSpace=True)
    events()


def selection_gates():
    nodes = fresh()
    select_rows([])
    check(not button("BraidToolsEnableStrandControls").isEnabled(), "Enable button active with no selected asset.")
    for index in range(1, 4):
        check(not button("BraidToolsSelectStrand%d" % index).isEnabled(), "Select action active without an asset.")
        check(not button("BraidToolsResetStrand%d" % index).isEnabled(), "Reset action active without an asset.")
    select_rows([nodes[0]])
    check(button("BraidToolsEnableStrandControls").isEnabled(), "Legacy asset cannot enable strand controls.")
    check(not button("BraidToolsSelectStrand1").isEnabled(), "Select action active before controls exist.")
    check(not button("BraidToolsResetAllStrands").isEnabled(), "Reset action active before controls exist.")


def enable_respects_count_and_hides_helper_rows():
    nodes = fresh()
    enable([nodes[0]], 24)
    for control in api.strand_controls(nodes[0]):
        selection = om.MSelectionList()
        selection.add(control_shape(control))
        check(om.MFnNurbsCurve(selection.getDagPath(0)).numCVs == 24,
              "UI count was not used as the actual control CV count.")
    check(panel.assets.count() == 2, "An auxiliary generator appeared as another braid in the asset list.")
    check(api.strand_controls(nodes[1]) == [], "Enabling a selected asset affected an unselected asset.")


def select_each_strand_keeps_public_asset():
    nodes = fresh()
    enable([nodes[0]])
    controls = api.strand_controls(nodes[0])
    for index in range(3):
        select_rows([nodes[0]])
        action = button("BraidToolsSelectStrand%d" % (index + 1))
        check(action.isEnabled(), "Strand selection button is disabled after enabling.")
        action.click()
        events()
        selected = cmds.ls(selection=True, long=True) or []
        check(selected, "Strand selection button selected nothing.")
        selected_objects = [item.split(".", 1)[0] for item in selected]
        expected = {controls[index], control_shape(controls[index])}
        check(all(item in expected for item in selected_objects), "Selection escaped the chosen strand control.")
        check(api.generators_from_selection() == [nodes[0]], "Control selection resolved to an auxiliary or other asset.")
        check(panel._nodes == [nodes[0]], "Panel lost its selected asset after selecting its control.")


def reset_one_via_widget_and_undo():
    nodes = fresh()
    enable([nodes[0]])
    node = nodes[0]
    edit(node, 0)
    edit(node, 1, -0.5)
    before = [points(node, index) for index in range(3)]
    baseline = points(node, 0, effective=False)
    select_rows([node])
    cmds.flushUndo()
    button("BraidToolsResetStrand1").click()
    events()
    check(difference(points(node, 0), baseline) < 2e-5, "Reset strand button did not reset its strand.")
    check(difference(points(node, 1), before[1]) < 2e-5, "Single-strand UI reset changed another strand.")
    cmds.undo()
    events()
    check(difference(points(node, 0), before[0]) < 2e-5, "One undo did not undo the UI strand reset.")


def batch_enable_and_reset_all():
    nodes = fresh()
    enable(nodes, 16)
    check(panel.assets.count() == 2, "Batch enabling exposed helpers in the list.")
    for node in nodes:
        edit(node, 0)
        edit(node, 2, -0.4)
    select_rows(nodes)
    action = button("BraidToolsResetAllStrands")
    check(action.isEnabled(), "Reset all is disabled for a controlled asset batch.")
    action.click()
    events()
    for node in nodes:
        for strand in range(3):
            check(difference(points(node, strand), points(node, strand, effective=False)) < 2e-5,
                  "Batch reset all left a deformation.")


def enable_button_one_undo_redo():
    nodes = fresh()
    select_rows([nodes[0]])
    samples().setValue(24)
    cmds.flushUndo()
    button("BraidToolsEnableStrandControls").click()
    events()
    check(len(api.strand_controls(nodes[0])) == 3, "UI did not enable controls.")
    cmds.undo()
    events()
    check(api.strand_controls(nodes[0]) == [] and panel.assets.count() == 2,
          "UI enable is not a single reversible operation.")
    cmds.redo()
    events()
    check(len(api.strand_controls(nodes[0])) == 3 and panel.assets.count() == 2,
          "Redo did not restore controls without helper rows.")


def panel_preview():
    nodes = fresh()
    enable([nodes[0]], 32)
    select_rows([nodes[0]])
    # Maya's offscreen Qt platform can miss Windows font discovery. Load the
    # existing system font explicitly for this screenshot only, not production.
    font_path = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/msyh.ttc"
    if font_path.is_file():
        font_id = ui.QtGui.QFontDatabase.addApplicationFont(str(font_path))
        families = ui.QtGui.QFontDatabase.applicationFontFamilies(font_id)
        check(bool(families), "Could not load the system Chinese font for the preview.")
        panel.setFont(ui.QtGui.QFont(families[0], 9))
    panel.resize(580, 1500)
    # Maya's mixin overrides both show and setVisible, including calls through
    # QWidget.show. Qt can grab a polished hidden widget without either path.
    panel.ensurePolished()
    panel.layout().activate()
    events()
    screenshot = REPORT_PATH.parent / "strand_controls_panel.png"
    check(panel.grab().save(str(screenshot)), "Could not save the offscreen panel preview.")
    REPORT["preview"] = str(screenshot)
    REPORT["preview_scope"] = "Offscreen Qt layout preview; not an interactive Maya viewport acceptance."


CASES = [selection_gates, enable_respects_count_and_hides_helper_rows,
         select_each_strand_keeps_public_asset, reset_one_via_widget_and_undo,
         batch_enable_and_reset_all, enable_button_one_undo_redo, panel_preview]
for test in CASES:
    try:
        test()
        result = {"name": test.__name__, "status": "passed"}
    except Exception:
        result = {"name": test.__name__, "status": "failed", "traceback": traceback.format_exc()}
    REPORT["cases"].append(result)
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["name"] + ": " + result["status"], flush=True)
REPORT["status"] = "passed" if all(case["status"] == "passed" for case in REPORT["cases"]) else "failed"
REPORT["complete"] = True
try:
    if panel is not None:
        panel.dispose()
        panel.close()
    events()
    cmds.file(new=True, force=True)
    maya.standalone.uninitialize()
    REPORT["teardown"] = "passed"
except Exception:
    REPORT["status"] = "failed"
    REPORT["teardown"] = traceback.format_exc()
REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT: " + str(REPORT_PATH), flush=True)
sys.exit(0 if REPORT["status"] == "passed" else 1)
