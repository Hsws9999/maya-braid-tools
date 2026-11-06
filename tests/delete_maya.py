"""Targeted delete-assets tests. Run only in a fresh isolated mayapy process.

Requires BRAIDTOOLS_ISOLATED_TEST=1 and MAYA_APP_DIR equal to --profile, a
dedicated child of --output. No connection to an existing Maya is supported.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", required=True)
    return parser.parse_args()


ARGS = arguments()
PACKAGE = Path(ARGS.package).resolve()
OUT = Path(ARGS.output).resolve()
PROFILE = Path(ARGS.profile).resolve()
if (os.environ.get("BRAIDTOOLS_ISOLATED_TEST") != "1"
        or Path(os.environ.get("MAYA_APP_DIR", "")).resolve() != PROFILE
        or PROFILE == OUT or os.path.commonpath([str(OUT), str(PROFILE)]) != str(OUT)
        or Path(sys.executable).name.lower() not in ("mayapy", "mayapy.exe")):
    raise RuntimeError("Use a NEW mayapy with an explicitly isolated MAYA_APP_DIR under --output.")
OUT.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)
os.environ["MAYA_SKIP_USERSETUP_PY"] = "1"
sys.path.insert(0, str(PACKAGE / "scripts"))

import maya.standalone
maya.standalone.initialize(name="python")
from maya import cmds, utils
from maya.api import OpenMaya as om
from braidtools import api

api.ensure_loaded()
REPORT = {"status": "running", "maya": cmds.about(version=True), "cases": []}
REPORT_PATH = OUT / "delete_results.json"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def identity(node):
    return (cmds.ls(node, uuid=True) or [None])[0]


def identities():
    return set(cmds.ls(uuid=True) or [])


def alive(node_id):
    return bool(cmds.ls(node_id, long=True))


def new_scene():
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    utils.processIdleEvents()


def asset(name="path", offset=0.0):
    source = cmds.curve(name=name, degree=3, point=[
        (offset, 0, 0), (offset + 1, 4, 0), (offset - 1, 8, 1), (offset, 12, 0)])
    generator = api.create([source])[0]
    root = api.asset_root(generator)
    meshes = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="mesh") or []
    return source, generator, root, meshes


def graph_points(generator):
    points = []
    for index in range(3):
        mesh = om.MFnMesh(api._plug(generator, "outMesh").elementByLogicalIndex(index).asMObject())
        points.append([(point.x, point.y, point.z) for point in mesh.getPoints()])
    return points


def refused_unchanged(nodes):
    before = identities()
    undo_name = cmds.undoInfo(query=True, undoName=True)
    try:
        api.delete_assets(nodes)
    except ValueError as exc:
        check("取消" in str(exc) or "生成" in str(exc), "Rejection must explain the issue.")
    else:
        raise AssertionError("Unsafe batch was not rejected.")
    check(identities() == before, "A rejected batch changed scene node identities.")
    check(cmds.undoInfo(query=True, undoName=True) == undo_name, "Preflight refusal added an undo step.")


def single_snapshots_undo_redo():
    source, node, root, meshes = asset()
    guides = api.extract_guides([node], root_surfaces=True)[0]
    baked = api.bake([node])[0]
    api.set_parameter([node], "size", 1.37)
    preserve = {identity(source), identity(guides), identity(baked)}
    preserve.update(identity(item) for group in (guides, baked)
                    for item in cmds.listRelatives(group, allDescendents=True, fullPath=True) or [])
    owned = {identity(item) for item in [node, root] + (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])}
    points = graph_points(node)
    result = api.delete_assets([node])
    check(result == [node], "Return value must contain the exact generator names.")
    check(all(not alive(item) for item in owned), "Live asset was not fully deleted.")
    check(all(alive(item) for item in preserve), "Source or independent snapshot was deleted.")
    cmds.undo()
    utils.processIdleEvents()
    check(all(alive(item) for item in owned), "One undo did not restore the whole asset.")
    check(cmds.getAttr(node + ".size") == 1.37 and graph_points(node) == points,
          "Undo did not restore connections, parameters and geometry.")
    cmds.redo()
    utils.processIdleEvents()
    check(all(not alive(item) for item in owned) and all(alive(item) for item in preserve), "Redo changed protected nodes.")


def multi_explicit_targets():
    first, second, third = asset("first"), asset("second", 4), asset("third", 8)
    protected = {identity(third[1]), identity(third[2]), identity(first[0]), identity(second[0])}
    result = api.delete_assets([first[1], second[1], first[1]])
    check(result == [first[1], second[1]], "Explicit batch was not deduplicated correctly.")
    check(not cmds.objExists(first[1]) and not cmds.objExists(second[1]), "Batch deletion missed a target.")
    check(all(alive(item) for item in protected), "Deletion widened to an unaddressed asset.")
    cmds.undo()
    check(cmds.objExists(first[1]) and cmds.objExists(second[1]), "Batch delete needs more than one undo.")
    cmds.redo()
    check(not cmds.objExists(first[1]) and not cmds.objExists(second[1]), "Batch redo failed.")


def shared_source():
    first, second = asset("sharedA"), asset("sharedB", 5)
    api.rebind(second[1], first[0])
    before = graph_points(second[1])
    cmds.select(first[0], replace=True)  # This resolves BOTH assets; the API must not use it.
    api.delete_assets([first[1]])
    check(cmds.objExists(second[1]) and cmds.objExists(first[0]) and cmds.objExists(second[0]),
          "Shared source or unaddressed generator was deleted.")
    check(graph_points(second[1]) == before, "Unaddressed shared-source geometry changed.")


def missing_source_and_root():
    first = asset("sourceMissing")
    cmds.delete(first[0])
    api.delete_assets([first[1]])
    check(not cmds.objExists(first[1]) and not cmds.objExists(first[2]), "Missing-source cleanup failed.")
    second = asset("rootMissing", 4)
    # A normal Maya root deletion may also remove its generator as history.
    # Deliberately detach the runtime output/root links to model a surviving
    # orphan generator, instead of asking the API to delete a stale name.
    for index in range(3):
        output = second[1] + ".outMesh[%d]" % index
        for destination in cmds.listConnections(output, source=False, destination=True, plugs=True) or []:
            cmds.disconnectAttr(output, destination)
    cmds.disconnectAttr(second[2] + ".message", second[1] + ".assetRoot")
    cmds.disconnectAttr(second[1] + ".message", second[2] + ".braidToolsGenerator")
    cmds.disconnectAttr(second[2] + ".worldInverseMatrix[0]", second[1] + ".outputParentInverseMatrix")
    cmds.delete(second[2])
    check(cmds.objExists(second[1]), "The orphan-generator fixture was not preserved.")
    api.delete_assets([second[1]])
    check(cmds.objExists(second[0]) and not cmds.objExists(second[1]), "Missing-root cleanup failed.")


def root_link_and_partial_mesh():
    source, node, root, meshes = asset()
    cmds.disconnectAttr(root + ".message", node + ".assetRoot")
    cmds.delete((cmds.listRelatives(meshes[0], parent=True, fullPath=True) or [])[0])
    api.delete_assets([node])
    check(cmds.objExists(source) and not cmds.objExists(root), "Surviving backlink did not identify the partial asset.")


def no_root_links_preserves_unknown_parents():
    source, node, root, meshes = asset()
    parents = [(cmds.listRelatives(mesh, parent=True, fullPath=True) or [])[0] for mesh in meshes]
    cmds.addAttr(parents[0], longName="userNote", dataType="string")
    cmds.setAttr(parents[0] + ".userNote", "keep me", type="string")
    cmds.disconnectAttr(root + ".message", node + ".assetRoot")
    cmds.disconnectAttr(node + ".message", root + ".braidToolsGenerator")
    keep = {identity(item) for item in [source, root] + parents}
    old_meshes = {identity(mesh) for mesh in meshes}
    api.delete_assets([node])
    check(all(alive(item) for item in keep), "Native shape deletion removed an unowned empty parent.")
    check(all(not alive(item) for item in old_meshes), "Connected meshes remained after deletion.")
    check(cmds.getAttr(parents[0] + ".userNote") == "keep me", "User transform metadata changed.")


def foreign_children_and_custom_transform():
    source, node, root, meshes = asset()
    parent = (cmds.listRelatives(meshes[0], parent=True, fullPath=True) or [])[0]
    cmds.addAttr(parent, longName="userData", attributeType="double", defaultValue=3.0)
    foreign = cmds.polyCube(name="userAttachedCube", constructionHistory=False)[0]
    foreign = cmds.parent(foreign, root, absolute=True)[0]
    extra = cmds.createNode("transform", name="userChild", parent=parent)
    keep = {identity(item) for item in [source, root, parent, foreign, extra]}
    keep.update(identity(item) for item in cmds.listRelatives(foreign, allDescendents=True, fullPath=True) or [])
    api.delete_assets([node])
    check(all(alive(item) for item in keep), "Foreign children or their ancestors were deleted.")
    check(cmds.getAttr(parent + ".userData") == 3.0, "Attached transform data changed.")


def inserted_and_external_ancestors():
    source, node, root, meshes = asset()
    mesh_id = identity(meshes[0])
    outside = cmds.createNode("transform", name="userOutside")
    root = cmds.parent(root, outside, absolute=True)[0]
    root = (cmds.ls(root, long=True) or [])[0]
    user_group = cmds.createNode("transform", name="userIntermediate", parent=root)
    shape = cmds.ls(mesh_id, long=True)[0]
    strand = (cmds.listRelatives(shape, parent=True, fullPath=True) or [])[0]
    strand = cmds.parent(strand, user_group, absolute=True)[0]
    keep = {identity(outside), identity(user_group), identity(strand)}
    api.delete_assets([node])
    check(all(alive(item) for item in keep), "Inserted user groups or unknown empty parents were removed.")
    # Separate case: the outside group has only an otherwise fully owned asset.
    other = asset("simple", 4)
    external = cmds.createNode("transform", name="singleChildUserAncestor")
    cmds.parent(other[2], external, absolute=True)
    external_id = identity(external)
    api.delete_assets([other[1]])
    check(alive(external_id), "Deleting a complete root removed its unowned external ancestor.")


def locked_batch():
    for kind in ("generator", "root", "mesh", "meshInput"):
        new_scene()
        first, second = asset("first"), asset("second", 4)
        target = {"generator": second[1], "root": second[2], "mesh": second[3][0]}[kind] if kind != "meshInput" else second[3][0]
        if kind == "meshInput":
            cmds.setAttr(target + ".inMesh", lock=True)
        else:
            cmds.lockNode(target, lock=True)
        refused_unchanged([first[1], second[1]])


def instances_and_shared_outputs():
    first, second = asset("first"), asset("second", 4)
    parent = (cmds.listRelatives(second[3][0], parent=True, fullPath=True) or [])[0]
    cmds.instance(parent)
    refused_unchanged([first[1], second[1]])
    new_scene()
    first, second = asset("first"), asset("second", 4)
    duplicate = cmds.createNode("mesh", name="externalSharedMesh")
    cmds.connectAttr(second[1] + ".outMesh[0]", duplicate + ".inMesh")
    refused_unchanged([first[1], second[1]])


def reference_batch():
    asset("toReference")
    path = str(OUT / "delete_reference_fixture.ma")
    cmds.file(rename=path)
    cmds.file(save=True, type="mayaAscii", force=True)
    new_scene()
    local = asset("local")
    cmds.file(path, reference=True, namespace="referencedAsset")
    referenced = [node for node in cmds.ls(type=api.NODE_TYPE) or [] if node.startswith("referencedAsset:")][0]
    refused_unchanged([local[1], referenced])


def invalid_selection_and_undo_disabled():
    source, node, root, meshes = asset()
    cmds.select(node, replace=True)
    check(api.delete_assets([]) == [] and cmds.objExists(node), "Empty explicit list used scene selection.")
    refused_unchanged([source])
    cmds.undoInfo(state=False)
    before = identities()
    try:
        api.delete_assets([node])
    except ValueError as exc:
        check("撤销" in str(exc), "Undo-disabled refusal was not actionable.")
    else:
        raise AssertionError("Deletion ran with undo disabled.")
    check(before == identities() and not cmds.undoInfo(query=True, state=True), "API changed scene or user undo settings.")
    cmds.undoInfo(state=True)


CASES = [single_snapshots_undo_redo, multi_explicit_targets, shared_source,
         missing_source_and_root, root_link_and_partial_mesh,
         no_root_links_preserves_unknown_parents, foreign_children_and_custom_transform,
         inserted_and_external_ancestors, locked_batch, instances_and_shared_outputs,
         reference_batch, invalid_selection_and_undo_disabled]
for test in CASES:
    start = time.perf_counter()
    try:
        new_scene()
        test()
        result = {"name": test.__name__, "status": "passed"}
    except Exception:
        result = {"name": test.__name__, "status": "failed", "traceback": traceback.format_exc()}
    result["seconds"] = time.perf_counter() - start
    REPORT["cases"].append(result)
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["name"] + ": " + result["status"], flush=True)
REPORT["status"] = "passed" if all(case["status"] == "passed" for case in REPORT["cases"]) else "failed"
REPORT["complete"] = True
REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT: " + str(REPORT_PATH), flush=True)
try:
    cmds.file(new=True, force=True)
    utils.processIdleEvents()
    maya.standalone.uninitialize()
    REPORT["teardown"] = "passed"
except Exception:
    REPORT["teardown"] = traceback.format_exc()
    REPORT["status"] = "failed"
REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if REPORT["status"] == "passed" else 1)
