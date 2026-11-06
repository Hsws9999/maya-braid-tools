"""Scoped source-shape repair after Maya replaces a registered curve shape.

The DG node handles geometry updates. These callbacks only repair structural
connections after Rebuild Curve, scene open/import, and undo/redo. No timers,
polling or source-name matching are used. Importing does not install callbacks.
"""
from maya import cmds
from maya.api import OpenMaya as om

_callbacks = []
_source_handles = {}
_scheduled = False
_repairing = False
_reported = set()


def is_installed():
    return bool(_callbacks)


def refresh_registry():
    """Refresh source identities from message links without editing the scene."""
    from . import api
    global _source_handles
    handles = {}
    if api.NODE_TYPE not in (cmds.allNodeTypes() or []):
        _source_handles = handles
        return
    for node in cmds.ls(type=api.NODE_TYPE) or []:
        source = api.source_transform(node)
        if not source:
            continue
        try:
            selection = om.MSelectionList()
            selection.add(source)
            handle = om.MObjectHandle(selection.getDependNode(0))
            handles[handle.hashCode()] = handle
        except (RuntimeError, ValueError):
            continue
    _source_handles = handles


def reconnect_all():
    """Repair only the live shape beneath each message-registered transform.

    Repairs are derived from current scene identity and intentionally do not
    create extra undo steps. Undo/Redo callbacks recompute the same invariant.
    Missing/ambiguous sources are left untouched for explicit user rebind.
    """
    from . import api
    global _repairing
    if _repairing or api._operation_depth:
        return []
    if api.NODE_TYPE not in (cmds.allNodeTypes() or []):
        return []
    repaired = []
    _repairing = True
    undo_state = cmds.undoInfo(query=True, state=True)
    try:
        if undo_state:
            cmds.undoInfo(stateWithoutFlush=False)
        for node in cmds.ls(type=api.NODE_TYPE) or []:
            source = api.source_transform(node)
            if not source:
                continue
            try:
                shape = api._curve_shape(source)
                instance = api._dag_path(shape).instanceNumber()
                plug = "%s.worldSpace[%d]" % (shape, instance)
                if not cmds.isConnected(plug, node + ".inputCurve"):
                    cmds.connectAttr(plug, node + ".inputCurve", force=True)
                    repaired.append(node)
            except (RuntimeError, ValueError) as exc:
                key = (node, str(exc))
                if key not in _reported:
                    om.MGlobal.displayWarning("BraidTools: %s；可在面板中重新绑定。" % exc)
                    _reported.add(key)
    finally:
        if undo_state:
            cmds.undoInfo(stateWithoutFlush=True)
        _repairing = False
    return repaired


def _flush():
    global _scheduled
    _scheduled = False
    if not _callbacks:
        return
    if _repairing:
        return
    refresh_registry()
    reconnect_all()


def _schedule(*unused):
    global _scheduled
    if _repairing or _scheduled or not _callbacks:
        return
    _scheduled = True
    cmds.evalDeferred(_flush, lowestPriority=True)


def _dag_changed(parent, child, *unused):
    if not _source_handles or _repairing:
        return
    try:
        # Both arguments are inspected because reparent events can describe
        # either side of the changed edge; only registered identities qualify.
        for path in (parent, child):
            node = path.node()
            key = om.MObjectHandle(node).hashCode()
            handle = _source_handles.get(key)
            if handle and handle.isValid() and handle.isAlive():
                _schedule()
                return
    except RuntimeError:
        return


def install():
    """Idempotently register lightweight callbacks; do not change scene data."""
    if _callbacks:
        return
    _callbacks.extend([
        om.MDGMessage.addNodeAddedCallback(_schedule, "braidToolsGenerator"),
        om.MDGMessage.addNodeRemovedCallback(_schedule, "braidToolsGenerator"),
        om.MDagMessage.addChildAddedCallback(_dag_changed),
        om.MDagMessage.addChildRemovedCallback(_dag_changed),
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen, _schedule),
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterImport, _schedule),
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterNew, _schedule),
        om.MEventMessage.addEventCallback("Undo", _schedule),
        om.MEventMessage.addEventCallback("Redo", _schedule),
    ])
    refresh_registry()


def uninstall():
    """Remove only this module's callbacks; leave every scene object intact."""
    global _scheduled
    for callback in _callbacks:
        try:
            om.MMessage.removeCallback(callback)
        except RuntimeError:
            pass
    _callbacks[:] = []
    _source_handles.clear()
    _reported.clear()
    _scheduled = False
