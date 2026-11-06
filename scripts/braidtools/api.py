"""Undoable scene operations; all live geometry is evaluated by the C++ node.

Public create() returns generator node names. extract_guides() and bake()
return independent snapshot root transforms. Names are labels only: identity
and traversal use Maya connections and DAG paths.
"""
from contextlib import contextmanager
import math
import os
import re
import sys

from maya import cmds
from maya.api import OpenMaya as om

NODE_TYPE = "braidToolsGenerator"
PLUGIN_NAME = "braidTools"
PARAMETERS = {
    "pitch", "cycles", "size", "flatness", "strandThickness", "strandFlatness",
    "orientation", "phase", "longitudinalMode", "lengthSegments", "radialSides",
    "autoQuality", "guideSamples",
}
INTEGER_PARAMETERS = {"longitudinalMode", "lengthSegments", "radialSides", "guideSamples"}
_CONTROL_FORWARD = tuple(sorted(PARAMETERS)) + (
    "densityMode", "sizeRamp", "rotationRamp", "inputCurve", "sourceWorldMatrix",
    "outputParentInverseMatrix", "frameReferenceTangent", "frameReferenceNormal",
    "strandControlSamples",
)
_operation_depth = 0


@contextmanager
def undo_chunk(label="BraidTools"):
    global _operation_depth
    outer = _operation_depth == 0
    if outer:
        cmds.undoInfo(openChunk=True, chunkName=label)
    _operation_depth += 1
    try:
        yield
    finally:
        _operation_depth -= 1
        if outer:
            cmds.undoInfo(closeChunk=True)


def maya_major_version():
    """Use the API version, not localized product/version display strings."""
    return int(cmds.about(apiVersion=True)) // 10000


def plugin_path():
    """Return only this package's binary for the running Maya major version."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(root, "plug-ins", str(maya_major_version()), "braidTools.mll")


def _canonical_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _loaded_braid_plugins():
    """Also find renamed plug-ins that already own our dependency-node type."""
    result = []
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        path = cmds.pluginInfo(name, query=True, path=True)
        node_types = cmds.pluginInfo(name, query=True, dependNode=True) or []
        if (NODE_TYPE in node_types or
                os.path.splitext(os.path.basename(path))[0].lower() == PLUGIN_NAME.lower()):
            result.append((name, path, node_types))
    return result


def _verify_loaded_plugin(plugins, expected_path, major):
    if len(plugins) != 1:
        raise RuntimeError("BraidTools 插件注册状态不明确；请重启 Maya 后从同一安装目录打开。")
    name, actual_path, node_types = plugins[0]
    if _canonical_path(actual_path) != _canonical_path(expected_path):
        raise RuntimeError(
            "已加载其他目录的 BraidTools，不能混用插件和脚本。\n"
            "已加载: %s\n当前包需要: %s\n请保存工作并重启 Maya，再从目标安装目录打开。"
            % (actual_path, expected_path))
    plugin_major = int(cmds.pluginInfo(name, query=True, apiVersion=True)) // 10000
    if plugin_major != major:
        raise RuntimeError("BraidTools 插件编译版本为 Maya %s，当前为 Maya %s。\n%s"
                           % (plugin_major, major, actual_path))
    if NODE_TYPE not in node_types or NODE_TYPE not in (cmds.allNodeTypes() or []):
        raise RuntimeError("已加载的 BraidTools 未注册所需节点 %s。\n%s" % (NODE_TYPE, actual_path))


def ensure_loaded():
    """Load and verify the exact per-version binary; never unload scene nodes."""
    if sys.platform != "win32":
        raise RuntimeError("此 BraidTools 包需要 Windows Maya 和对应版本的 .mll 插件。")
    major = maya_major_version()
    expected_path = plugin_path()
    loaded = _loaded_braid_plugins()
    if not loaded:
        if NODE_TYPE in (cmds.allNodeTypes() or []):
            raise RuntimeError("节点 %s 已存在，但无法确认所属插件；请重启 Maya 后重新打开。" % NODE_TYPE)
        if not os.path.isfile(expected_path):
            raise RuntimeError(
                "当前包缺少 Maya %s 的 BraidTools 插件。\n需要文件: %s\n"
                "请使用包含该版本二进制的发布包，或针对该 Maya 版本编译源码。"
                % (major, expected_path))
        try:
            cmds.loadPlugin(expected_path, quiet=True)
        except RuntimeError as exc:
            raise RuntimeError("无法加载 Maya %s 的 BraidTools 插件。\n%s\n%s"
                               % (major, expected_path, exc)) from exc
        loaded = _loaded_braid_plugins()
    _verify_loaded_plugin(loaded, expected_path, major)
    from . import lifecycle
    lifecycle.install()


def _as_list(value):
    if isinstance(value, str):
        return [value]
    return list(value or [])


def _unique(values):
    return list(dict.fromkeys(values))


def _long(node):
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise ValueError("对象不存在或名称不唯一: %s" % node)
    return matches[0]


def _dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _curve_shape(curve, require_open=True):
    curve = _long(curve.split(".", 1)[0])
    if cmds.nodeType(curve) == "nurbsCurve":
        if cmds.getAttr(curve + ".intermediateObject"):
            parents = cmds.listRelatives(curve, parent=True, fullPath=True) or []
            curve = parents[0] if parents else curve
        else:
            shape = curve
            if require_open and cmds.getAttr(shape + ".form") != 0:
                raise ValueError("只支持开放 NURBS 曲线: %s" % curve)
            return shape
    shapes = cmds.listRelatives(curve, shapes=True, noIntermediate=True, fullPath=True, type="nurbsCurve") or []
    if len(shapes) != 1:
        raise ValueError("需要恰好包含一条可见 NURBS 曲线的变换节点: %s" % curve)
    shape = shapes[0]
    if require_open and cmds.getAttr(shape + ".form") != 0:
        raise ValueError("只支持开放 NURBS 曲线: %s" % curve)
    return shape


def _curve_transform(curve):
    return (cmds.listRelatives(_curve_shape(curve), parent=True, fullPath=True) or [])[0]


def primary_generator(node):
    """Resolve the private deformation stage through a reciprocal message link."""
    node = _long(node)
    if cmds.nodeType(node) != NODE_TYPE:
        raise ValueError("不是 BraidTools 生成节点: %s" % node)
    if not cmds.attributeQuery("braidToolsControlOwner", node=node, exists=True):
        return node
    owners = cmds.listConnections(node + ".braidToolsControlOwner", source=True, destination=False) or []
    if (len(owners) != 1 or cmds.nodeType(owners[0]) != NODE_TYPE
            or cmds.attributeQuery("braidToolsControlOwner", node=owners[0], exists=True)):
        raise ValueError("分股控制的辅助节点归属已损坏: %s" % node)
    owner = _long(owners[0])
    if (not cmds.attributeQuery("strandControlDriver", node=owner, exists=True)
            or not cmds.isConnected(node + ".message", owner + ".strandControlDriver")):
        raise ValueError("分股控制的双向归属连接已损坏: %s" % node)
    return owner


def all_generators():
    """List user-facing assets only; helpers are never independent assets."""
    return [node for node in (cmds.ls(type=NODE_TYPE) or [])
            if not cmds.attributeQuery("braidToolsControlOwner", node=node, exists=True)]


def geometry_generator(node):
    """Return the effective mesh/guide stage, retaining primary public identity."""
    node = primary_generator(node)
    if not cmds.attributeQuery("strandControlDriver", node=node, exists=True):
        return node
    drivers = cmds.listConnections(node + ".strandControlDriver", source=True, destination=False) or []
    if not drivers:
        controls = (cmds.listConnections(node + ".strandControls", source=True, destination=False)
                    if cmds.attributeQuery("strandControls", node=node, exists=True) else [])
        if controls:
            raise ValueError("分股控制辅助节点缺失，不能使用未调整的几何代替: %s" % node)
        return node
    if len(drivers) != 1 or primary_generator(drivers[0]) != node:
        raise ValueError("分股控制辅助节点归属不明确: %s" % node)
    return _long(drivers[0])


def source_transform(node):
    node = primary_generator(node)
    values = cmds.listConnections(node + ".sourceCurve", source=True, destination=False) or []
    return _long(values[0]) if values else None


def asset_root(node):
    node = primary_generator(node)
    values = cmds.listConnections(node + ".assetRoot", source=True, destination=False) or []
    return _long(values[0]) if values else None


def _generators(nodes):
    result = []
    for node in _as_list(nodes):
        node = _long(node)
        if cmds.nodeType(node) != NODE_TYPE:
            raise ValueError("不是 BraidTools 生成节点: %s" % node)
        result.append(primary_generator(node))
    return _unique(result)


def generators_from_selection(selection=None):
    """Resolve source curves, generated meshes, roots or generator selections."""
    result = []
    for item in _as_list(selection) if selection is not None else (cmds.ls(selection=True, long=True) or []):
        item = item.split(".", 1)[0]
        if not cmds.objExists(item):
            continue
        if cmds.nodeType(item) == NODE_TYPE:
            result.append(item)
            continue
        path = _long(item)
        # The source's message can drive multiple generators after an explicit rebind.
        for candidate in cmds.listConnections(path + ".message", source=False, destination=True, type=NODE_TYPE) or []:
            result.append(candidate)
        while path:
            if cmds.attributeQuery("braidToolsGenerator", node=path, exists=True):
                result.extend(cmds.listConnections(path + ".braidToolsGenerator", source=True, destination=False, type=NODE_TYPE) or [])
            if cmds.nodeType(path) == "mesh":
                result.extend(cmds.listConnections(path + ".inMesh", source=True, destination=False, type=NODE_TYPE) or [])
            if cmds.nodeType(path) == "nurbsCurve":
                parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
                if parents:
                    result.extend(cmds.listConnections(parents[0] + ".message", source=False, destination=True, type=NODE_TYPE) or [])
            parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
            path = parents[0] if parents else None
    return _unique([primary_generator(node) for node in result])


def _label(source):
    leaf = source.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_]", "_", leaf) or "Braid"


def _root_reference_frame(shape_path):
    """Choose a local-space seed once, independent of later CV edits."""
    curve = om.MFnNurbsCurve(shape_path)
    try:
        tangent = curve.tangent(curve.knotDomain[0], om.MSpace.kObject)
    except RuntimeError:
        tangent = om.MVector(0.0, 1.0, 0.0)
    if not all(math.isfinite(value) for value in (tangent.x, tangent.y, tangent.z)) or tangent.length() <= 1e-12:
        # A collapsed path remains creatable so the native node can report its
        # actionable invalid-input status; never normalize a zero vector here.
        tangent = om.MVector(0.0, 1.0, 0.0)
    tangent.normalize()
    axis = om.MVector(1.0, 0.0, 0.0)
    normal = axis - tangent * (axis * tangent)
    if normal.length() <= 1e-8:
        axes = (om.MVector(1.0, 0.0, 0.0), om.MVector(0.0, 1.0, 0.0), om.MVector(0.0, 0.0, 1.0))
        axis = min(axes, key=lambda candidate: abs(candidate * tangent))
        normal = axis - tangent * (axis * tangent)
    normal.normalize()
    return (tangent.x, tangent.y, tangent.z), (normal.x, normal.y, normal.z)


def _check_source_dependency(node, shape):
    """Reject direct/indirect data cycles before replacing any source link.

    Message links describe ownership and do not carry geometry. Traverse only
    data connections, including transform ancestors whose matrices contribute
    to worldSpace. This also catches a history/constraint between two assets.
    """
    targets = [_plug(node, "message").node()]
    driver = geometry_generator(node)
    if driver != node:
        targets.append(_plug(driver, "message").node())
    target_ids = {om.MObjectHandle(item).hashCode() for item in targets}
    pending = [_dag_path(shape).node()]
    seen = set()
    while pending:
        current = pending.pop()
        identity = om.MObjectHandle(current).hashCode()
        if identity in target_ids:
            raise ValueError("不能绑定该曲线：它直接或间接依赖此辫子，会形成循环依赖。")
        if identity in seen:
            continue
        seen.add(identity)
        fn = om.MFnDependencyNode(current)
        for plug in fn.getConnections():
            if plug.attribute().hasFn(om.MFn.kMessageAttribute):
                continue
            for source in plug.connectedTo(True, False):
                if not source.attribute().hasFn(om.MFn.kMessageAttribute):
                    pending.append(source.node())
        if current.hasFn(om.MFn.kDagNode):
            dag = om.MFnDagNode(current)
            for index in range(dag.parentCount()):
                parent = dag.parent(index)
                if not parent.isNull():
                    pending.append(parent)


def _connect_curve(node, curve, connect_identity=True):
    shape = _curve_shape(curve)
    _check_source_dependency(node, shape)
    shape_path = _dag_path(shape)
    transform_path = om.MDagPath(shape_path)
    transform_path.pop()
    transform = transform_path.fullPathName()
    cmds.connectAttr("%s.worldSpace[%d]" % (shape, shape_path.instanceNumber()), node + ".inputCurve", force=True)
    cmds.connectAttr("%s.worldMatrix[%d]" % (transform, transform_path.instanceNumber()),
                     node + ".sourceWorldMatrix", force=True)
    if connect_identity:
        cmds.connectAttr(transform + ".message", node + ".sourceCurve", force=True)
        tangent, normal = _root_reference_frame(shape_path)
        cmds.setAttr(node + ".frameReferenceTangent", *tangent, type="double3")
        cmds.setAttr(node + ".frameReferenceNormal", *normal, type="double3")
    return transform


def create_from_selection():
    return create(cmds.ls(selection=True, long=True) or [])


def create(curves):
    """Create one live braid per open path; existing source assets are reused."""
    ensure_loaded()
    sources = _unique([_curve_transform(curve) for curve in _as_list(curves)])
    if not sources:
        raise ValueError("请先选择一条或多条开放 NURBS 曲线。")
    result = []
    with undo_chunk("BraidTools 创建麻花辫"):
        for source in sources:
            existing = []
            for node in cmds.listConnections(source + ".message", source=False, destination=True, type=NODE_TYPE) or []:
                if cmds.attributeQuery("braidToolsControlOwner", node=node, exists=True):
                    continue
                if source_transform(node) == source and asset_root(node):
                    existing.append(node)
            if existing:
                result.extend(existing)
                continue
            label = _label(source)
            root = cmds.createNode("transform", name=label + "_Braid#")
            root = _long(root)
            node = cmds.createNode(NODE_TYPE, name=label + "_BraidGenerator#")
            cmds.addAttr(root, longName="braidToolsGenerator", attributeType="message")
            cmds.connectAttr(node + ".message", root + ".braidToolsGenerator", force=True)
            cmds.connectAttr(root + ".message", node + ".assetRoot", force=True)
            cmds.connectAttr(root + ".worldInverseMatrix[0]", node + ".outputParentInverseMatrix", force=True)
            _connect_curve(node, source)
            _write_ramp(node, "sizeRamp", [(0.0, 1.0, 1), (1.0, 1.0, 1)])
            _write_ramp(node, "rotationRamp", [(0.0, 0.0, 1), (1.0, 0.0, 1)])
            for strand in range(3):
                transform = cmds.createNode("transform", name="strand%d" % (strand + 1), parent=root)
                shape = cmds.createNode("mesh", name="strand%dShape" % (strand + 1), parent=transform)
                cmds.connectAttr("%s.outMesh[%d]" % (node, strand), shape + ".inMesh", force=True)
                cmds.sets(shape, edit=True, forceElement="initialShadingGroup")
            result.append(node)
        roots = [asset_root(node) for node in result if asset_root(node)]
        if roots:
            cmds.select(roots, replace=True)
    from . import lifecycle
    lifecycle.refresh_registry()
    return _unique(result)


def strand_controls(node):
    """Return the three message-owned controls in strand order, or none."""
    node = primary_generator(node)
    if not cmds.attributeQuery("strandControls", node=node, exists=True):
        return []
    indices = cmds.getAttr(node + ".strandControls", multiIndices=True) or []
    connected = {}
    for index in indices:
        values = cmds.listConnections("%s.strandControls[%d]" % (node, index),
                                      source=True, destination=False) or []
        if values:
            if index not in (0, 1, 2) or len(values) != 1:
                raise ValueError("分股控制曲线归属不明确: %s" % node)
            control = _long(values[0])
            if (cmds.nodeType(control) != "transform"
                    or not cmds.attributeQuery("braidToolsGenerator", node=control, exists=True)
                    or not cmds.isConnected(node + ".message", control + ".braidToolsGenerator")):
                raise ValueError("分股控制曲线归属连接已损坏: %s" % control)
            connected[index] = control
    if not connected:
        if geometry_generator(node) != node:
            raise ValueError("分股控制曲线已缺失: %s" % node)
        return []
    if set(connected) != {0, 1, 2} or len(set(connected.values())) != 3:
        raise ValueError("需要三条独立的分股控制曲线；请撤销误删除或恢复文件: %s" % node)
    return [connected[index] for index in range(3)]


def _control_connections(node, driver, controls):
    """Canonical owned graph, used to reject unexplained extra dependencies."""
    result = {
        (node + ".message", driver + ".braidToolsControlOwner"),
        (node + ".message", driver + ".sourceCurve"),
        (driver + ".message", node + ".strandControlDriver"),
    }
    for attribute in _CONTROL_FORWARD:
        result.add((node + "." + attribute, driver + "." + attribute))
    for index, control in enumerate(controls):
        shape = _curve_shape(control)
        result.update({
            (node + ".message", control + ".braidToolsGenerator"),
            (control + ".message", "%s.strandControls[%d]" % (node, index)),
            ("%s.outControlCurves[%d]" % (node, index), shape + ".create"),
            ("%s.outControlCurves[%d]" % (node, index), "%s.strandRestCurves[%d]" % (driver, index)),
            (shape + ".worldSpace[0]", "%s.strandControlCurves[%d]" % (driver, index)),
        })
    return result


def _connection_key(plug):
    """Canonical full DAG path and long attribute names from an API plug."""
    return _delete_name(plug.node()) + "." + plug.partialName(
        includeNodeName=False, includeNonMandatoryIndices=True,
        includeInstancedIndices=True, useAlias=False, useFullAttributePath=True,
        useLongNames=True)


def _validated_control_graph(node):
    """Validate the owned graph without changing it; used before resets/delete."""
    node = primary_generator(node)
    driver = geometry_generator(node)
    controls = strand_controls(node)
    if driver == node:
        if controls:
            raise ValueError("分股曲线存在但辅助节点缺失: %s" % node)
        return None
    if not controls or not cmds.getAttr(driver + ".strandControlsEnabled"):
        raise ValueError("分股控制没有完整启用: %s" % node)
    expected = _control_connections(node, driver, controls)
    for source, destination in expected:
        if not cmds.isConnected(source, destination):
            raise ValueError("分股控制连接已改变，请先恢复连接: %s → %s" % (source, destination))
    root = asset_root(node)
    if not root:
        raise ValueError("分股控制资产组缺失: %s" % node)
    for control in controls:
        if (cmds.listRelatives(control, parent=True, fullPath=True) or []) != [root]:
            raise ValueError("分股控制曲线已移出原资产组，请恢复层级: %s" % control)
    return driver, controls, expected


def _enable_meshes(node, root):
    meshes = {}
    for plug, other in _delete_connections(node):
        attribute = om.MFnAttribute(plug.attribute()).name
        if attribute == "message":
            continue
        if attribute != "outMesh" or not plug.isElement or plug.logicalIndex() not in (0, 1, 2):
            raise ValueError("创建分股控制前需先处理生成器的额外下游连接: %s" % plug.name())
        mesh = _delete_name(other.node())
        index = plug.logicalIndex()
        if (cmds.nodeType(mesh) != "mesh" or om.MFnAttribute(other.attribute()).name != "inMesh"
                or index in meshes or not mesh.startswith(root + "|")):
            raise ValueError("需要三个直接连接的原始辫子网格，不能自动替换用户添加的历史: %s" % node)
        _delete_check_editable(mesh)
        parent = (cmds.listRelatives(mesh, parent=True, fullPath=True) or [None])[0]
        if parent:
            _delete_check_editable(parent)
        meshes[index] = mesh
    if set(meshes) != {0, 1, 2}:
        raise ValueError("需要完整的三股实时网格才能创建分股控制: %s" % node)
    return meshes


def enable_strand_controls(nodes, samples=32):
    """Create an acyclic baseline -> CV-tweak -> deformation graph, undoably.

    Existing controls are reused without changing their resolution or edits.
    ``samples`` fixes the number of editable CVs at first creation (4..256).
    """
    nodes = _generators(nodes)
    if not nodes:
        return []
    if isinstance(samples, bool) or int(samples) != samples or not 4 <= int(samples) <= 256:
        raise ValueError("分股控制点数量必须为 4 到 256 的整数。")
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("创建分股控制需要启用 Maya 撤销。")
    pending = []
    for node in nodes:
        if not cmds.attributeQuery("outControlCurves", node=node, exists=True):
            raise ValueError("当前加载的插件不支持分股控制，请保存工作并重启 Maya 后加载新版。")
        graph = _validated_control_graph(node)
        if graph:
            continue
        root = _delete_root(node)
        if not root or not source_transform(node):
            raise ValueError("请先修复资产组和源曲线: %s" % node)
        _delete_check_editable(node)
        _delete_check_editable(root)
        sample_plug = node + ".strandControlSamples"
        if (cmds.listConnections(sample_plug, source=True, destination=False)
                or not cmds.getAttr(sample_plug, settable=True)):
            raise ValueError("控制点数量受外部连接驱动或不可编辑，请先处理该连接: %s" % sample_plug)
        meshes = _enable_meshes(node, root)
        # Evaluate first, so invalid input cannot leave a half-created rig.
        _plug(node, "outMesh").elementByLogicalIndex(0).asMObject()
        if cmds.getAttr(node + ".outStatus") != "OK":
            raise ValueError(cmds.getAttr(node + ".outStatus"))
        pending.append((node, root, meshes))
    changed = False
    try:
        with undo_chunk("BraidTools 创建分股控制曲线"):
            for node, root, meshes in pending:
                # Creating a node is a guaranteed undoable mutation. Mark only
                # after it succeeds, so a failed first command cannot undo the
                # user's preceding operation through an empty undo chunk.
                driver = cmds.createNode(NODE_TYPE, name=_label(node) + "_StrandAdjust#")
                changed = True
                cmds.setAttr(node + ".strandControlSamples", int(samples))
                if not cmds.attributeQuery("strandControlDriver", node=node, exists=True):
                    cmds.addAttr(node, longName="strandControlDriver", attributeType="message")
                if not cmds.attributeQuery("strandControls", node=node, exists=True):
                    cmds.addAttr(node, longName="strandControls", attributeType="message", multi=True)
                cmds.addAttr(driver, longName="braidToolsControlOwner", attributeType="message")
                cmds.connectAttr(node + ".message", driver + ".braidToolsControlOwner")
                cmds.connectAttr(driver + ".message", node + ".strandControlDriver")
                cmds.connectAttr(node + ".message", driver + ".sourceCurve")
                for attribute in _CONTROL_FORWARD:
                    # Connecting compound ramps also forwards future entries.
                    cmds.connectAttr(node + "." + attribute, driver + "." + attribute, force=True)
                for index in range(3):
                    control = cmds.createNode("transform", name="strand%d_control" % (index + 1), parent=root)
                    control = _long(control)
                    cmds.addAttr(control, longName="braidToolsGenerator", attributeType="message")
                    cmds.connectAttr(node + ".message", control + ".braidToolsGenerator")
                    cmds.connectAttr(control + ".message", "%s.strandControls[%d]" % (node, index))
                    shape = cmds.createNode("nurbsCurve", name="strand%d_controlShape" % (index + 1), parent=control)
                    shape = _long(shape)
                    cmds.connectAttr("%s.outControlCurves[%d]" % (node, index), shape + ".create")
                    cmds.connectAttr("%s.outControlCurves[%d]" % (node, index),
                                     "%s.strandRestCurves[%d]" % (driver, index))
                    cmds.connectAttr(shape + ".worldSpace[0]", "%s.strandControlCurves[%d]" % (driver, index))
                    cmds.setAttr(shape + ".overrideEnabled", True)
                    cmds.setAttr(shape + ".overrideColor", (13, 14, 6)[index])
                cmds.setAttr(driver + ".strandControlsEnabled", True)
                _plug(driver, "outMesh").elementByLogicalIndex(0).asMObject()
                if cmds.getAttr(driver + ".outStatus") != "OK":
                    raise ValueError(cmds.getAttr(driver + ".outStatus"))
                for index, mesh in meshes.items():
                    cmds.connectAttr("%s.outMesh[%d]" % (driver, index), mesh + ".inMesh", force=True)
            controls = [control for node in nodes for control in strand_controls(node)]
            if controls:
                cmds.select(controls, replace=True)
    except Exception:
        if changed:
            cmds.undo()  # Exactly this closed operation chunk; no older work.
        raise
    from . import lifecycle
    lifecycle.refresh_registry()
    return nodes


def reset_strand_controls(nodes, strand=None):
    """Clear owned CV tweaks and control-transform offsets, preserving the rig."""
    nodes = _generators(nodes)
    if strand is not None and (isinstance(strand, bool) or strand not in (0, 1, 2)):
        raise ValueError("股编号必须为 0、1、2 或 None（全部）。")
    targets = []
    for node in nodes:
        graph = _validated_control_graph(node)
        if not graph:
            raise ValueError("请先创建分股控制曲线: %s" % node)
        unused_driver, controls, unused_expected = graph
        for index in range(3) if strand is None else [int(strand)]:
            control = controls[index]
            shape = _curve_shape(control)
            for target in (control, shape):
                _delete_check_editable(target)
            for attribute in ("translate", "rotate", "scale", "shear", "rotatePivot", "scalePivot",
                              "rotatePivotTranslate", "scalePivotTranslate", "rotateAxis", "offsetParentMatrix",
                              "inheritsTransform"):
                if cmds.attributeQuery(attribute, node=control, exists=True):
                    if cmds.listConnections(control + "." + attribute, source=True, destination=False):
                        raise ValueError("控制曲线变换受外部连接驱动，不能重置: %s.%s" % (control, attribute))
            if cmds.listConnections(shape + ".controlPoints", source=True, destination=False):
                raise ValueError("控制点受外部连接驱动，不能重置: %s" % shape)
            targets.append((control, shape))
    with undo_chunk("BraidTools 重置分股控制"):
        for control, shape in targets:
            # A connected curve's controlPoints are additive tweaks, not CVs.
            for index in cmds.getAttr(shape + ".controlPoints", multiIndices=True) or []:
                cmds.setAttr("%s.controlPoints[%d]" % (shape, index), 0.0, 0.0, 0.0, type="double3")
            for attribute in ("translate", "rotate", "shear", "rotatePivot", "scalePivot",
                              "rotatePivotTranslate", "scalePivotTranslate", "rotateAxis"):
                cmds.setAttr(control + "." + attribute, 0.0, 0.0, 0.0, type="double3")
            cmds.setAttr(control + ".scale", 1.0, 1.0, 1.0, type="double3")
            cmds.setAttr(control + ".inheritsTransform", True)
            if cmds.attributeQuery("offsetParentMatrix", node=control, exists=True):
                cmds.setAttr(control + ".offsetParentMatrix", *tuple(om.MMatrix()), type="matrix")
    return nodes


def _delete_name(obj):
    """Resolve an object identity without passing a DAG short name to cmds."""
    if obj.hasFn(om.MFn.kDagNode):
        paths = om.MDagPath.getAllPathsTo(obj)
        if len(paths) != 1:
            raise ValueError("删除已取消：连接对象包含实例或无有效路径，不能确定独占归属。")
        return paths[0].fullPathName()
    return om.MFnDependencyNode(obj).name()


def _delete_check_editable(node):
    """Check every planned node before the first destructive command."""
    if cmds.referenceQuery(node, isNodeReferenced=True):
        raise ValueError("删除已取消：资产包含引用节点，请先处理引用：%s" % node)
    if any(cmds.lockNode(node, query=True, lock=True) or []) or cmds.listAttr(node, locked=True):
        raise ValueError("删除已取消：资产节点或属性已锁定：%s" % node)
    selection = om.MSelectionList()
    selection.add(node)
    obj = selection.getDependNode(0)
    if obj.hasFn(om.MFn.kDagNode) and len(om.MDagPath.getAllPathsTo(obj)) != 1:
        raise ValueError("删除已取消：资产包含实例，不能确定独占归属：%s" % node)


def _delete_connections(node, outgoing=True):
    selection = om.MSelectionList()
    selection.add(node)
    fn = om.MFnDependencyNode(selection.getDependNode(0))
    result = []
    for plug in fn.getConnections():
        for other in plug.connectedTo(not outgoing, outgoing):
            result.append((plug, other))
    return result


def _delete_root(node):
    """Use either surviving message link, never a name or guessed ancestor."""
    graph = _validated_control_graph(node)
    control_connections = graph[2] if graph else set()
    linked = [_delete_name(plug.node()) for plug in _plug(node, "assetRoot").connectedTo(True, False)]
    back_links = []
    for plug, other in _delete_connections(node):
        if om.MFnAttribute(plug.attribute()).name != "message":
            continue
        if (_connection_key(plug), _connection_key(other)) in control_connections:
            continue
        if om.MFnAttribute(other.attribute()).name != "braidToolsGenerator":
            raise ValueError("删除已取消：生成器有额外对象连接，归属不明确：%s" % node)
        other_node = _delete_name(other.node())
        if cmds.nodeType(other_node) != "transform":
            raise ValueError("删除已取消：资产根连接不是 transform：%s" % node)
        back_links.append(other_node)
    roots = _unique(linked + back_links)
    if len(roots) > 1:
        raise ValueError("删除已取消：生成器关联了多个资产根，归属不明确：%s" % node)
    if not roots:
        return None
    root = roots[0]
    if cmds.nodeType(root) != "transform":
        raise ValueError("删除已取消：资产根不是 transform：%s" % root)
    marker_owners = []
    if cmds.attributeQuery("braidToolsGenerator", node=root, exists=True):
        marker_owners = [_delete_name(plug.node()) for plug in _plug(root, "braidToolsGenerator").connectedTo(True, False)]
    root_owners = []
    for plug, other in _delete_connections(root):
        if (om.MFnAttribute(plug.attribute()).name == "message"
                and om.MFnAttribute(other.attribute()).name == "assetRoot"):
            root_owners.append(_delete_name(other.node()))
    owners = _unique(marker_owners + root_owners)
    if any(owner != node for owner in owners):
        raise ValueError("删除已取消：资产根被其他生成器共用：%s" % root)
    return root


def _delete_cleanup_transform(transform, allowed_nodes, is_root=False, is_control=False):
    """Only empty-after-delete transforms without attached user data qualify."""
    attributes = cmds.listAttr(transform, userDefined=True) or []
    permitted = {"braidToolsGenerator"} if is_root or is_control else set()
    if set(attributes) - permitted:
        return False
    for outgoing in (True, False):
        for plug, other in _delete_connections(transform, outgoing=outgoing):
            other_node = _delete_name(other.node())
            if other_node not in allowed_nodes:
                return False
    return True


def delete_assets(nodes):
    """Delete exactly the supplied generators and their unambiguous live meshes.

    Returns the deleted generator names. Source curves, independent snapshots,
    other generators and foreign children are preserved. A damaged asset can
    lose either/both root links; when ownership evidence is missing, empty
    transforms are kept. Locks, references, instances, shared outputs and
    unexpected downstream dependencies reject the whole batch before editing.
    """
    nodes = _generators(nodes)
    if not nodes:
        return []
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("删除已取消：Maya 撤销当前未启用。请先启用撤销，再删除资产。")
    generators = set(nodes)
    meshes = set()
    control_shapes = set()
    control_transforms = set()
    cleanup = set()
    roots = set()
    protected_sources = {_delete_name(plug.node()) for node in nodes
                         for plug in _plug(node, "sourceCurve").connectedTo(True, False)}
    for node in nodes:
        _delete_check_editable(node)
        graph = _validated_control_graph(node)
        internal = set()
        stages = [node]
        if graph:
            driver, controls, internal = graph
            generators.add(driver)
            stages.append(driver)
            _delete_check_editable(driver)
            for outgoing in (True, False):
                for plug, other in _delete_connections(driver, outgoing=outgoing):
                    pair = ((_connection_key(plug), _connection_key(other)) if outgoing
                            else (_connection_key(other), _connection_key(plug)))
                    if pair in internal:
                        continue
                    if outgoing and om.MFnAttribute(plug.attribute()).name == "outMesh":
                        continue  # Full mesh ownership checks below.
                    raise ValueError("删除已取消：分股辅助节点有额外连接：%s" % other.name())
            for control in controls:
                _delete_check_editable(control)
                shape = _curve_shape(control)
                _delete_check_editable(shape)
                for outgoing in (True, False):
                    for plug, other in _delete_connections(shape, outgoing=outgoing):
                        pair = ((_connection_key(plug), _connection_key(other)) if outgoing
                                else (_connection_key(other), _connection_key(plug)))
                        if pair not in internal:
                            raise ValueError("删除已取消：分股控制曲线有额外历史或外部连接：%s" % other.name())
                control_shapes.add(shape)
                control_transforms.add(control)
                cleanup.add(control)
        root = _delete_root(node)
        if root:
            _delete_check_editable(root)
            roots.add(root)
        by_output = {}
        for stage in stages:
            for plug, other in _delete_connections(stage):
                attribute = om.MFnAttribute(plug.attribute()).name
                if (_connection_key(plug), _connection_key(other)) in internal:
                    continue
                if attribute == "message":
                    continue  # Already checked by _delete_root.
                if attribute != "outMesh" or not plug.isElement or plug.logicalIndex() not in (0, 1, 2):
                    raise ValueError("删除已取消：生成器有额外下游连接，请先断开或烘焙：%s" % plug.name())
                other_node = _delete_name(other.node())
                if cmds.nodeType(other_node) != "mesh" or om.MFnAttribute(other.attribute()).name != "inMesh":
                    raise ValueError("删除已取消：网格输出含额外历史或不明确对象：%s" % other.name())
                index = plug.logicalIndex()
                if index in by_output and by_output[index] != other_node:
                    raise ValueError("删除已取消：同一股输出被多个网格共用：%s" % plug.name())
                by_output[index] = other_node
        for mesh in by_output.values():
            _delete_check_editable(mesh)
            if root and not mesh.startswith(root + "|"):
                raise ValueError("删除已取消：输出网格已移到资产根之外，归属需确认：%s" % mesh)
            for plug, other in _delete_connections(mesh):
                if om.MFnAttribute(plug.attribute()).name in {"outMesh", "worldMesh", "outSmoothMesh"}:
                    raise ValueError("删除已取消：生成网格仍被外部几何使用：%s" % other.name())
            # A locked parent can prevent DAG edits even when it is preserved.
            parent = (cmds.listRelatives(mesh, parent=True, fullPath=True) or [None])[0]
            if parent:
                _delete_check_editable(parent)
                if root and (cmds.listRelatives(parent, parent=True, fullPath=True) or []) == [root]:
                    cleanup.add(parent)
            meshes.add(mesh)
    planned = generators | meshes | control_shapes
    # Clean only immediate strand transforms proved by an output connection.
    # Arbitrary inserted groups and unidentifiable empty children stay intact.
    for transform in sorted(cleanup, key=lambda value: value.count("|"), reverse=True):
        children = set(cmds.listRelatives(transform, children=True, fullPath=True) or [])
        if (transform not in protected_sources and children <= planned
                and _delete_cleanup_transform(transform, planned, is_control=transform in control_transforms)):
            planned.add(transform)
    for root in roots:
        children = set(cmds.listRelatives(root, children=True, fullPath=True) or [])
        if (root not in protected_sources and children <= planned
                and _delete_cleanup_transform(root, planned, is_root=True)):
            planned.add(root)
    for node in planned:
        _delete_check_editable(node)
        # A locked connection on a preserved endpoint can block disconnection.
        for outgoing in (True, False):
            for plug, other in _delete_connections(node, outgoing=outgoing):
                if other.isLocked:
                    raise ValueError("删除已取消：相连属性已锁定：%s" % other.name())
    # One native delete command, without inputConnectionsAndNodes or history
    # deletion. A parent is included only if every child is also in this plan.
    targets = [node for node in planned
               if not any(node.startswith(parent + "|") for parent in planned if parent.startswith("|"))]
    with undo_chunk("BraidTools 删除资产"):
        cmds.delete(targets)
    from . import lifecycle
    lifecycle.refresh_registry()
    return nodes


def _numeric_value(name, value):
    if isinstance(value, bool):
        raise ValueError("参数需要数值: %s" % name)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("参数必须是有限数值: %s" % name)
    if name in INTEGER_PARAMETERS:
        if value != int(value):
            raise ValueError("参数需要整数: %s" % name)
        value = int(value)
    return value


def _check_limits(nodes, name, value):
    for node in nodes:
        if cmds.attributeQuery(name, node=node, minExists=True):
            lower = cmds.attributeQuery(name, node=node, minimum=True)[0]
            if value < lower:
                raise ValueError("%s 必须 >= %s" % (name, lower))
        if cmds.attributeQuery(name, node=node, maxExists=True):
            upper = cmds.attributeQuery(name, node=node, maximum=True)[0]
            if value > upper:
                raise ValueError("%s 必须 <= %s" % (name, upper))
    if name == "longitudinalMode" and value not in (0, 1):
        raise ValueError("longitudinalMode 必须为 0 或 1。")
    if name == "guideSamples" and value != 0 and value < 16:
        raise ValueError("guideSamples 必须为 0（自动）或至少 16。")


def set_parameter(nodes, name, value):
    """Change exactly one named property on the addressed generators."""
    nodes = _generators(nodes)
    if name == "densityMode":
        return set_density_mode(nodes, value)
    if name in ("sizeRamp", "rotationRamp"):
        return set_ramp(nodes, name, value)
    if name not in PARAMETERS:
        raise ValueError("不支持的可编辑参数: %s" % name)
    value = _numeric_value(name, value)
    _check_limits(nodes, name, value)
    with undo_chunk("BraidTools 调整 " + name):
        for node in nodes:
            cmds.setAttr(node + "." + name, value)
    return nodes


def set_density_mode(nodes, mode):
    """Convert current measured length before switching, preserving the pattern."""
    nodes = _generators(nodes)
    if isinstance(mode, str):
        mapping = {"pitch": 0, "fixedCycles": 1}
        if mode not in mapping:
            raise ValueError("密度模式应为 pitch 或 fixedCycles。")
        mode = mapping[mode]
    if mode not in (0, 1):
        raise ValueError("密度模式应为 0 或 1。")
    pending = []
    for node in nodes:
        if cmds.getAttr(node + ".densityMode") == mode:
            continue
        length = cmds.getAttr(node + ".outLength")
        cycles = cmds.getAttr(node + ".outCycles")
        if length <= 1e-8 or cycles <= 1e-8:
            raise ValueError("请先修复输入曲线，再切换密度模式: %s" % node)
        _check_limits([node], "cycles" if mode == 1 else "pitch", cycles if mode == 1 else length / cycles)
        pending.append((node, length, cycles))
    with undo_chunk("BraidTools 切换密度模式"):
        for node, length, cycles in pending:
            if mode == 1:
                cmds.setAttr(node + ".cycles", cycles)
            else:
                cmds.setAttr(node + ".pitch", length / cycles)
            cmds.setAttr(node + ".densityMode", mode)
    return nodes


def _ramp_children(node, name):
    children = cmds.attributeQuery(name, node=node, listChildren=True) or []
    if len(children) != 3:
        raise RuntimeError("Ramp 结构无效: %s.%s" % (node, name))
    return children


def get_ramp(node, name):
    if name not in ("sizeRamp", "rotationRamp"):
        raise ValueError("未知 Ramp: " + name)
    children = _ramp_children(node, name)
    result = []
    for index in cmds.getAttr(node + "." + name, multiIndices=True) or []:
        prefix = "%s.%s[%d]." % (node, name, index)
        result.append(tuple(cmds.getAttr(prefix + child) for child in children))
    return sorted(result)


def sample_ramp(node, name, count=129):
    """Read Maya's actual ramp interpolation for the panel's curve graph."""
    if name not in ("sizeRamp", "rotationRamp"):
        raise ValueError("未知 Ramp: " + name)
    ramp = om.MRampAttribute(_plug(node, name))
    count = max(2, int(count))
    return [(index / float(count - 1), ramp.getValueAtPosition(index / float(count - 1)))
            for index in range(count)]


def _validated_ramp(name, entries):
    result = []
    for entry in entries:
        if len(entry) not in (2, 3):
            raise ValueError("Ramp 项应为 (位置, 值[, 插值])。")
        position, value = float(entry[0]), float(entry[1])
        interpolation = int(entry[2]) if len(entry) == 3 else 1
        if not (math.isfinite(position) and math.isfinite(value)) or not 0.0 <= position <= 1.0:
            raise ValueError("Ramp 位置必须在 0 到 1，值必须有限。")
        if name == "sizeRamp" and value < 0.0:
            raise ValueError("尺寸 Ramp 值不可为负。")
        if interpolation not in (0, 1, 2, 3):
            raise ValueError("插值应为 0=无、1=线性、2=平滑、3=样条。")
        result.append((position, value, interpolation))
    if not result:
        raise ValueError("Ramp 至少需要一个点。")
    return sorted(result)


def _write_ramp(node, name, entries):
    children = _ramp_children(node, name)
    for index in cmds.getAttr(node + "." + name, multiIndices=True) or []:
        cmds.removeMultiInstance("%s.%s[%d]" % (node, name, index), b=True)
    for index, entry in enumerate(entries):
        for child, value in zip(children, entry):
            cmds.setAttr("%s.%s[%d].%s" % (node, name, index, child), value)


def set_ramp(nodes, name, entries):
    """Copy only the named ramp to the addressed generators, undoably."""
    nodes = _generators(nodes)
    if name not in ("sizeRamp", "rotationRamp"):
        raise ValueError("未知 Ramp: " + name)
    entries = _validated_ramp(name, entries)
    with undo_chunk("BraidTools 调整 " + name):
        for node in nodes:
            _write_ramp(node, name, entries)
    return nodes


def rebuild_sources(nodes, spans, history=True):
    """Run Maya's Rebuild Curve on registered sources and reconnect new shapes."""
    nodes = _generators(nodes)
    if int(spans) != spans or int(spans) < 1 or int(spans) > 8192:
        raise ValueError("曲线跨数必须为 1 到 8192 的整数。")
    sources = _unique([source_transform(node) for node in nodes])
    if not all(sources):
        raise ValueError("源曲线已删除；请先重新绑定。")
    for source in sources:
        _curve_shape(source)
    with undo_chunk("BraidTools 重建源曲线"):
        for source in sources:
            cmds.rebuildCurve(source, constructionHistory=bool(history), replaceOriginal=True,
                              rebuildType=0, endKnots=1, keepRange=0, keepControlPoints=False,
                              keepEndPoints=True, keepTangents=False, spans=int(spans), degree=3)
        for node in nodes:
            _connect_curve(node, source_transform(node), connect_identity=False)
    from . import lifecycle
    lifecycle.refresh_registry()
    return nodes


def rebind(node, curve):
    """Explicitly assign a new source transform; source names are never guessed."""
    node = _generators([node])[0]
    _curve_shape(curve)
    with undo_chunk("BraidTools 重新绑定"):
        _connect_curve(node, curve)
    from . import lifecycle
    lifecycle.refresh_registry()
    return node


def _plug(node, attribute):
    selection = om.MSelectionList()
    selection.add(node)
    return om.MFnDependencyNode(selection.getDependNode(0)).findPlug(attribute, False)


def _guide_data(node, index, matrix):
    node = geometry_generator(node)
    data = _plug(node, "outGuideCurves").elementByLogicalIndex(index).asMObject()
    status = cmds.getAttr(node + ".outStatus")
    if status != "OK":
        raise RuntimeError(status)
    if data.isNull():
        raise RuntimeError("无法计算导向线: %s" % node)
    try:
        fn = om.MFnNurbsCurve(data)
    except RuntimeError as exc:
        raise RuntimeError("导向线数据为空或无效，请检查曲线和采样设置: %s" % node) from exc
    points = [point * matrix for point in fn.cvPositions(om.MSpace.kObject)]
    return [(point.x, point.y, point.z) for point in points], fn.degree, list(fn.knots())


def _validate_root_disk(points, node, strand):
    """Reject collapsed/tiny root rings before any snapshot DAG is created."""
    center = points[0]
    vectors = [tuple(point[axis] - center[axis] for axis in range(3)) for point in points[1:]]
    area_vector = [0.0, 0.0, 0.0]
    for first, second in zip(vectors, vectors[1:] + vectors[:1]):
        area_vector[0] += first[1] * second[2] - first[2] * second[1]
        area_vector[1] += first[2] * second[0] - first[0] * second[2]
        area_vector[2] += first[0] * second[1] - first[1] * second[0]
    radius_squared = max(sum(value * value for value in vector) for vector in vectors)
    double_area = math.sqrt(sum(value * value for value in area_vector))
    if not (math.isfinite(radius_squared) and math.isfinite(double_area)) or radius_squared <= 1e-12 or double_area <= 1e-12:
        raise ValueError("%s 第 %d 股的根部面退化或尺寸过小；请增大尺寸 Ramp 的根部值，或关闭“创建 3 个 UV 根部面”。"
                         % (node, strand + 1))


def _make_root_disk(points, parent, name):
    # Native guide order is center, followed by eight points around its radius.
    ring = points[1:]
    disk = cmds.polyCreateFacet(point=ring, constructionHistory=False, name=name)[0]
    disk = cmds.parent(disk, parent, absolute=True)[0]
    disk = _long(disk)
    cmds.polyPoke(disk + ".f[0]", constructionHistory=False)
    # Assign radial UVs by vertex position; the disk remains a static mesh.
    expected = ring + [points[0]]
    vertices = cmds.ls(disk + ".vtx[*]", flatten=True) or []
    for vertex in vertices:
        position = cmds.pointPosition(vertex, world=True)
        nearest = min(range(9), key=lambda i: sum((position[j] - expected[i][j]) ** 2 for j in range(3)))
        if nearest == 8:
            u, v = 0.5, 0.5
            cmds.xform(vertex, worldSpace=True, translation=points[0])
        else:
            angle = 2.0 * math.pi * nearest / 8.0
            u, v = 0.5 + 0.5 * math.cos(angle), 0.5 + 0.5 * math.sin(angle)
        uvs = cmds.ls(cmds.polyListComponentConversion(vertex, fromVertex=True, toUV=True), flatten=True) or []
        if uvs:
            cmds.polyEditUV(uvs, relative=False, uValue=u, vValue=v)
    return disk


def extract_guides(nodes, root_surfaces=True, samples=0):
    """Create 27 independent curves per braid, optionally three UV root disks.

    samples=0 uses the node's guideSamples setting (whose default is automatic).
    A positive override is temporary and does not change the live asset.
    """
    nodes = _generators(nodes)
    samples = _numeric_value("guideSamples", samples)
    _check_limits(nodes, "guideSamples", samples)
    result = []
    with undo_chunk("BraidTools 提取 XGen 导向线"):
        prepared = []
        # Resolve every source and validate all requested root surfaces before
        # creating a single snapshot group, including multi-asset extraction.
        for node in nodes:
            root = asset_root(node)
            if not root or not source_transform(node):
                raise ValueError("资产或源曲线缺失: %s" % node)
            matrix = _dag_path(root).inclusiveMatrix()
            previous_samples = cmds.getAttr(node + ".guideSamples")
            try:
                if samples:
                    cmds.setAttr(node + ".guideSamples", samples)
                if cmds.getAttr(node + ".outStatus") != "OK":
                    raise ValueError(cmds.getAttr(node + ".outStatus"))
                curves = [_guide_data(node, index, matrix) for index in range(27)]
            finally:
                if samples:
                    cmds.setAttr(node + ".guideSamples", previous_samples)
            guide_roots = [points[0] for points, degree, knots in curves]
            if root_surfaces:
                for strand in range(3):
                    _validate_root_disk(guide_roots[strand * 9:strand * 9 + 9], node, strand)
            prepared.append((root, curves, guide_roots))
        unparented = []
        try:
            for root, curves, guide_roots in prepared:
                group = cmds.createNode("transform", name=_label(root) + "_XGenGuides#")
                group = _long(group)
                result.append(group)
                guides_group = cmds.createNode("transform", name="guides", parent=group)
                strand_groups = [cmds.createNode("transform", name="strand%d" % (strand + 1), parent=guides_group)
                                 for strand in range(3)]
                for index, (points, degree, knots) in enumerate(curves):
                    curve = cmds.curve(point=points, degree=degree, knot=knots,
                                       name="strand%d_guide%02d" % (index // 9 + 1, index % 9 + 1))
                    unparented.append(_long(curve))
                    cmds.parent(curve, strand_groups[index // 9], absolute=True)
                    unparented.pop()
                if root_surfaces:
                    surfaces_group = cmds.createNode("transform", name="root_surfaces", parent=group)
                    for strand in range(3):
                        _make_root_disk(guide_roots[strand * 9:strand * 9 + 9], surfaces_group,
                                        "strand%d_root" % (strand + 1))
        except Exception:
            cleanup = [node for node in result + unparented if cmds.objExists(node)]
            if cleanup:
                cmds.delete(cleanup)
            raise
        if result:
            cmds.select(result, replace=True)
    return result


def bake(nodes):
    """Duplicate the evaluated strand meshes into independent static groups."""
    nodes = _generators(nodes)
    result = []
    with undo_chunk("BraidTools 烘焙网格"):
        for node in nodes:
            root = asset_root(node)
            if not root:
                raise ValueError("资产组缺失: %s" % node)
            geometry = geometry_generator(node)
            if cmds.getAttr(geometry + ".outStatus") != "OK":
                raise ValueError(cmds.getAttr(geometry + ".outStatus"))
            output = cmds.createNode("transform", name=_label(root) + "_Baked#")
            output = _long(output)
            for index in range(3):
                destinations = cmds.listConnections("%s.outMesh[%d]" % (geometry, index),
                                                    source=False, destination=True, shapes=True, type="mesh") or []
                if not destinations:
                    raise ValueError("输出网格缺失: %s strand %d" % (node, index + 1))
                shape = _long(destinations[0])
                transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [])[0]
                duplicate = cmds.duplicate(transform, returnRootsOnly=True, inputConnections=False,
                                           upstreamNodes=False, name="strand%d_baked" % (index + 1))[0]
                duplicate = cmds.parent(duplicate, output, absolute=True)[0]
                cmds.delete(duplicate, constructionHistory=True)
            result.append(output)
        if result:
            cmds.select(result, replace=True)
    return result
