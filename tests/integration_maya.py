"""Independent BraidTools integration acceptance tests for an isolated mayapy.

Never connect this runner to an existing Maya. The parent process must set
BRAIDTOOLS_ISOLATED_TEST=1 and MAYA_APP_DIR to --profile under --output.
No XGen description, production scene or user preferences are used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plugin', required=True)
    parser.add_argument('--scripts', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--phase', choices=('smoke', 'full', 'performance'), default='full')
    parser.add_argument('--api', default='braidtools.api')
    parser.add_argument('--performance-limit-ms', type=float, default=100.0)
    return parser.parse_args()


ARGS = arguments()
OUT = Path(ARGS.output).resolve()
PROFILE = Path(ARGS.profile).resolve()
PLUGIN = Path(ARGS.plugin).resolve()
SCRIPTS = Path(ARGS.scripts).resolve()
if os.environ.get('BRAIDTOOLS_ISOLATED_TEST') != '1':
    raise RuntimeError('This runner requires BRAIDTOOLS_ISOLATED_TEST=1 in a new mayapy process.')
if os.path.commonpath([str(OUT), str(PROFILE)]) != str(OUT) or PROFILE == OUT:
    raise RuntimeError('--profile must be a dedicated child directory of --output.')
if Path(os.environ.get('MAYA_APP_DIR', '')).resolve() != PROFILE:
    raise RuntimeError('MAYA_APP_DIR does not match the explicitly isolated profile.')
if not PLUGIN.is_file() or not SCRIPTS.is_dir():
    raise RuntimeError('Plugin or Python scripts directory is missing.')
OUT.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SCRIPTS))
os.environ['MAYA_SKIP_USERSETUP_PY'] = '1'

REPORT = {
    'phase': ARGS.phase, 'status': 'initializing', 'complete': False,
    'plugin': str(PLUGIN), 'scripts': str(SCRIPTS), 'profile': str(PROFILE),
    'scope': 'Isolated mayapy; no current interactive Maya, no XGen, no production scenes.',
    'cases': [],
    'artifacts': {
        'pluginSha256': hashlib.sha256(PLUGIN.read_bytes()).hexdigest(),
        'testRunnerSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'pythonSourcesSha256': {
            str(path.relative_to(SCRIPTS)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(SCRIPTS.rglob('*.py'))
        },
    },
}
REPORT_PATH = OUT / ('results_' + ARGS.phase + '.json')


def save_report():
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def near(a, b, tolerance=1e-5):
    return abs(a - b) <= tolerance


def distance(a, b):
    return math.sqrt(sum((x-y)**2 for x, y in zip(a, b)))


def max_point_delta(a, b):
    check(len(a) == len(b), 'Point counts differ.')
    return max((distance(x, y) for x, y in zip(a, b)), default=0.0)


def point_digest(points):
    return hashlib.sha256(json.dumps(points, separators=(',', ':')).encode()).hexdigest()


def drain_events():
    # Process one-shot lifecycle callbacks as an interactive Maya would between
    # commands, without calling a tool-specific repair function to hide a defect.
    import maya.utils
    maya.utils.processIdleEvents()


def new_scene():
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear='cm', angle='deg', time='film')
    cmds.undoInfo(state=True)
    drain_events()


def curve(name='testPath', kind='s', offset=0.0):
    if kind == 'straight':
        pts = [(offset, 0, 0), (offset, 4, 0), (offset, 8, 0), (offset, 12, 0)]
    elif kind == 'u':
        pts = [(offset, 12, 0), (offset+2, 9, 0), (offset+3, 5, 1),
               (offset+2, 1, 0), (offset-2, 1, 0), (offset-3, 5, -1),
               (offset-2, 9, 0), (offset, 12, -2)]
    elif kind == 'zero':
        pts = [(offset, 0, 0)] * 4
    else:
        pts = [(offset, 0, 0), (offset+2, 3, 0), (offset-2, 7, 1),
               (offset+1, 10, -1), (offset, 14, 0)]
    return cmds.curve(name=name, degree=3, point=pts)


def visible_curve_shape(transform):
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True) or []
    shapes = [s for s in shapes if cmds.nodeType(s) == 'nurbsCurve']
    check(len(shapes) == 1, 'Expected one visible NURBS shape under ' + transform)
    return shapes[0]


def dag(path):
    selection = om.MSelectionList()
    selection.add(path)
    return selection.getDagPath(0)


def dep(node):
    selection = om.MSelectionList()
    selection.add(node)
    return om.MFnDependencyNode(selection.getDependNode(0))


def curve_points(shape, world=True):
    cmds.dgeval(shape + '.local')
    pts = om.MFnNurbsCurve(dag(shape)).cvPositions(om.MSpace.kWorld if world else om.MSpace.kObject)
    return [(p.x, p.y, p.z) for p in pts]


def mesh_points(shape, world=True):
    cmds.dgeval(shape + '.outMesh')
    pts = om.MFnMesh(dag(shape)).getPoints(om.MSpace.kWorld if world else om.MSpace.kObject)
    return [(p.x, p.y, p.z) for p in pts]


def node_mesh_points(node, index):
    obj = dep(node).findPlug('outMesh', False).elementByLogicalIndex(index).asMObject()
    check(not obj.isNull(), 'Null mesh output.')
    fn = om.MFnMesh(obj)
    return [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)]


def node_guide(node, index):
    obj = dep(node).findPlug('outGuideCurves', False).elementByLogicalIndex(index).asMObject()
    check(not obj.isNull(), 'Null curve output at index %d.' % index)
    fn = om.MFnNurbsCurve(obj)
    return {
        'points': [(p.x, p.y, p.z) for p in fn.cvPositions(om.MSpace.kObject)],
        'degree': fn.degree, 'knots': list(fn.knots()), 'form': int(fn.form),
    }


def asset_root(node):
    roots = cmds.listConnections(node + '.assetRoot', source=True, destination=False) or []
    check(len(roots) == 1, 'Missing unique assetRoot association.')
    return cmds.ls(roots[0], long=True)[0]


def source_transform(node):
    sources = cmds.listConnections(node + '.sourceCurve', source=True, destination=False) or []
    check(len(sources) == 1, 'Missing unique sourceCurve association.')
    return cmds.ls(sources[0], long=True)[0]


def assert_live_curve_source(node):
    source = source_transform(node)
    shape = visible_curve_shape(source)
    instance = dag(shape).instanceNumber()
    check(cmds.isConnected('%s.worldSpace[%d]' % (shape, instance), node + '.inputCurve'),
          'Generator is not connected to the current visible source curve shape.')


def mesh_shapes(node):
    result = []
    for i in range(3):
        shapes = cmds.listConnections(node + '.outMesh[%d]' % i,
                                      source=False, destination=True, shapes=True) or []
        shapes = [s for s in shapes if cmds.nodeType(s) == 'mesh']
        check(len(shapes) == 1, 'Each strand must drive one mesh shape.')
        result.append(cmds.ls(shapes[0], long=True)[0])
    return result


def create_one(path):
    created = api.create([path])
    check(isinstance(created, (list, tuple)) and len(created) == 1, 'create must return one node per path.')
    node = created[0]
    check(cmds.nodeType(node) == 'braidToolsGenerator', 'Wrong generator node type.')
    check(cmds.getAttr(node + '.outStatus') == 'OK', 'Native status is not OK.')
    mesh_shapes(node)
    return node


def state(node):
    shapes = mesh_shapes(node)
    return {
        'shapes': shapes,
        'uuids': [cmds.ls(s, uuid=True)[0] for s in shapes],
        'points': [mesh_points(s) for s in shapes],
        'shaders': [sorted(cmds.listConnections(s + '.instObjGroups', type='shadingEngine') or []) for s in shapes],
        'params': {a: cmds.getAttr(node + '.' + a) for a in (
            'densityMode', 'pitch', 'cycles', 'size', 'flatness', 'strandThickness',
            'strandFlatness', 'orientation', 'phase', 'longitudinalMode', 'lengthSegments', 'radialSides')},
    }


def set_ramp(node, attribute, entries):
    children = cmds.attributeQuery(attribute, node=node, listChildren=True)
    check(len(children) == 3, 'Ramp should have position/value/interpolation children.')
    pos = next(c for c in children if c.lower().endswith('position'))
    val = next(c for c in children if c.lower().endswith('floatvalue'))
    interp = next(c for c in children if c.lower().endswith('interp'))
    for index in cmds.getAttr(node + '.' + attribute, multiIndices=True) or []:
        cmds.removeMultiInstance(node + '.' + attribute + '[%d]' % index, b=True)
    for index, (x, y) in enumerate(entries):
        prefix = node + '.' + attribute + '[%d].' % index
        cmds.setAttr(prefix + pos, x)
        cmds.setAttr(prefix + val, y)
        cmds.setAttr(prefix + interp, 1)  # Linear: locality assertions have an exact interval.


def output_guide_world_points(node, index):
    matrix = dag(asset_root(node)).inclusiveMatrix()
    return [tuple(om.MPoint(*p) * matrix)[:3] for p in node_guide(node, index)['points']]


def mean_root(node):
    roots = [output_guide_world_points(node, i * 9)[0] for i in range(3)]
    return tuple(sum(p[d] for p in roots) / 3 for d in range(3))


def source_root(transform):
    fn = om.MFnNurbsCurve(dag(visible_curve_shape(transform)))
    p = fn.getPointAtParam(fn.knotDomain[0], om.MSpace.kWorld)
    return (p.x, p.y, p.z)


def assert_continuous_mesh(shape):
    fn = om.MFnMesh(dag(shape))
    counts, indices = fn.getVertices()
    check(fn.numVertices > 0 and fn.numPolygons > 0, 'Empty strand mesh.')
    roots = list(range(fn.numVertices))
    def find(i):
        while roots[i] != i:
            roots[i] = roots[roots[i]]
            i = roots[i]
        return i
    cursor = 0
    for count in counts:
        face = indices[cursor:cursor+count]
        cursor += count
        for i in face[1:]:
            roots[find(i)] = find(face[0])
    check(len({find(i) for i in range(fn.numVertices)}) == 1, 'A strand has disconnected topology.')
    check(fn.numUVs() > 0, 'Mesh has no UVs.')
    uv_counts, uv_indices = fn.getAssignedUVs()
    check(sum(uv_counts) == len(indices), 'Not every face vertex has a UV.')
    normals = fn.getVertexNormals(True, om.MSpace.kObject)
    check(all(math.isfinite(v) for n in normals for v in (n.x, n.y, n.z)), 'Non-finite normals.')


def test_create_and_selection():
    new_scene()
    paths = [curve('straight', 'straight'), curve('ess', 's', 4), curve('uPath', 'u', 9)]
    cmds.select(paths, replace=True)
    nodes = api.create_from_selection()
    check(len(nodes) == 3, 'Multi-select did not create three assets.')
    check(len(cmds.ls(type='braidToolsGenerator')) == 3, 'Unexpected generator count.')
    before = {n: state(n)['uuids'] for n in nodes}
    again = api.create(paths)
    check(set(again) == set(nodes), 'Repeated create did not find existing assets.')
    check(len(cmds.ls(type='braidToolsGenerator')) == 3, 'Repeated create duplicated assets.')
    for n in nodes:
        check(before[n] == state(n)['uuids'], 'Repeated create replaced mesh shapes.')
        for shape in mesh_shapes(n):
            assert_continuous_mesh(shape)
    for selected in [paths[0], asset_root(nodes[1]), mesh_shapes(nodes[2])[0]]:
        cmds.select(selected, replace=True)
        found = api.generators_from_selection()
        check(len(found) == 1, 'Selecting a source/group/mesh must identify its generator.')
    return {'assets': 3, 'meshShapes': 9, 'repeatCreateKeptShapes': True}


def test_create_undo_redo():
    new_scene()
    paths = [curve('undoPath1'), curve('undoPath2', 'u', 5)]
    nodes = api.create(paths)
    original_roots = [asset_root(n) for n in nodes]
    cmds.undo()
    check(not cmds.ls(type='braidToolsGenerator'), 'One undo must remove the whole create operation.')
    check(all(cmds.objExists(p) for p in paths), 'Undo deleted a user-owned source curve.')
    cmds.redo()
    nodes = cmds.ls(type='braidToolsGenerator') or []
    check(len(nodes) == 2, 'One redo did not restore all assets.')
    for n in nodes:
        state(n)
    return {'sourceCurvesRetained': True, 'restoredGenerators': 2}


def test_continuous_cv_and_batch_parameters():
    new_scene()
    paths = [curve('liveA'), curve('liveB', 'u', 7)]
    nodes = api.create(paths)
    # This case compares corresponding vertices. Automatic segmentation is
    # allowed to change vertex count as arc length/curvature changes, so use a
    # fixed topology here; the performance fixture retains automatic defaults.
    api.set_parameter(nodes, 'longitudinalMode', 1)
    api.set_parameter(nodes, 'lengthSegments', 256)
    api.set_parameter([nodes[0]], 'flatness', .73)
    api.set_parameter([nodes[1]], 'flatness', 1.18)
    initial = [state(n) for n in nodes]
    shape = visible_curve_shape(paths[0])
    p = cmds.pointPosition(shape + '.cv[2]', world=True)
    deltas = []
    # Each edit is evaluated immediately. No tool update or mouse release action.
    for dx in (.15, .30, -.20):
        cmds.xform(shape + '.cv[2]', worldSpace=True, translation=(p[0]+dx, p[1], p[2]))
        changed = state(nodes[0])
        deltas.append(max(max_point_delta(a, b) for a, b in zip(initial[0]['points'], changed['points'])))
    check(all(d > .01 for d in deltas), 'CV edits did not immediately affect generated geometry.')
    check(state(nodes[1])['points'] == initial[1]['points'], 'Editing one path changed another asset.')
    before_batch = [state(n) for n in nodes]
    api.set_parameter(nodes, 'size', 1.37)
    check(near(cmds.getAttr(nodes[0]+'.flatness'), .73) and near(cmds.getAttr(nodes[1]+'.flatness'), 1.18),
          'Batch size edit overwrote unrelated per-asset parameters.')
    for n, before in zip(nodes, before_batch):
        check(state(n)['points'] != before['points'], 'Batch edit did not update each asset.')
    cmds.undo()
    for n, before in zip(nodes, before_batch):
        after = state(n)
        check(after['params'] == before['params'], 'One undo did not restore batch parameters.')
        check(after['points'] == before['points'], 'One undo did not restore batch geometry.')
    cmds.redo()
    check(all(near(cmds.getAttr(n+'.size'), 1.37) for n in nodes), 'Batch redo failed.')
    return {'continuousEditDeltasCm': deltas, 'singleUndoForBatch': True}


def test_density_switch_and_length():
    new_scene()
    path = curve('densityPath', 'straight')
    node = create_one(path)
    api.set_parameter([node], 'guideSamples', 193)
    before = [node_guide(node, i)['points'] for i in (0, 9, 18)]
    length0 = cmds.getAttr(node+'.outLength')
    cycles0 = cmds.getAttr(node+'.outCycles')
    check(near(cycles0, length0/cmds.getAttr(node+'.pitch'), 1e-5), 'Pitch mode cycle count is wrong.')
    api.set_density_mode([node], 1)
    after_switch = [node_guide(node, i)['points'] for i in (0, 9, 18)]
    check(max(max_point_delta(a,b) for a,b in zip(before, after_switch)) < 1e-4, 'Density mode switch jumps shape.')
    cmds.setAttr(path+'.scaleY', 1.8)
    check(near(cmds.getAttr(node+'.outCycles'), cycles0, 1e-5), 'Fixed cycle count changed when curve stretched.')
    check(near(cmds.getAttr(node+'.outLength'), length0*1.8, 1e-4), 'World arc length ignores curve scale.')
    before_back = [node_guide(node, i)['points'] for i in (0, 9, 18)]
    api.set_density_mode([node], 0)
    after_back = [node_guide(node, i)['points'] for i in (0, 9, 18)]
    check(max(max_point_delta(a,b) for a,b in zip(before_back, after_back)) < 1e-4, 'Switching back to pitch jumps shape.')
    pitch = cmds.getAttr(node+'.pitch')
    cmds.setAttr(path+'.scaleY', 2.7)
    check(near(cmds.getAttr(node+'.pitch'), pitch), 'Curve stretch changed pitch setting.')
    check(near(cmds.getAttr(node+'.outCycles'), cmds.getAttr(node+'.outLength')/pitch, 1e-5), 'Pitch mode did not add cycles.')
    return {'initialLengthCm': length0, 'initialCycles': cycles0, 'modeSwitchShapeToleranceCm': 1e-4}


def test_local_ramps():
    new_scene()
    node = create_one(curve('rampPath', 'straight'))
    api.set_parameter([node], 'guideSamples', 257)
    baseline = node_guide(node, 0)['points']
    set_ramp(node, 'rotationRamp', [(0,0), (.35,0), (.5,90), (.65,0), (1,0)])
    rotated = node_guide(node, 0)['points']
    n = len(baseline)
    low = list(range(int(n*.25))) + list(range(int(n*.75), n))
    middle = range(int(n*.42), int(n*.58))
    check(max(distance(baseline[i],rotated[i]) for i in low) < 2e-4, 'Local twist changed unrelated ends.')
    check(max(distance(baseline[i],rotated[i]) for i in middle) > .1, 'Local twist did not rotate the middle.')
    set_ramp(node, 'rotationRamp', [(0,0),(1,0)])
    set_ramp(node, 'sizeRamp', [(0,1),(.35,1),(.5,.4),(.65,1),(1,1)])
    scaled = node_guide(node, 0)['points']
    check(max(distance(baseline[i],scaled[i]) for i in low) < 2e-4, 'Local size ramp changed unrelated ends.')
    i = n//2
    radial0 = math.hypot(baseline[i][0], baseline[i][2])
    radial1 = math.hypot(scaled[i][0], scaled[i][2])
    check(radial0 > .05 and .3 < radial1/radial0 < .5, 'Middle size ramp did not scale the cross section.')
    check(cmds.getAttr(node+'.outStatus') == 'OK', 'Ramps generated invalid geometry.')
    return {'samples': n, 'middleRadialRatio': radial1/radial0, 'endsUnaffected': True}


def test_batch_ramp_scope_and_undo():
    new_scene()
    nodes = api.create([curve('rampBatchA'), curve('rampBatchB', 'u', 6)])
    api.set_ramp([nodes[0]], 'sizeRamp', [(0,1,1), (1,.3,1)])
    api.set_ramp([nodes[1]], 'sizeRamp', [(0,.7,1), (1,1.2,1)])
    api.set_ramp([nodes[0]], 'rotationRamp', [(0,0,1), (1,12,1)])
    api.set_ramp([nodes[1]], 'rotationRamp', [(0,-20,1), (1,0,1)])
    size_before = [api.get_ramp(n, 'sizeRamp') for n in nodes]
    rotation_before = [api.get_ramp(n, 'rotationRamp') for n in nodes]
    params_before = [state(n)['params'] for n in nodes]
    new_rotation = [(0,0,1), (.5,90,1), (1,0,1)]
    api.set_ramp(nodes, 'rotationRamp', new_rotation)
    check(all(api.get_ramp(n, 'rotationRamp') == new_rotation for n in nodes),
          'Batch ramp edit did not update every addressed asset.')
    check([api.get_ramp(n, 'sizeRamp') for n in nodes] == size_before,
          'Batch rotation ramp edit overwrote independent size ramps.')
    check([state(n)['params'] for n in nodes] == params_before,
          'Batch ramp edit overwrote scalar parameters.')
    cmds.undo()
    check([api.get_ramp(n, 'rotationRamp') for n in nodes] == rotation_before,
          'One undo did not restore each asset\'s previous rotation ramp.')
    check([api.get_ramp(n, 'sizeRamp') for n in nodes] == size_before,
          'Undo altered unrelated size ramps.')
    cmds.redo()
    check(all(api.get_ramp(n, 'rotationRamp') == new_rotation for n in nodes),
          'Batch ramp redo failed.')
    return {'assets': 2, 'unrelatedRampsRetained': True, 'oneStepUndoRedo': True}


def test_resolution_and_guide_independence():
    new_scene()
    node = create_one(curve('resolutionPath'))
    api.set_parameter([node], 'longitudinalMode', 1)
    api.set_parameter([node], 'lengthSegments', 96)
    api.set_parameter([node], 'radialSides', 8)
    before = state(node)
    guides = [node_guide(node, i) for i in range(27)]
    api.set_parameter([node], 'radialSides', 24)
    api.set_parameter([node], 'lengthSegments', 240)
    after = state(node)
    check(before['uuids'] == after['uuids'], 'Resolution change replaced output shapes.')
    check(before['shaders'] == after['shaders'], 'Resolution change lost material association.')
    check(all(len(a) > len(b) for a,b in zip(after['points'], before['points'])), 'Resolution did not increase vertices.')
    check(guides == [node_guide(node, i) for i in range(27)], 'Guide geometry depends on preview mesh topology.')
    for shape in after['shapes']:
        assert_continuous_mesh(shape)
    api.set_parameter([node], 'radialSides', 3)
    api.set_parameter([node], 'lengthSegments', 8)
    for shape in mesh_shapes(node): assert_continuous_mesh(shape)
    return {'meshShapeUUIDsKept': True, 'guideCount': 27, 'guideDataIdenticalAcrossMeshResolutions': True}


def test_names_parenting_and_nonuniform_scale():
    new_scene()
    path = curve('originalPath', 's')
    node = create_one(path)
    root = asset_root(node)
    path = cmds.rename(path, 'renamedPath')
    node = cmds.rename(node, 'renamedGenerator')
    parent = cmds.createNode('transform', name='sourceParent')
    path = cmds.parent(path, parent)[0]
    cmds.setAttr(parent+'.translate', 6, -2, 3, type='double3')
    cmds.setAttr(parent+'.rotate', 17, 31, -9, type='double3')
    cmds.setAttr(parent+'.scale', 1.7, .65, 1.2, type='double3')
    check(distance(mean_root(node), source_root(path)) < 1e-4, 'Source transform produces wrong world roots.')
    output_parent = cmds.createNode('transform', name='outputParent')
    cmds.setAttr(output_parent+'.translate', -3, 5, 2, type='double3')
    cmds.setAttr(output_parent+'.rotate', -7, 10, 22, type='double3')
    cmds.setAttr(output_parent+'.scale', .8, 1.3, 1.1, type='double3')
    before = [mesh_points(s) for s in mesh_shapes(node)]
    cmds.parent(root, output_parent, relative=True)
    after = [mesh_points(s) for s in mesh_shapes(node)]
    check(max(max_point_delta(a,b) for a,b in zip(before,after)) < 5e-4, 'Output parenting causes a double transform.')
    cmds.select(path, replace=True)
    check(api.generators_from_selection() == [node], 'Source association depends on old names.')
    cmds.select(asset_root(node), replace=True)
    check(api.generators_from_selection() == [node], 'Root association depends on old names.')
    cmds.setAttr(parent+'.scaleX', -1.7)
    check(distance(mean_root(node), source_root(path)) < 1e-4, 'Negative source scale breaks world space association.')
    check(all(math.isfinite(v) for s in mesh_shapes(node) for p in mesh_points(s) for v in p), 'Non-finite transformed output.')
    return {'renamedSource': path, 'nonuniformAndNegativeScale': True, 'outputParentingToleranceCm': 5e-4}


def test_initial_frame_continuity():
    new_scene()
    epsilon = 1e-5
    def prepare(path):
        node = create_one(path)
        api.set_parameter([node], 'longitudinalMode', 1)
        api.set_parameter([node], 'lengthSegments', 256)
        api.set_parameter([node], 'guideSamples', 193)
        return node

    def root_points(node):
        sides = cmds.getAttr(node+'.radialSides')
        guides = [output_guide_world_points(node, index)[0] for index in range(27)]
        mesh_roots = [point for shape in mesh_shapes(node) for point in mesh_points(shape)[:sides]]
        check(cmds.getAttr(node+'.outStatus') == 'OK', 'Tiny root-tangent change invalidated the braid.')
        return guides, mesh_roots

    def compare_roots(previous, current, label):
        guide_delta = max_point_delta(previous[0], current[0])
        mesh_delta = max_point_delta(previous[1], current[1])
        check(max(guide_delta, mesh_delta) < .002,
              '%s flips the root frame: guide %.8f cm, mesh %.8f cm.' % (label, guide_delta, mesh_delta))
        return {'guideRootMaxDisplacementCm': guide_delta, 'meshRootMaxDisplacementCm': mesh_delta}

    horizontal = []
    for direction in (1, -1):
        path = cmds.curve(name='frameBoundaryPath', degree=3,
                          point=[(direction*x, x*epsilon, 0) for x in (0, 4, 8, 12)])
        node = prepare(path)
        shape = visible_curve_shape(path)
        previous = root_points(node)
        steps = []
        for slope in (0.0, -epsilon):
            for index, x in enumerate((0, 4, 8, 12)):
                cmds.xform('%s.cv[%d]' % (shape, index), objectSpace=True,
                           translation=(direction*x, x*slope, 0))
            current = root_points(node)
            steps.append(compare_roots(previous, current, 'Horizontal direction %d slope %.8f' % (direction, slope)))
            previous = current
        horizontal.append({'directionX': direction, 'steps': steps})

    # A common hanging braid starts exactly along -Y. A globally fixed +Y
    # reference has its antipodal singularity here, so tiny CV changes must be
    # checked from the exact vertical state as well as across either side.
    path = cmds.curve(name='downwardFramePath', degree=3,
                      point=[(0, y, 0) for y in (0, -4, -8, -12)])
    node = prepare(path)
    shape = visible_curve_shape(path)
    previous = root_points(node)
    downward = []
    for x in (epsilon, -epsilon):
        cmds.xform(shape+'.cv[1]', objectSpace=True, translation=(x, -4, 0))
        current = root_points(node)
        downward.append(compare_roots(previous, current, 'Downward -Y first CV x %.8f' % x))
        previous = current
    return {'tangentEpsilon': epsilon, 'horizontalX': horizontal, 'downwardY': downward,
            'maximumAcceptedDisplacementCm': .002}


def test_native_rebuild(history):
    new_scene()
    path = curve('nativeRebuildPath', 's')
    node = create_one(path)
    api.set_parameter([node], 'orientation', 23.0)
    initial = state(node)
    params = initial['params']
    details = []
    for spans in (14, 5):
        before_count = om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs
        before_length = cmds.getAttr(node+'.outLength')
        cmds.rebuildCurve(path, constructionHistory=history, replaceOriginal=True,
                          rebuildType=0, degree=3, spans=spans, keepRange=0,
                          keepControlPoints=False, keepEndPoints=True, keepTangents=True, tolerance=.001)
        drain_events()
        rebuilt_count = om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs
        check(rebuilt_count != before_count, 'Native rebuild did not alter CV count; test is ineffective.')
        check(cmds.getAttr(node+'.outStatus') == 'OK', 'Native rebuild disconnected generator.')
        assert_live_curve_source(node)
        check(state(node)['params'] == params, 'Native rebuild reset parameters.')
        check(state(node)['uuids'] == initial['uuids'], 'Native rebuild replaced output meshes.')
        check(abs(cmds.getAttr(node+'.outLength')-before_length) < .15, 'Rebuild caused excessive unintended path change.')
        undo_name = cmds.undoInfo(query=True, undoName=True)
        cmds.undo(); drain_events()
        restored_count = om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs
        check(restored_count == before_count, 'One undo did not undo native rebuild (extra repair operation?).')
        check(cmds.getAttr(node+'.outStatus') == 'OK', 'Undoing native rebuild broke association.')
        assert_live_curve_source(node)
        cmds.redo(); drain_events()
        check(om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs == rebuilt_count, 'Native rebuild redo failed.')
        check(cmds.getAttr(node+'.outStatus') == 'OK', 'Redoing native rebuild broke association.')
        assert_live_curve_source(node)
        details.append({'spans': spans, 'beforeCVs': before_count, 'afterCVs': rebuilt_count, 'undoName': undo_name})
    return {'constructionHistory': history, 'rebuilds': details}


def test_tool_rebuild_and_rebind():
    new_scene()
    path = curve('toolRebuildPath')
    node = create_one(path)
    params = state(node)['params']
    for history in (True, False):
        before_count = om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs
        spans = 11 if before_count != 14 else 6
        api.rebuild_sources([node], spans=spans, history=history)
        drain_events()
        after_count = om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs
        check(after_count != before_count, 'Tool rebuild did not rebuild.')
        check(state(node)['params'] == params, 'Tool rebuild reset parameters.')
        assert_live_curve_source(node)
        cmds.undo(); drain_events()
        check(om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs == before_count, 'Tool rebuild is not a one-step undo.')
        assert_live_curve_source(node)
        cmds.redo(); drain_events()
        check(om.MFnNurbsCurve(dag(visible_curve_shape(path))).numCVs == after_count, 'Tool rebuild redo failed.')
        assert_live_curve_source(node)
    replacement = curve('replacementPath', 'u', 9)
    shape_ids = state(node)['uuids']
    api.rebind(node, replacement)
    check(source_transform(node) == cmds.ls(replacement, long=True)[0], 'Rebind did not associate new curve.')
    check(distance(mean_root(node), source_root(replacement)) < 1e-4, 'Rebind output still follows old curve.')
    check(state(node)['uuids'] == shape_ids, 'Rebind recreated output meshes.')
    cmds.undo(); drain_events()
    check(source_transform(node) == cmds.ls(path, long=True)[0], 'Rebind undo did not restore original source.')
    return {'toolRebuildHistoryModes': 2, 'rebindPreservedMeshes': True}


def test_extract_guides_and_bake():
    new_scene()
    path = curve('extractPath', 's')
    node = create_one(path)
    cmds.setAttr(path+'.translate', 4, -2, 7, type='double3')
    cmds.setAttr(path+'.rotate', 13, 27, -9, type='double3')
    root = asset_root(node)
    cmds.setAttr(root+'.translate', -3, 5, 2, type='double3')
    cmds.setAttr(root+'.rotate', -11, 8, 31, type='double3')
    cmds.setAttr(root+'.scale', .8, 1.3, 1.1, type='double3')
    material = cmds.shadingNode('lambert', asShader=True, name='braidTestMaterial')
    shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                              name='braidTestMaterialSG')
    cmds.connectAttr(material+'.outColor', shading_group+'.surfaceShader')
    cmds.sets(mesh_shapes(node)[1], edit=True, forceElement=shading_group)
    original_shaders = state(node)['shaders']
    previous_samples = cmds.getAttr(node+'.guideSamples')
    cmds.setAttr(node+'.guideSamples', 193)
    expected_guides = [output_guide_world_points(node, i) for i in range(27)]
    cmds.setAttr(node+'.guideSamples', previous_samples)
    original_meshes = [mesh_points(s) for s in mesh_shapes(node)]
    groups = api.extract_guides([node], root_surfaces=True, samples=193)
    check(len(groups) == 1, 'Expected one extraction group per braid.')
    check(cmds.getAttr(node+'.guideSamples') == previous_samples, 'Extraction changed live guide resolution.')
    curves = cmds.listRelatives(groups[0], allDescendents=True, type='nurbsCurve', fullPath=True) or []
    roots = cmds.listRelatives(groups[0], allDescendents=True, type='mesh', fullPath=True) or []
    check(len(curves) == 27 and len(roots) == 3, 'Extraction must create 27 curves and 3 root disks.')
    curve_state = {cmds.ls(s,uuid=True)[0]: curve_points(s) for s in curves}
    strand_groups = {}
    matched_indices = set()
    for shape in curves:
        check(not cmds.listConnections(shape+'.create', source=True, destination=False), 'Extracted curve has live geometry input.')
        fn = om.MFnNurbsCurve(dag(shape))
        check(fn.form == om.MFnNurbsCurve.kOpen and fn.length() > 0, 'Guide has no open root-to-tip direction.')
        points = curve_points(shape)
        matches = [i for i, expected in enumerate(expected_guides)
                   if len(points) == len(expected) and max_point_delta(points, expected) < 5e-4]
        check(len(matches) == 1, 'Snapshot guide points or root-to-tip order differ from native guide output.')
        index = matches[0]
        check(index not in matched_indices, 'Two snapshot curves duplicate the same native guide.')
        matched_indices.add(index)
        transform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        group = cmds.listRelatives(transform, parent=True, fullPath=True)[0]
        strand_groups.setdefault(group, []).append(index)
    check(len(strand_groups) == 3, 'Guides must have one dedicated group per strand.')
    check(all(len(indices) == 9 and len({i//9 for i in indices}) == 1
              for indices in strand_groups.values()), 'Strand groups mix guides from different strands.')
    all_roots = [curve_points(s)[0] for s in curves]
    for shape in roots:
        pts = mesh_points(shape)
        fn = om.MFnMesh(dag(shape))
        check(fn.numVertices == 9 and fn.numPolygons == 8 and fn.numUVs() >= 9, 'Root disk topology or UVs are wrong.')
        check(all(min(distance(p,r) for r in all_roots) < 5e-4 for p in pts), 'Root disk vertices do not match guide roots.')
    baked = api.bake([node])
    check(len(baked) == 1, 'Expected one baked group per braid.')
    meshes = cmds.listRelatives(baked[0], allDescendents=True, type='mesh', fullPath=True) or []
    check(len(meshes) == 3, 'Bake did not preserve the three strand meshes.')
    baked_state = {s: mesh_points(s) for s in meshes}
    matched_meshes = set()
    for shape in meshes:
        check(not cmds.listConnections(shape+'.inMesh', source=True, destination=False), 'Baked mesh has a live geometry input.')
        check(not [n for n in cmds.listHistory(shape) or [] if cmds.nodeType(n)=='braidToolsGenerator'], 'Bake depends on generator.')
        points = baked_state[shape]
        matches = [i for i, expected in enumerate(original_meshes)
                   if len(points) == len(expected) and max_point_delta(points, expected) < 5e-4]
        check(len(matches) == 1 and matches[0] not in matched_meshes,
              'Bake did not preserve each source strand in world coordinates.')
        matched_meshes.add(matches[0])
        baked_shaders = sorted(cmds.listConnections(shape+'.instObjGroups', type='shadingEngine') or [])
        check(baked_shaders == original_shaders[matches[0]], 'Bake did not retain strand material association.')
    api.set_parameter([node], 'size', 1.8)
    api.set_parameter([node], 'radialSides', 19)
    for shape in curves:
        check(curve_points(shape) == curve_state[cmds.ls(shape,uuid=True)[0]], 'Changing source modified extracted guides.')
    for shape, pts in baked_state.items():
        check(mesh_points(shape) == pts, 'Changing source modified baked geometry.')
    second = api.extract_guides([node], root_surfaces=False, samples=193)
    check(len(second)==1 and second[0]!=groups[0] and cmds.objExists(groups[0]), 'Re-extraction overwrote prior snapshot.')
    check(len(cmds.listRelatives(second[0],ad=True,type='mesh') or []) == 0, 'Root surface opt-out ignored.')
    cmds.select(groups + baked, replace=True)
    detached_path = OUT/'detached_snapshots.ma'
    cmds.file(str(detached_path), force=True, exportSelected=True, type='mayaAscii', options='v=0;', preserveReferences=False)
    text = detached_path.read_text(encoding='utf-8',errors='replace')
    check('createNode braidToolsGenerator ' not in text, 'Exported snapshots still include generator nodes.')
    return {'snapshotCurves': 27, 'rootDisks': 3, 'bakedMeshes': 3, 'exportedScene': str(detached_path)}


def test_save_reopen_and_ui_independence():
    new_scene()
    path = curve('persistentPath', 'u')
    node = create_one(path)
    api.set_parameter([node], 'size', 1.12)
    api.set_parameter([node], 'orientation', -21.0)
    set_ramp(node, 'sizeRamp', [(0,1),(.7,.8),(1,.4)])
    set_ramp(node, 'rotationRamp', [(0,0),(.5,65),(1,0)])
    before = state(node)
    scene = OUT/'BraidTools_validation_scene.ma'
    cmds.file(rename=str(scene))
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(scene), open=True, force=True, prompt=False, executeScriptNodes=False)
    drain_events()
    nodes = cmds.ls(type='braidToolsGenerator') or []
    check(len(nodes)==1, 'Saved scene lost generator.')
    node=nodes[0]
    after=state(node)
    check(after['params']==before['params'], 'Saved parameters changed on reopen.')
    check(max(max_point_delta(a,b) for a,b in zip(before['points'],after['points'])) < 1e-4, 'Saved geometry changed on reopen.')
    source=source_transform(node)
    shape=visible_curve_shape(source)
    p=cmds.pointPosition(shape+'.cv[3]',world=True)
    cmds.xform(shape+'.cv[3]',worldSpace=True,translation=(p[0]+.4,p[1],p[2]))
    check(state(node)['points']!=after['points'], 'Reopened scene cannot update without opening tool UI.')
    return {'savedScene':str(scene),'updatedAfterReopenWithoutUI':True}


def test_invalid_inputs_and_missing_source():
    new_scene()
    invalid=cmds.polyCube(name='notACurve',constructionHistory=False)[0]
    messages=[]
    callback=om.MCommandMessage.addCommandOutputCallback(lambda *args:messages.append(str(args[0])))
    try:
        try: api.create([invalid])
        except Exception as exc: messages.append(str(exc))
    finally: om.MMessage.removeCallback(callback)
    check(not cmds.ls(type='braidToolsGenerator'), 'Invalid selection created a partial generator.')
    check(messages, 'Invalid selection did not produce an actionable message.')
    zero=curve('zeroCurve','zero')
    native=cmds.createNode('braidToolsGenerator',name='invalidNative')
    cmds.connectAttr(visible_curve_shape(zero)+'.worldSpace[0]',native+'.inputCurve')
    cmds.connectAttr(zero+'.message',native+'.sourceCurve')
    try: status=cmds.getAttr(native+'.outStatus')
    except RuntimeError as exc: status=str(exc)
    check(status and status!='OK', 'Zero-length curve was treated as valid.')
    check(any(word in status.lower() for word in ('empty', 'shorter', 'zero', 'length')),
          'Zero-length fixture failed for an unrelated connection/identity reason: '+status)
    node=create_one(curve('toRemove'))
    source=source_transform(node)
    cmds.delete(source)
    drain_events()
    try: missing=cmds.getAttr(node+'.outStatus')
    except RuntimeError as exc: missing=str(exc)
    check(missing and missing!='OK','Missing source was treated as valid.')
    check(cmds.objExists(node),'Deleting source unexpectedly removed editable generator parameters.')
    same_name = curve(source.rsplit('|', 1)[-1], 'straight', 8)
    drain_events()
    check(not (cmds.listConnections(node+'.sourceCurve', source=True, destination=False) or []),
          'A new source with the deleted name was implicitly claimed.')
    check(cmds.getAttr(node+'.outStatus') != 'OK', 'Same-name replacement silently recovered missing source.')
    replacement=curve('explicitReplacement','straight',4)
    api.rebind(node,replacement)
    check(cmds.getAttr(node+'.outStatus')=='OK','Explicit rebind did not recover missing source.')
    return {'invalidSelectionMessages':messages[:3],'zeroLengthStatus':status,'missingSourceStatus':missing}


def test_resolution_limits_and_recovery():
    new_scene()
    node = create_one(curve('resolutionLimitPath', 'straight'))
    api.set_density_mode([node], 1)
    api.set_parameter([node], 'cycles', 200)
    mesh_status = cmds.getAttr(node+'.outStatus')
    check(mesh_status != 'OK' and '8192' in mesh_status,
          'Excessive automatic mesh resolution must return an actionable limit message.')
    api.set_parameter([node], 'longitudinalMode', 1)
    api.set_parameter([node], 'lengthSegments', 256)
    check(cmds.getAttr(node+'.outStatus') == 'OK', 'Manual resolution did not recover a valid mesh.')
    manual_meshes = [mesh_points(shape) for shape in mesh_shapes(node)]
    check(all(points for points in manual_meshes), 'Manual-resolution mesh is empty.')
    # Request the lazy guide output directly: its independent safety limit is
    # expected to fail without invalidating an already valid preview mesh.
    dep(node).findPlug('outGuideCurves', False).elementByLogicalIndex(0).asMObject()
    guide_status = cmds.getAttr(node+'.outStatus')
    check(guide_status != 'OK' and '8193' in guide_status,
          'Excessive automatic guide sampling must return an actionable limit message.')
    check([mesh_points(shape) for shape in mesh_shapes(node)] == manual_meshes,
          'A guide-only sampling error cleared or changed the valid preview mesh.')
    api.set_parameter([node], 'guideSamples', 64)
    check(node_guide(node, 0)['points'], 'Explicit guide sampling failed to recover guide output.')
    check(cmds.getAttr(node+'.outStatus') == 'OK', 'Explicit guide sampling did not clear the error status.')
    groups = api.extract_guides([node], root_surfaces=False, samples=64)
    curves = cmds.listRelatives(groups[0], allDescendents=True, type='nurbsCurve') or []
    check(len(curves) == 27, 'Explicit-sampling extraction did not recover all 27 guides.')
    return {'automaticMeshStatus': mesh_status, 'automaticGuideStatus': guide_status,
            'manualMeshRetained': True, 'explicitGuideSamples': 64, 'recoveredSnapshotCurves': 27}


def test_degenerate_root_extraction_is_atomic():
    new_scene()
    valid, tapered = api.create([curve('validExtractionPath'), curve('zeroRootPath', 's', 5)])
    api.set_ramp([tapered], 'sizeRamp', [(0,0,1), (.2,1,1), (1,1,1)])
    expected_samples = {n: cmds.getAttr(n+'.guideSamples') for n in (valid, tapered)}
    errors = []
    for nodes in ([tapered], [valid, tapered]):
        before = set(cmds.ls(dag=True, uuid=True) or [])
        error = None
        try:
            api.extract_guides(nodes, root_surfaces=True, samples=193)
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
        check(error, 'Zero-size root surface extraction must fail explicitly.')
        # Require a useful root/surface/degeneracy message, rather than exposing
        # an unrelated Maya command error after partially creating snapshots.
        check(any(word in error.lower() for word in ('根', 'root', '退化', 'degenerate')),
              'Degenerate root error does not identify the invalid root surface: ' + error)
        check(set(cmds.ls(dag=True, uuid=True) or []) == before,
              'Failed extraction left partial snapshot DAG objects; batch validation must precede creation.')
        check(all(cmds.getAttr(n+'.guideSamples') == expected_samples[n] for n in (valid, tapered)),
              'Failed extraction changed a live guide sampling setting.')
        errors.append(error)
    groups = api.extract_guides([tapered], root_surfaces=False, samples=193)
    check(len(groups) == 1, 'Zero-size root must permit curve-only extraction.')
    curves = cmds.listRelatives(groups[0], allDescendents=True, type='nurbsCurve', fullPath=True) or []
    meshes = cmds.listRelatives(groups[0], allDescendents=True, type='mesh', fullPath=True) or []
    check(len(curves) == 27 and not meshes, 'Curve-only extraction must create 27 guides and no root surfaces.')
    for shape in curves:
        fn = om.MFnNurbsCurve(dag(shape))
        check(fn.form == om.MFnNurbsCurve.kOpen and fn.length() > 0,
              'Curve-only extraction returned an invalid zero-root guide.')
    return {'singleAndBatchErrors': errors, 'failedOperationsAddedDAGObjects': False,
            'curveOnlyGuideCount': len(curves)}


def test_performance_twenty_assets():
    new_scene()
    paths=[curve('perfPath%02d'%i,'s',i*3.0) for i in range(20)]
    nodes=api.create(paths)
    check(len(nodes)==20,'Performance fixture did not create twenty assets.')
    baseline={n:[point_digest(node_mesh_points(n,i)) for i in range(3)] for n in nodes}
    dirtied={n:0 for n in nodes}
    callbacks=[]
    for n in nodes:
        def notify(*_,name=n): dirtied[name]+=1
        callbacks.append(om.MNodeMessage.addNodeDirtyPlugCallback(dep(n).object(),notify))
    times=[]
    shape=visible_curve_shape(paths[0]); origin=cmds.pointPosition(shape+'.cv[2]',world=True)
    try:
        for i in range(45):
            dx=.45*math.sin((i+1)*.37)
            start=time.perf_counter()
            cmds.xform(shape+'.cv[2]',worldSpace=True,translation=(origin[0]+dx,origin[1],origin[2]))
            for strand in range(3): node_mesh_points(nodes[0],strand)
            times.append((time.perf_counter()-start)*1000)
    finally:
        for cb in callbacks:om.MMessage.removeCallback(cb)
    other_changes=[]
    for n in nodes[1:]:
        if baseline[n]!=[point_digest(node_mesh_points(n,i)) for i in range(3)]: other_changes.append(n)
    other_dirty=[n for n in nodes[1:] if dirtied[n]]
    check(not other_changes,'Editing one braid changed other braid output data.')
    check(not other_dirty,'Editing one braid dirtied unrelated generators: '+str(other_dirty))
    ordered=sorted(times[3:]); p95=ordered[min(len(ordered)-1,math.ceil(len(ordered)*.95)-1)]
    result={'assets':20,'editedAsset':nodes[0],'iterations':len(times),'medianMs':statistics.median(ordered),
            'p95Ms':p95,'maxMs':max(ordered),'dirtyCallbacks':dirtied,'otherOutputsUnchanged':True,
            'measurement':'CV edit + evaluation/readback of three mesh outputs; excludes viewport rendering.',
            'acceptanceLimitMs':ARGS.performance_limit_ms}
    (OUT/'performance_detail.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    check(p95<=ARGS.performance_limit_ms,'Default-resolution edit/evaluation p95 %.2f ms exceeds %.2f ms.'%(p95,ARGS.performance_limit_ms))
    return result


def run_case(name,callback):
    entry={'name':name,'status':'running'}
    REPORT['cases'].append(entry);save_report()
    start=time.perf_counter()
    try:
        entry['details']=callback()
        entry['status']='passed'
    except Exception as exc:
        entry['status']='failed';entry['error']=str(exc);entry['traceback']=traceback.format_exc()
    entry['seconds']=time.perf_counter()-start
    print('BRAID_TEST '+name+' '+entry['status'],flush=True)
    save_report()


initialized=False
try:
    save_report()
    import maya.standalone
    maya.standalone.initialize(name='python');initialized=True
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    cmds.loadPlugin(str(PLUGIN),quiet=True)
    api=importlib.import_module(ARGS.api)
    REPORT.update(status='running',mayaVersion=cmds.about(version=True),apiModule=api.__file__)
    if ARGS.phase!='performance':
        run_case('create_multi_and_repeat',test_create_and_selection)
        run_case('create_undo_redo',test_create_undo_redo)
        run_case('continuous_cv_batch_and_undo',test_continuous_cv_and_batch_parameters)
        run_case('density_modes_and_length',test_density_switch_and_length)
        run_case('localized_size_and_rotation_ramps',test_local_ramps)
        run_case('batch_ramp_scope_and_undo',test_batch_ramp_scope_and_undo)
        run_case('mesh_resolution_independent_guides',test_resolution_and_guide_independence)
    if ARGS.phase=='full':
        run_case('names_parenting_nonuniform_scale',test_names_parenting_and_nonuniform_scale)
        run_case('initial_frame_continuity',test_initial_frame_continuity)
        run_case('native_rebuild_history_on',lambda:test_native_rebuild(True))
        run_case('native_rebuild_history_off',lambda:test_native_rebuild(False))
        run_case('tool_rebuild_and_rebind',test_tool_rebuild_and_rebind)
        run_case('static_guides_root_disks_and_bake',test_extract_guides_and_bake)
        run_case('save_reopen_without_ui',test_save_reopen_and_ui_independence)
        run_case('invalid_and_missing_inputs',test_invalid_inputs_and_missing_source)
        run_case('resolution_limits_and_recovery',test_resolution_limits_and_recovery)
        run_case('degenerate_root_extraction_is_atomic',test_degenerate_root_extraction_is_atomic)
    if ARGS.phase in ('full','performance'):
        run_case('twenty_asset_performance',test_performance_twenty_assets)
    failures=[c['name'] for c in REPORT['cases'] if c['status']!='passed']
    REPORT.update(complete=True,status='failed' if failures else 'passed',failedCases=failures)
except Exception as exc:
    REPORT.update(status='initialization_failed',error=str(exc),traceback=traceback.format_exc())
finally:
    save_report()
    if initialized:
        try:
            cmds.file(new=True,force=True)
            maya.standalone.uninitialize()
        except Exception as exc:
            REPORT['teardownError']=str(exc);save_report()

print(json.dumps({'status':REPORT['status'],'report':str(REPORT_PATH),'cases':len(REPORT['cases'])}),flush=True)
sys.exit(0 if REPORT['status']=='passed' else 1)
