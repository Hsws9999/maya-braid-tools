"""Isolated offscreen Qt acceptance for explicit BraidTools asset deletion.

Set BRAIDTOOLS_ISOLATED_TEST=1, QT_QPA_PLATFORM=offscreen, MAYA_APP_DIR to
a disposable directory and BRAIDTOOLS_TEST_REPORT to a file beneath it.
Never run this script inside an interactive Maya session.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

if os.environ.get('BRAIDTOOLS_ISOLATED_TEST') != '1':
    raise RuntimeError('An explicitly isolated mayapy process is required.')
if os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
    raise RuntimeError('This test requires offscreen Qt.')
profile = Path(os.environ['MAYA_APP_DIR']).resolve()
report_path = Path(os.environ['BRAIDTOOLS_TEST_REPORT']).resolve()
if profile not in report_path.parents:
    raise RuntimeError('The test report must remain inside the isolated profile.')
profile.mkdir(parents=True, exist_ok=True)
package = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(package / 'scripts'))
from maya.app.general import mayaMixin
QtWidgets = importlib.import_module(mayaMixin._qtImported + '.QtWidgets')
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name='python')
from maya import cmds
from braidtools import api, ui
from braidtools._qt import QtCore
api.ensure_loaded()
cmds.undoInfo(state=True, infinity=True)
report = {'status': 'running', 'maya': str(api.maya_major_version()),
          'qt': QtCore.qVersion(), 'cases': [],
          'scope': 'Isolated offscreen widgets; no user scene or interactive Maya.'}
panel = None
real_exec = ui.dialog_exec


def events():
    for unused in range(3):
        app.processEvents()


def fresh():
    global panel
    if panel is not None:
        panel.dispose()
        panel.close()
    cmds.file(new=True, force=True)
    first = cmds.curve(name='pathA', degree=3,
                       point=[(0, 0, 0), (1, 3, 0), (-1, 7, 0), (0, 10, 0)])
    second = cmds.curve(name='pathB', degree=3,
                        point=[(3, 0, 0), (4, 3, 0), (2, 7, 0), (3, 10, 0)])
    nodes = api.create([first, second])
    api.rebind(nodes[1], first)
    panel = ui.BraidToolsPanel()
    events()
    return first, second, nodes


def select_rows(nodes):
    panel.assets.clearSelection()
    for index in range(panel.assets.count()):
        item = panel.assets.item(index)
        item.setSelected(item.data(QtCore.Qt.ItemDataRole.UserRole) in nodes)
    assert set(panel._nodes) == set(nodes), panel._nodes


def invoke(accept, during=None):
    observed = []
    errors = []

    def execute(dialog):
        assert dialog.objectName() == 'BraidToolsDeleteDialog'
        confirm = dialog.findChild(QtWidgets.QPushButton, 'BraidToolsDeleteConfirm')
        cancel = dialog.findChild(QtWidgets.QPushButton, 'BraidToolsDeleteCancel')
        assert confirm is not None and cancel is not None
        assert cancel.isDefault() and not confirm.isDefault()
        observed.append(dialog)

        def act():
            try:
                if during:
                    during()
                (confirm if accept else cancel).click()
            except Exception:
                errors.append(traceback.format_exc())
                dialog.reject()
        QtCore.QTimer.singleShot(0, act)
        return real_exec(dialog)

    ui.dialog_exec = execute
    try:
        panel.delete_button.click()
    finally:
        ui.dialog_exec = real_exec
    events()
    assert observed, 'The delete action did not display its confirmation.'
    assert not errors, errors


def record(name, operation):
    operation()
    report['cases'].append({'name': name, 'status': 'passed'})


def no_selection_and_cancel():
    source, unused, nodes = fresh()
    select_rows([])
    assert not panel.delete_button.isEnabled()
    select_rows([nodes[0]])
    assert panel.delete_button.isEnabled()
    panel.resize(465, 1000)
    events()
    assert panel.grab().save(str(profile / 'delete_panel.png'))
    before = set(cmds.ls(long=True))
    cmds.flushUndo()
    invoke(False)
    assert set(cmds.ls(long=True)) == before
    assert cmds.undoInfo(query=True, undoQueueEmpty=True)
    assert panel.assets.count() == 2


def single_shared_source_and_undo():
    source, unused, nodes = fresh()
    snapshots = api.extract_guides([nodes[0]]) + api.bake([nodes[0]])
    roots = [api.asset_root(node) for node in nodes]
    select_rows([nodes[0]])
    cmds.flushUndo()
    invoke(True, lambda: cmds.select(source, replace=True))
    assert not cmds.objExists(nodes[0]) and cmds.objExists(nodes[1])
    assert not cmds.objExists(roots[0]) and cmds.objExists(roots[1])
    assert cmds.objExists(source) and all(cmds.objExists(path) for path in snapshots)
    assert panel.assets.count() == 1
    assert not panel.delete_button.isEnabled()
    cmds.undo()
    events()
    assert all(cmds.objExists(node) for node in nodes)
    assert panel.assets.count() == 2
    cmds.redo()
    events()
    assert not cmds.objExists(nodes[0]) and cmds.objExists(nodes[1])
    assert panel.assets.count() == 1


def multiple_targets():
    source, unused, nodes = fresh()
    select_rows(nodes)
    cmds.flushUndo()
    invoke(True)
    assert not any(cmds.objExists(node) for node in nodes)
    assert cmds.objExists(source)
    assert panel.assets.count() == 0 and not panel.delete_button.isEnabled()
    cmds.undo()
    events()
    assert all(cmds.objExists(node) for node in nodes)
    assert panel.assets.count() == 2


def rename_while_confirming():
    source, unused, nodes = fresh()
    select_rows([nodes[0]])
    renamed = []
    invoke(True, lambda: renamed.append(cmds.rename(nodes[0], 'renamedDuringConfirmation')))
    assert not cmds.objExists(renamed[0])
    assert cmds.objExists(nodes[1]) and cmds.objExists(source)


def locked_batch_refusal():
    source, unused, nodes = fresh()
    cmds.lockNode(nodes[1], lock=True)
    select_rows(nodes)
    invoke(True)
    assert all(cmds.objExists(node) for node in nodes)
    assert panel.assets.count() == 2
    assert '\u672a\u5b8c\u6210' in panel.status.text(), panel.status.text()
    cmds.lockNode(nodes[1], lock=False)


def deleted_target_cannot_match_replacement():
    source, unused, nodes = fresh()
    select_rows([nodes[0]])
    replacement = []

    def replace():
        cmds.delete(nodes[0])
        replacement.append(cmds.createNode(api.NODE_TYPE, name=nodes[0]))
    invoke(True, replace)
    assert cmds.objExists(replacement[0]) and cmds.objExists(nodes[1])
    assert '\u672a\u5b8c\u6210' in panel.status.text(), panel.status.text()


def stale_row_cannot_match_replacement():
    source, unused, nodes = fresh()
    select_rows([nodes[0]])
    # Keep the visible list intact while an external script replaces a node.
    # Selection synchronization is irrelevant to an already-selected list row.
    panel._delete_active = True
    try:
        cmds.delete(nodes[0])
        replacement = cmds.createNode(api.NODE_TYPE, name=nodes[0])
    finally:
        panel._delete_active = False
    calls = []
    ui.dialog_exec = lambda dialog: calls.append(dialog) or 0
    try:
        panel.delete_button.click()
    finally:
        ui.dialog_exec = real_exec
    assert not calls, 'A stale row must be rejected before offering confirmation.'
    assert cmds.objExists(replacement) and cmds.objExists(nodes[1])
    assert '\u672a\u5b8c\u6210' in panel.status.text(), panel.status.text()


def reverse_root_link_changed_during_confirmation():
    source, unused, nodes = fresh()
    root = api.asset_root(nodes[0])
    cmds.delete(cmds.listRelatives(root, children=True, fullPath=True) or [])
    cmds.disconnectAttr(root + '.message', nodes[0] + '.assetRoot')
    replacement = cmds.createNode('transform', name='notConfirmedGroup')
    cmds.addAttr(replacement, longName='braidToolsGenerator', attributeType='message')
    panel.refresh()
    select_rows([nodes[0]])

    def swap_root():
        cmds.disconnectAttr(nodes[0] + '.message', root + '.braidToolsGenerator')
        cmds.connectAttr(nodes[0] + '.message', replacement + '.braidToolsGenerator')
    invoke(True, swap_root)
    assert cmds.objExists(nodes[0]) and cmds.objExists(replacement)
    assert cmds.objExists(root) and cmds.objExists(nodes[1])
    assert '\u672a\u5b8c\u6210' in panel.status.text(), panel.status.text()


try:
    for name, test in [
        ('no_selection_and_cancel', no_selection_and_cancel),
        ('single_shared_source_and_undo_redo', single_shared_source_and_undo),
        ('multiple_targets_one_undo', multiple_targets),
        ('rename_while_confirming', rename_while_confirming),
        ('locked_batch_refusal', locked_batch_refusal),
        ('deleted_target_cannot_match_replacement', deleted_target_cannot_match_replacement),
        ('stale_row_cannot_match_replacement', stale_row_cannot_match_replacement),
        ('reverse_root_link_changed_during_confirmation', reverse_root_link_changed_during_confirmation),
    ]:
        record(name, test)
    report['status'] = 'passed'
except Exception:
    report['status'] = 'failed'
    report['error'] = traceback.format_exc()
finally:
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True), flush=True)
    if panel is not None:
        panel.dispose()
        panel.close()
    events()
    cmds.file(new=True, force=True)
    maya.standalone.uninitialize()
if report['status'] != 'passed':
    raise SystemExit(1)
