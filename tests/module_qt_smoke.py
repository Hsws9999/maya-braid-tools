"""Run only in an isolated mayapy with a package in MAYA_APP_DIR/modules.

Checks module discovery and offscreen Qt widgets, not Maya docking or
viewport interaction. Set BRAIDTOOLS_ISOLATED_TEST=1, MAYA_APP_DIR to a disposable
profile, QT_QPA_PLATFORM=offscreen and pass the report path as the only argument.
"""
import json
import importlib
import os
from pathlib import Path
import sys

if os.environ.get('BRAIDTOOLS_ISOLATED_TEST') != '1':
    raise RuntimeError('This test requires an isolated process/profile.')
if os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
    raise RuntimeError('This test must not open visible windows.')
profile = Path(os.environ['MAYA_APP_DIR']).resolve()
expected = profile / 'modules' / 'BraidTools'
if not (profile / 'modules' / 'BraidTools.mod').is_file():
    raise RuntimeError('Copy the module descriptor beside the package folder first.')
report_path = Path(sys.argv[1]).resolve()
if profile not in report_path.parents:
    raise RuntimeError('Report must stay inside the isolated profile.')

# Maya standalone creates a QGuiApplication; QWidget tests need QApplication
# first. Creating a second application after initialize() is invalid in Qt.
from maya.app.general import mayaMixin
QtWidgets = importlib.import_module(mayaMixin._qtImported + '.QtWidgets')
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
if not isinstance(app, QtWidgets.QApplication):
    raise RuntimeError('Run in a fresh mayapy before Maya initializes QGuiApplication.')
import maya.standalone
maya.standalone.initialize(name='python')
from maya import cmds
module_path = Path(cmds.moduleInfo(moduleName='BraidTools', path=True)).resolve()
assert module_path == expected, (module_path, expected)
import braidtools
assert Path(braidtools.__file__).resolve().parent.parent == expected / 'scripts'
from braidtools import api
api.ensure_loaded()
from braidtools import ui
from braidtools._qt import QtCore, QtGui, QtWidgets, dialog_exec

path = cmds.curve(degree=3, point=[(0, 0, 0), (1, 3, 0), (-1, 7, 0), (0, 10, 0)])
node = api.create([path])[0]
cmds.select(path)
panel = ui.BraidToolsPanel()
panel.refresh()
assert panel._nodes == [node]
initial_size = cmds.getAttr(node+'.size')
panel._begin_edit()
panel.rows['size'].spin.setValue(2.0)
panel._end_edit()
assert abs(cmds.getAttr(node+'.size') - 2.0) < 1e-6
cmds.undo()
assert abs(cmds.getAttr(node+'.size') - initial_size) < 1e-6

# Render a real QWidget to exercise Qt enum and painter overloads offscreen.
canvas = ui.RampCanvas()
canvas.resize(400, 160)
canvas.show()
app.processEvents()
assert not canvas.grab().isNull()
start = canvas._point(canvas.entries[0])
finish = QtCore.QPointF(start.x(), start.y()+15.0)
def event(kind, position, button, buttons):
    return QtGui.QMouseEvent(kind, position, button, buttons, QtCore.Qt.KeyboardModifier.NoModifier)
left = QtCore.Qt.MouseButton.LeftButton
none = QtCore.Qt.MouseButton.NoButton
canvas.mousePressEvent(event(QtCore.QEvent.Type.MouseButtonPress, start, left, left))
canvas.mouseMoveEvent(event(QtCore.QEvent.Type.MouseMove, finish, none, left))
canvas.mouseReleaseEvent(event(QtCore.QEvent.Type.MouseButtonRelease, finish, left, none))
assert canvas.entries[0][1] < 1.0
dialog = QtWidgets.QDialog()
QtCore.QTimer.singleShot(0, dialog.accept)
assert dialog_exec(dialog) == int(QtWidgets.QDialog.DialogCode.Accepted)
report = {'status': 'passed', 'mayaVersion': cmds.about(version=True),
          'apiVersion': cmds.about(apiVersion=True), 'python': sys.version.split()[0],
          'qt': QtCore.qVersion(), 'topLevelModuleDiscovered': True,
          'perVersionPluginLoaded': True, 'panelConstructed': True,
          'panelParameterUndo': True, 'offscreenRampPaintAndMouse': True,
          'dialogExec': True, 'scope': 'Offscreen widgets; no docking or viewport test.'}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
panel.dispose()
panel.close()
canvas.close()
dialog.close()
app.processEvents()
cmds.file(new=True, force=True)
maya.standalone.uninitialize()
