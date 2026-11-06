"""Independent acceptance checks for live per-strand curve controls.

Run only in a fresh mayapy with BRAIDTOOLS_ISOLATED_TEST=1 and MAYA_APP_DIR
equal to --profile, a dedicated child of --output. Never connects to Maya.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback


parser = argparse.ArgumentParser()
parser.add_argument("--package", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--profile", required=True)
ARGS = parser.parse_args()
PACKAGE = Path(ARGS.package).resolve()
OUT = Path(ARGS.output).resolve()
PROFILE = Path(ARGS.profile).resolve()
if (os.environ.get("BRAIDTOOLS_ISOLATED_TEST") != "1"
        or Path(os.environ.get("MAYA_APP_DIR", "")).resolve() != PROFILE
        or PROFILE == OUT or OUT not in PROFILE.parents
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
REPORT_PATH = OUT / "strand_controls_results.json"
REPORT = {"status": "running", "complete": False, "maya": cmds.about(version=True),
          "scope": "Fresh isolated mayapy; no interactive Maya, production scene, or XGen description.",
          "cases": [], "artifacts": {
              "testSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "pluginSha256": hashlib.sha256(Path(api.plugin_path()).read_bytes()).hexdigest(),
              "scriptsSha256": {str(path.relative_to(PACKAGE)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((PACKAGE / "scripts").rglob("*.py"))}}}
EPS = 2e-5


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def tuple3(point):
    return point.x, point.y, point.z


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def delta(first, second):
    check(len(first) == len(second), "Geometry point counts differ.")
    return max((distance(a, b) for a, b in zip(first, second)), default=0.0)


def close(first, second, message, tolerance=EPS):
    error = delta(first, second)
    check(error < tolerance, "%s Maximum error: %.9g" % (message, error))


def ids():
    return set(cmds.ls(uuid=True) or [])


def uid(node):
    return cmds.ls(node, uuid=True)[0]


def alive(identity):
    return bool(cmds.ls(identity, long=True))


def dag(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def matrix(node):
    return dag(node).inclusiveMatrix()


def shape(transform):
    values = cmds.listRelatives(transform, shapes=True, noIntermediate=True,
                                fullPath=True, type="nurbsCurve") or []
    check(len(values) == 1, "Expected one editable NURBS shape: " + transform)
    return values[0]


def output_curve(node, attribute, index):
    data = api._plug(node, attribute).elementByLogicalIndex(index).asMObject()
    check(not data.isNull(), "Null curve output: %s.%s[%d]" % (node, attribute, index))
    # MFnNurbsCurve does not retain the temporary geometry-data owner returned
    # by MPlug.asMObject(). Keep that MObject alive for every function-set read.
    return data, om.MFnNurbsCurve(data)


def mesh_points(node, strand, world=True, effective=True):
    geometry = api.geometry_generator(node) if effective else node
    data = api._plug(geometry, "outMesh").elementByLogicalIndex(strand).asMObject()
    check(not data.isNull(), "Null mesh output.")
    result = om.MFnMesh(data).getPoints()
    transform = matrix(api.asset_root(node)) if world else om.MMatrix()
    return [tuple3(point * transform) for point in result]


def all_meshes(node, effective=True):
    return [mesh_points(node, strand, effective=effective) for strand in range(3)]


def control_points(control):
    return [tuple3(point) for point in om.MFnNurbsCurve(dag(shape(control))).cvPositions(om.MSpace.kWorld)]


def offsets(node, strand):
    current = control_points(api.strand_controls(node)[strand])
    rest_data, rest_fn = output_curve(node, "outControlCurves", strand)
    rest = rest_fn.cvPositions()
    transform = matrix(api.asset_root(node))
    return [tuple(a - b for a, b in zip(point, tuple3(base * transform)))
            for point, base in zip(current, rest)]


def centers(node, strand, effective=True):
    points = mesh_points(node, strand, effective=effective)
    sides = cmds.getAttr(node + ".radialSides")
    check(len(points) % sides == 0, "Unexpected ring topology.")
    return [tuple(sum(point[axis] for point in points[start:start + sides]) / sides
                  for axis in range(3)) for start in range(0, len(points), sides)]


def check_delta_field(node):
    """Compare ring centers to an independent normalized-parameter oracle."""
    controls = api.strand_controls(node)
    transform = matrix(api.asset_root(node))
    maximum = 0.0
    for strand in range(3):
        original = centers(node, strand, effective=False)
        adjusted = centers(node, strand)
        edited = om.MFnNurbsCurve(dag(shape(controls[strand])))
        rest_data, rest = output_curve(node, "outControlCurves", strand)
        e0, e1 = edited.knotDomain
        r0, r1 = rest.knotDomain
        expected = []
        for i, point in enumerate(original):
            fraction = float(i) / (len(original) - 1)
            e = edited.getPointAtParam(e0 + fraction * (e1 - e0), om.MSpace.kWorld)
            r = rest.getPointAtParam(r0 + fraction * (r1 - r0)) * transform
            expected.append(tuple3(om.MPoint(*point) + (e - r)))
        error = delta(adjusted, expected)
        maximum = max(maximum, error)
        check(error < EPS, "Strand %d does not follow the normalized curve delta: %.9g" % (strand, error))
    return maximum


def fresh():
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm", angle="deg")
    cmds.undoInfo(state=True, infinity=True)
    utils.processIdleEvents()


def asset(name="braidPath", offset=0.0):
    source = cmds.curve(name=name, degree=3, point=[
        (offset, 0, 0), (offset + 1, 3, 0), (offset - 1, 7, 1),
        (offset + 0.5, 10, -0.5), (offset, 14, 0)])
    node = api.create([source])[0]
    cmds.setAttr(node + ".densityMode", 1)
    api.set_parameter([node], "cycles", 4.0)
    api.set_parameter([node], "longitudinalMode", 1)
    api.set_parameter([node], "lengthSegments", 96)
    api.set_parameter([node], "radialSides", 8)
    api.set_parameter([node], "guideSamples", 97)
    return source, node


def edit(control, index=12, vector=(0.7, 0.1, 0.2)):
    cmds.move(*vector, shape(control) + ".cv[%d]" % index,
              relative=True, objectSpace=True)


def all_guides(node):
    transform = matrix(api.asset_root(node))
    geometry = api.geometry_generator(node)
    result = []
    for index in range(27):
        data, curve = output_curve(geometry, "outGuideCurves", index)
        result.append([tuple3(point * transform) for point in curve.cvPositions()])
    return result


def legacy_upgrade_zero_edit_and_identity():
    source, node = asset()
    before = all_meshes(node)
    before_guides = all_guides(node)
    check(api.strand_controls(node) == [], "A legacy asset should have no controls before enabling.")
    api.enable_strand_controls([node], samples=32)
    controls = api.strand_controls(node)
    helper = api.geometry_generator(node)
    check(len(controls) == 3 and helper != node, "Expected three controls and a separate evaluated helper.")
    for strand in range(3):
        close(before[strand], mesh_points(node, strand), "Enabling controls changed strand %d." % strand)
        check(len(control_points(controls[strand])) == 32, "Control sample setting must mean actual CV count.")
        for selected in (controls[strand], shape(controls[strand]), shape(controls[strand]) + ".cv[3]"):
            check(api.generators_from_selection([selected]) == [node], "Control selection exposed the helper or wrong asset.")
    for before_curve, after_curve in zip(before_guides, all_guides(node)):
        close(before_curve, after_curve, "Zero-edit guide geometry changed.")
    check(api.generators_from_selection([helper]) == [node], "Helper selection should resolve to its public owner.")
    check(api.generators_from_selection([source]) == [node], "Source selection returned an auxiliary generator.")
    check(api.create([source]) == [node], "Create should reuse only the original public generator.")
    edit(controls[0])
    modified = all_meshes(node)
    before_ids = ids()
    api.enable_strand_controls([node], samples=64)
    check(ids() == before_ids, "Enabling an existing control rig created duplicates.")
    for strand in range(3):
        close(modified[strand], mesh_points(node, strand), "Repeated enable silently reset existing edits.")


def one_strand_locality_and_delta_field():
    unused, node = asset()
    api.enable_strand_controls([node])
    before = all_meshes(node)
    controls = api.strand_controls(node)
    edit(controls[1], vector=(0.9, 0.2, -0.3))
    after = all_meshes(node)
    check(delta(before[1], after[1]) > 0.1, "Editing a control CV did not deform its strand.")
    close(before[0], after[0], "Strand 1 changed while editing strand 2.")
    close(before[2], after[2], "Strand 3 changed while editing strand 2.")
    return {"maximum_center_error_cm": check_delta_field(node)}


def main_path_density_and_ramps_keep_edits():
    source, node = asset()
    api.enable_strand_controls([node])
    controls = api.strand_controls(node)
    edit(controls[0])
    edit(controls[2], index=20, vector=(-0.6, 0.1, 0.3))
    old_points = control_points(controls[0])
    old_offsets = [offsets(node, strand) for strand in range(3)]
    cmds.move(1.5, 0.3, -0.5, source + ".cv[2]", relative=True, objectSpace=True)
    api.set_parameter([node], "cycles", 7.25)
    api.set_parameter([node], "size", 1.4)
    api.set_parameter([node], "phase", 36.0)
    # Edit the public ramp directly: connection propagation must cover entries,
    # including an element added after the helper was created.
    cmds.setAttr(node + ".sizeRamp[2].sizeRamp_Position", 0.6)
    cmds.setAttr(node + ".sizeRamp[2].sizeRamp_FloatValue", 0.7)
    cmds.setAttr(node + ".sizeRamp[2].sizeRamp_Interp", 1)
    check(delta(old_points, control_points(controls[0])) > 0.1, "Controls did not follow the changed primary path/parameters.")
    for strand in range(3):
        close(old_offsets[strand], offsets(node, strand), "Master edits changed the stored per-strand tweak.")
    return {"maximum_center_error_cm": check_delta_field(node)}


def replacing_ramp_entries_keeps_live_controls():
    unused, node = asset()
    api.enable_strand_controls([node])
    helper = api.geometry_generator(node)
    edit(api.strand_controls(node)[0], 11, (0.6, -0.1, 0.2))
    original_offsets = [offsets(node, strand) for strand in range(3)]
    maximum = 0.0
    changes = [
        ("sizeRamp", [(0.0, 1.0, 1), (0.2, 0.65, 2), (0.5, 1.3, 1), (0.8, 0.8, 1), (1.0, 0.35, 1)]),
        ("rotationRamp", [(0.0, -15.0, 1), (0.3, 32.0, 2), (0.7, -20.0, 1), (1.0, 10.0, 1)]),
        ("sizeRamp", [(0.0, 0.9, 1), (1.0, 0.5, 1)]),
        ("rotationRamp", [(0.5, 21.0, 1)]),
        ("rotationRamp", [(0.0, -5.0, 1), (0.4, 16.0, 2), (1.0, 35.0, 1)]),
    ]
    for name, entries in changes:
        api.set_ramp([node], name, entries)
        check(cmds.isConnected(node + "." + name, helper + "." + name),
              "Replacing ramp elements disconnected the helper's live ramp input.")
        check(len(cmds.getAttr(node + "." + name, multiIndices=True)) == len(entries),
              "Ramp fixture did not actually replace the number of entries.")
        for strand in range(3):
            close(original_offsets[strand], offsets(node, strand), "Replacing ramp entries changed a stored CV tweak.")
        maximum = max(maximum, check_delta_field(node))
    return {"maximum_center_error_cm": maximum, "ramp_replacements": len(changes)}


def source_and_asset_transforms():
    source, node = asset()
    api.enable_strand_controls([node])
    before = all_meshes(node)
    root = api.asset_root(node)
    cmds.setAttr(root + ".translate", 2.0, -1.0, 3.0, type="double3")
    cmds.setAttr(root + ".rotate", 11.0, 19.0, -6.0, type="double3")
    cmds.setAttr(root + ".scale", 1.3, 0.8, 1.7, type="double3")
    for strand in range(3):
        close(before[strand], mesh_points(node, strand), "Asset-root inverse compensation failed with unedited controls.")
    controls = api.strand_controls(node)
    edit(controls[0])
    cmds.setAttr(source + ".translate", -3.0, 2.0, 1.0, type="double3")
    cmds.setAttr(source + ".rotate", 8.0, -23.0, 17.0, type="double3")
    cmds.setAttr(source + ".scale", 0.7, 1.1, 1.4, type="double3")
    cmds.setAttr(controls[2] + ".translate", 0.3, 0.0, -0.2, type="double3")
    cmds.setAttr(controls[2] + ".rotate", 0.0, 0.0, 4.0, type="double3")
    return {"maximum_center_error_cm": check_delta_field(node)}


def guides_roots_and_bake_match_adjusted_geometry():
    unused, node = asset()
    api.enable_strand_controls([node])
    controls = api.strand_controls(node)
    edit(controls[0], 0, (0.2, 0.1, 0.3))
    edit(controls[1], 12, (-0.5, 0.2, 0.1))
    helper = api.geometry_generator(node)
    transform = matrix(api.asset_root(node))
    maximum = 0.0
    for strand in range(3):
        guide_data, guide = output_curve(helper, "outGuideCurves", strand * 9)
        for point in centers(node, strand):
            local = om.MPoint(*point) * transform.inverse()
            closest, unused_parameter = guide.closestPoint(local, tolerance=1e-8)
            maximum = max(maximum, (closest - local).length())
        check(maximum < 1e-4, "Center guide does not pass through matching-resolution mesh ring centers.")
        ring = mesh_points(node, strand)[:8]
        center = centers(node, strand)[0]
        for index in range(8):
            curve_data, curve = output_curve(helper, "outGuideCurves", strand * 9 + index + 1)
            root_point = tuple3(curve.getPointAtParam(curve.knotDomain[0]) * transform)
            expected = tuple(center[axis] + 0.96 * (ring[index][axis] - center[axis]) for axis in range(3))
            check(distance(root_point, expected) < EPS, "A peripheral guide root drifted from its strand cross-section.")
    guides = api.extract_guides([node], root_surfaces=True)[0]
    snapshots = sorted(cmds.listRelatives(guides, allDescendents=True, fullPath=True, type="nurbsCurve") or [])
    check(len(snapshots) == 27, "Guide extraction did not produce 27 independent curves.")
    native_guides = all_guides(node)
    for index, snapshot in enumerate(snapshots):
        actual = [tuple3(point) for point in om.MFnNurbsCurve(dag(snapshot)).cvPositions(om.MSpace.kWorld)]
        close(actual, native_guides[index], "Extracted guides use the unadjusted generator.")
    baked = api.bake([node])[0]
    mesh_shapes = cmds.listRelatives(baked, allDescendents=True, fullPath=True, type="mesh") or []
    check(len(mesh_shapes) == 3, "Baking did not produce three meshes.")
    baked_points = [[tuple3(point) for point in om.MFnMesh(dag(item)).getPoints(om.MSpace.kWorld)] for item in mesh_shapes]
    adjusted = all_meshes(node)
    best_error = min(max(delta(baked_points[index], adjusted[strand]) for index, strand in enumerate(order))
                     for order in itertools.permutations(range(3)))
    check(best_error < EPS, "Baked meshes do not match the adjusted live geometry.")
    static_before = [[tuple3(point) for point in om.MFnNurbsCurve(dag(item)).cvPositions(om.MSpace.kWorld)] for item in snapshots]
    edit(controls[1], 15, (0.8, 0.0, 0.0))
    for before, item in zip(static_before, snapshots):
        close(before, [tuple3(point) for point in om.MFnNurbsCurve(dag(item)).cvPositions(om.MSpace.kWorld)],
              "Previously extracted independent guides unexpectedly changed.")
    return {"maximum_center_guide_error_cm": maximum, "maximum_baked_error_cm": best_error}


def reset_single_all_and_undo_redo():
    unused, node = asset()
    api.enable_strand_controls([node])
    controls = api.strand_controls(node)
    for strand in range(3):
        edit(controls[strand], 10 + strand, (0.4 + strand * 0.1, 0.1, 0.2))
    cmds.setAttr(controls[1] + ".translateX", 0.15)
    before = all_meshes(node)
    baseline = all_meshes(node, effective=False)
    cmds.flushUndo()
    api.reset_strand_controls([node], strand=1)
    close(mesh_points(node, 1), baseline[1], "Single reset did not clear CV and transform edits.")
    for strand in (0, 2):
        close(mesh_points(node, strand), before[strand], "Single reset affected another strand.")
    cmds.undo()
    utils.processIdleEvents()
    for strand in range(3):
        close(mesh_points(node, strand), before[strand], "One undo did not restore a strand reset.")
    cmds.redo()
    utils.processIdleEvents()
    close(mesh_points(node, 1), baseline[1], "Redo did not restore the reset state.")
    api.reset_strand_controls([node])
    for strand in range(3):
        close(mesh_points(node, strand), baseline[strand], "Reset all left a strand deformation.")


def enabling_is_one_undoable_operation():
    source, node = asset()
    before_ids = ids()
    before = all_meshes(node)
    cmds.flushUndo()
    api.enable_strand_controls([node], samples=16)
    after_ids = ids()
    check(after_ids != before_ids, "Fixture created no controls.")
    cmds.undo()
    utils.processIdleEvents()
    check(ids() == before_ids, "One undo did not restore the exact pre-enable scene identities.")
    check(api.geometry_generator(node) == node and api.strand_controls(node) == [], "Undo retained a partial control rig.")
    for strand in range(3):
        close(mesh_points(node, strand), before[strand], "Undo of enabling changed original geometry.")
    cmds.redo()
    utils.processIdleEvents()
    check(ids() == after_ids, "Redo did not restore the original control identities.")
    check(len(api.strand_controls(node)) == 3 and api.create([source]) == [node], "Redo broke public asset identity.")
    check_delta_field(node)


def save_reopen_preserves_live_controls():
    source, node = asset()
    api.enable_strand_controls([node], samples=24)
    controls = api.strand_controls(node)
    edit(controls[0], 9, (0.8, 0.1, -0.1))
    cmds.setAttr(controls[2] + ".translateZ", 0.2)
    before = all_meshes(node)
    old_offsets = [offsets(node, strand) for strand in range(3)]
    owned_ids = [uid(item) for item in [node, api.geometry_generator(node)] + controls]
    filename = str(OUT / "strand_controls_roundtrip.ma")
    cmds.file(rename=filename)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(filename, open=True, force=True, prompt=False)
    utils.processIdleEvents()
    check(all(alive(identity) for identity in owned_ids), "Save/reopen lost controlled asset identities.")
    for strand in range(3):
        close(mesh_points(node, strand), before[strand], "Save/reopen changed evaluated geometry.")
        close(offsets(node, strand), old_offsets[strand], "Save/reopen changed stored CV tweaks.")
    edit(api.strand_controls(node)[0], 9, (0.2, 0.0, 0.0))
    check(delta(mesh_points(node, 0), before[0]) > 0.01, "Reopened controls are no longer live.")
    check(api.generators_from_selection([source]) == [node], "Reopened source resolves to an auxiliary node.")
    check_delta_field(node)


def delete_controls_with_snapshots_and_undo():
    source, node = asset()
    api.enable_strand_controls([node])
    controls = api.strand_controls(node)
    edit(controls[0])
    root = api.asset_root(node)
    helper = api.geometry_generator(node)
    guides = api.extract_guides([node])[0]
    baked = api.bake([node])[0]
    keep = {uid(source), uid(guides), uid(baked)}
    owned = {uid(item) for item in [node, helper, root] + (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])}
    before = all_meshes(node)
    cmds.flushUndo()
    check(api.delete_assets([node]) == [node], "Controlled deletion changed the public return identity.")
    check(all(not alive(identity) for identity in owned), "Controlled deletion leaked owned graph nodes.")
    check(all(alive(identity) for identity in keep), "Controlled deletion removed independent data.")
    cmds.undo()
    utils.processIdleEvents()
    check(all(alive(identity) for identity in owned), "One undo did not restore the entire controlled asset.")
    for strand in range(3):
        close(mesh_points(node, strand), before[strand], "Delete undo did not restore strand edits.")
    cmds.redo()
    utils.processIdleEvents()
    check(all(not alive(identity) for identity in owned), "Delete redo left owned controls.")
    check(all(alive(identity) for identity in keep), "Delete redo removed source or snapshots.")


def deletion_preserves_user_children():
    source, node = asset()
    api.enable_strand_controls([node])
    control = api.strand_controls(node)[0]
    child = cmds.createNode("transform", name="userAttachedChild", parent=control)
    cmds.addAttr(child, longName="userData", attributeType="double", defaultValue=17.0)
    root = api.asset_root(node)
    sibling = cmds.polyCube(name="userMesh", constructionHistory=False)[0]
    sibling = cmds.parent(sibling, root, absolute=True)[0]
    keep = {uid(item) for item in (source, child, control, root, sibling)}
    helper_id = uid(api.geometry_generator(node))
    api.delete_assets([node])
    check(all(alive(identity) for identity in keep), "Deleting controls removed user children or required ancestors.")
    check(not alive(helper_id) and not cmds.objExists(node), "Deleting controls retained their generator graph.")
    check(cmds.getAttr(child + ".userData") == 17.0, "User child data changed.")


def external_control_consumer_refuses_deletion():
    unused, node = asset()
    api.enable_strand_controls([node])
    foreign = cmds.createNode("nurbsCurve", name="externalCurveConsumer")
    control_shape = shape(api.strand_controls(node)[0])
    cmds.connectAttr(control_shape + ".worldSpace[0]", foreign + ".create")
    before_ids = ids()
    undo_name = cmds.undoInfo(query=True, undoName=True)
    try:
        api.delete_assets([node])
    except (ValueError, RuntimeError):
        pass
    else:
        raise AssertionError("Deleting a control with an external consumer was not refused.")
    check(ids() == before_ids, "Refused deletion still changed scene identities.")
    check(cmds.undoInfo(query=True, undoName=True) == undo_name, "Preflight refusal added an undo operation.")


def assert_empty_native_mesh(helper):
    geometry = api._plug(helper, "outMesh").elementByLogicalIndex(0).asMObject()
    check(cmds.getAttr(helper + ".outStatus") != "OK", "Invalid control input was reported as valid.")
    vertices = 0
    if not geometry.isNull():
        try:
            vertices = om.MFnMesh(geometry).numVertices
        except RuntimeError:
            # Empty MFnMeshData has a valid owner with no attachable mesh.
            check(geometry.hasFn(om.MFn.kMeshData), "Invalid output was not empty mesh data.")
    check(vertices == 0, "An invalid control input left stale mesh geometry.")


def missing_native_control_input_clears_geometry():
    unused, node = asset()
    api.enable_strand_controls([node])
    helper = api.geometry_generator(node)
    points = all_meshes(node)
    destination = helper + ".strandControlCurves[1]"
    source = cmds.listConnections(destination, source=True, destination=False, plugs=True)[0]
    cmds.disconnectAttr(source, destination)
    assert_empty_native_mesh(helper)
    cmds.connectAttr(source, destination)
    for strand in range(3):
        close(mesh_points(node, strand), points[strand], "Reconnecting a missing control did not recover geometry.")
    check(cmds.getAttr(helper + ".outStatus") == "OK", "Restored control remained invalid.")


def inserted_control_knot_invalidates_and_undo_recovers():
    unused, node = asset()
    api.enable_strand_controls([node])
    helper = api.geometry_generator(node)
    control = api.strand_controls(node)[0]
    edit(control, 13)
    before = all_meshes(node)
    curve = om.MFnNurbsCurve(dag(shape(control)))
    first, last = curve.knotDomain
    original_count = curve.numCVs
    cmds.flushUndo()
    cmds.insertKnotCurve(control, constructionHistory=True, replaceOriginal=True,
                         parameter=first + 0.417 * (last - first), numberOfKnots=1, addKnots=True)
    check(om.MFnNurbsCurve(dag(shape(control))).numCVs > original_count,
          "Insert-knot fixture did not change the control topology.")
    assert_empty_native_mesh(helper)
    cmds.undo()
    utils.processIdleEvents()
    for strand in range(3):
        close(mesh_points(node, strand), before[strand], "Undo of control topology edit did not restore live geometry.")
    check(cmds.getAttr(helper + ".outStatus") == "OK", "Undo left a control topology error.")


def indirect_control_rebind_cycle_is_refused():
    unused_a, first = asset("firstPath")
    source_b, second = asset("secondPath", 5.0)
    api.enable_strand_controls([first, second])
    control_a = api.strand_controls(first)[0]
    control_b = api.strand_controls(second)[0]
    api.rebind(first, control_b)
    check(api.source_transform(first) == control_b, "An acyclic cross-asset source was not bound.")
    before_ids = ids()
    before_second = all_meshes(second)
    undo_name = cmds.undoInfo(query=True, undoName=True)
    try:
        api.rebind(second, control_a)
    except (ValueError, RuntimeError):
        pass
    else:
        raise AssertionError("A cross-asset control dependency cycle was not refused.")
    check(ids() == before_ids, "Refusing a rebind cycle changed scene identities.")
    check(api.source_transform(second) == cmds.ls(source_b, long=True)[0], "Cycle refusal changed the original source.")
    check(cmds.undoInfo(query=True, undoName=True) == undo_name, "Cycle refusal altered the undo queue.")
    check(not (cmds.cycleCheck(all=True, list=True) or []), "A rejected rebind left a dependency cycle.")
    for strand in range(3):
        close(mesh_points(second, strand), before_second[strand], "Cycle refusal changed geometry.")


def enable_preflight_failure_preserves_previous_user_undo():
    unused, node = asset()
    upstream = cmds.createNode("addDoubleLinear", name="userSampleDriver")
    cmds.setAttr(upstream + ".input1", 32.0)
    cmds.connectAttr(upstream + ".output", node + ".strandControlSamples")
    marker = cmds.createNode("transform", name="userUndoMarker")
    cmds.setAttr(marker + ".translateX", 7.0)
    before_ids = ids()
    undo_name = cmds.undoInfo(query=True, undoName=True)
    try:
        api.enable_strand_controls([node])
    except (ValueError, RuntimeError):
        pass
    else:
        raise AssertionError("Enabling controls with a connected sample count was not refused.")
    check(ids() == before_ids, "Failed enable changed scene identities.")
    check(cmds.getAttr(marker + ".translateX") == 7.0, "Failed enable undid a previous user operation.")
    check(cmds.undoInfo(query=True, undoName=True) == undo_name, "Failed enable changed the user's undo queue.")
    check(api.strand_controls(node) == [], "Failed enable left a partial control rig.")
    cmds.undo()
    check(cmds.objExists(marker) and cmds.getAttr(marker + ".translateX") == 0.0,
          "The previous user operation can no longer be undone normally.")


def multiple_assets_and_cycle_free_evaluation():
    first_source, first = asset("firstPath")
    second_source, second = asset("secondPath", 5.0)
    api.enable_strand_controls([first, second], samples=16)
    before = all_meshes(second)
    edit(api.strand_controls(first)[0], 7)
    for strand in range(3):
        close(mesh_points(second, strand), before[strand], "One controlled asset affected another.")
    check(api.generators_from_selection([first_source, second_source]) == [first, second],
          "Multi-source selection includes helper duplicates.")
    cycles = cmds.cycleCheck(all=True, list=True) or []
    check(not cycles, "Control dependency graph contains cycles: %s" % cycles)
    previous_mode = cmds.evaluationManager(query=True, mode=True)[0]
    try:
        cmds.evaluationManager(mode="parallel")
        edit(api.strand_controls(first)[1], 8, (-0.4, 0.1, 0.2))
        check_delta_field(first)
        check_delta_field(second)
    finally:
        cmds.evaluationManager(mode=previous_mode)


def edit_latency_observation():
    unused, node = asset()
    api.enable_strand_controls([node])
    control = api.strand_controls(node)[0]
    durations = []
    for index in range(24):
        start = time.perf_counter()
        edit(control, 12, ((1 if index % 2 else -1) * 0.02, 0.0, 0.0))
        all_meshes(node)
        durations.append((time.perf_counter() - start) * 1000.0)
    timings = sorted(durations[4:])
    return {"sample_count": len(timings), "median_ms": statistics.median(timings),
            "p95_ms": timings[int(math.ceil(len(timings) * 0.95)) - 1],
            "note": "Single isolated asset, manual 96 longitudinal segments; observational timing, not an FPS guarantee."}


CASES = [legacy_upgrade_zero_edit_and_identity, one_strand_locality_and_delta_field,
         main_path_density_and_ramps_keep_edits, replacing_ramp_entries_keeps_live_controls,
         source_and_asset_transforms,
         guides_roots_and_bake_match_adjusted_geometry, reset_single_all_and_undo_redo,
         enabling_is_one_undoable_operation, save_reopen_preserves_live_controls,
         delete_controls_with_snapshots_and_undo, deletion_preserves_user_children,
         external_control_consumer_refuses_deletion, missing_native_control_input_clears_geometry,
         inserted_control_knot_invalidates_and_undo_recovers, indirect_control_rebind_cycle_is_refused,
         enable_preflight_failure_preserves_previous_user_undo,
         multiple_assets_and_cycle_free_evaluation, edit_latency_observation]
for test in CASES:
    start = time.perf_counter()
    try:
        fresh()
        details = test()
        result = {"name": test.__name__, "status": "passed"}
        if details is not None:
            result["details"] = details
    except Exception:
        result = {"name": test.__name__, "status": "failed", "traceback": traceback.format_exc()}
    result["seconds"] = time.perf_counter() - start
    REPORT["cases"].append(result)
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["name"] + ": " + result["status"], flush=True)
REPORT["status"] = "passed" if all(case["status"] == "passed" for case in REPORT["cases"]) else "failed"
REPORT["complete"] = True
try:
    cmds.file(new=True, force=True)
    utils.processIdleEvents()
    maya.standalone.uninitialize()
    REPORT["teardown"] = "passed"
except Exception:
    REPORT["teardown"] = traceback.format_exc()
    REPORT["status"] = "failed"
REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT: " + str(REPORT_PATH), flush=True)
sys.exit(0 if REPORT["status"] == "passed" else 1)
