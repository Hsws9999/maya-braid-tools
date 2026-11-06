// BraidTools -- native dependency-graph braid generator for Autodesk Maya.
// All geometry is returned as data. compute() never changes the scene DAG.
#include <maya/MPxNode.h>
#include <maya/MFnPlugin.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnEnumAttribute.h>
#include <maya/MFnMessageAttribute.h>
#include <maya/MFnStringData.h>
#include <maya/MFnNurbsCurve.h>
#include <maya/MFnNurbsCurveData.h>
#include <maya/MFnMesh.h>
#include <maya/MFnMeshData.h>
#include <maya/MRampAttribute.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MArrayDataHandle.h>
#include <maya/MArrayDataBuilder.h>
#include <maya/MPlug.h>
#include <maya/MGlobal.h>
#include <maya/MPointArray.h>
#include <maya/MFloatArray.h>
#include <maya/MDoubleArray.h>
#include <maya/MIntArray.h>
#include <maya/MMatrix.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef BRAIDTOOLS_NODE_ID
#define BRAIDTOOLS_NODE_ID 0x0007B7A1
#endif
#if defined(BRAIDTOOLS_PUBLIC_RELEASE) && BRAIDTOOLS_PUBLIC_RELEASE
#if !defined(BRAIDTOOLS_REGISTERED_NODE_ID_CONFIRMED) || !BRAIDTOOLS_REGISTERED_NODE_ID_CONFIRMED
#error Public releases require an explicitly confirmed registered Maya node type ID.
#endif
#endif

namespace {
constexpr std::uint64_t kNodeTypeId = BRAIDTOOLS_NODE_ID;
static_assert(kNodeTypeId > 0 && kNodeTypeId <= 0xFFFFFFFFULL, "Maya node type IDs must fit a nonzero unsigned 32-bit integer");
#if defined(BRAIDTOOLS_PUBLIC_RELEASE) && BRAIDTOOLS_PUBLIC_RELEASE
static_assert(kNodeTypeId != 0x0007B7A1, "The development node ID is forbidden in public releases");
#endif
constexpr double kPi = 3.1415926535897932384626433832795;
constexpr double kTau = 2.0 * kPi;
constexpr double kEpsilon = 1.0e-10;
constexpr int kMaxSegments = 8192;

void check(const MStatus& status, const char* context) {
    if (!status) throw std::runtime_error(std::string(context) + ": " + status.errorString().asChar());
}
void sampleRamp(MRampAttribute& ramp, MFloatArray& positions, MFloatArray& values, const char* context) {
    MStatus status;
#if MAYA_API_VERSION >= 20260000
    ramp.getValuesAtPositions(positions, values, &status);
    check(status, context);
#else
    // Batch ramp sampling was added in Maya 2026. Earlier SDKs evaluate the
    // same ramp at the same normalized positions one sample at a time.
    values.setLength(positions.length());
    for (unsigned int i = 0; i < positions.length(); ++i) {
        ramp.getValueAtPosition(positions[i], values[i], &status);
        check(status, context);
    }
#endif
}
bool finite(const MVector& v) { return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z); }
bool finite(const MPoint& p) { return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z); }
MVector unit(const MVector& v, const char* context) {
    const double length = v.length();
    if (!std::isfinite(length) || length < kEpsilon) throw std::runtime_error(context);
    return v / length;
}
MVector perpendicular(const MVector& t, MVector preferred = MVector(1, 0, 0)) {
    MVector n = preferred - t * (preferred * t);
    if (n.length() < 1.0e-6) {
        const std::array<MVector,3> axes = { MVector(1,0,0), MVector(0,1,0), MVector(0,0,1) };
        preferred = *std::min_element(axes.begin(), axes.end(), [&](const MVector& a, const MVector& b) {
            return std::abs(a*t) < std::abs(b*t);
        });
        n = preferred - t * (preferred * t);
    }
    return unit(n, "Cannot construct a frame for this source curve");
}
MVector initialPathNormal(const MVector& t, const MVector& referenceT, const MVector& referenceN) {
    // The reference is captured once in source-local coordinates by create/rebind
    // and stored on the node. Transforming it with the source keeps parent edits
    // coherent; there is no fixed world-axis pole near the initial root direction.
    MVector axis = referenceT ^ t;
    const double sine = axis.length(), cosine = std::clamp(referenceT*t, -1.0, 1.0);
    if (sine < kEpsilon) return referenceN;
    axis /= sine;
    const MVector normal = referenceN*cosine + (axis ^ referenceN)*sine
                         + axis*(axis*referenceN)*(1.0-cosine);
    return unit(normal-t*(normal*t), "Cannot construct the initial source frame");
}
struct Frame { MVector t, n, b; };
std::vector<Frame> transportFrames(const std::vector<MPoint>& points,
                                 const std::vector<MVector>* givenTangents = nullptr,
                                 MVector preferred = MVector(1, 0, 0),
                                 MVector referenceT = MVector(0, 1, 0)) {
    std::vector<Frame> result(points.size());
    MVector oldT, normal;
    for (size_t i = 0; i < points.size(); ++i) {
        MVector t = givenTangents ? (*givenTangents)[i] :
            points[std::min(i+1, points.size()-1)] - points[i ? i-1 : 0];
        t = unit(t, "Source or strand contains coincident samples or a tangent reversal");
        if (!i) normal = givenTangents ? initialPathNormal(t, referenceT, preferred) : perpendicular(t, preferred);
        else {
            MVector axis = oldT ^ t;
            const double s = axis.length(), c = std::clamp(oldT*t, -1.0, 1.0);
            if (s > kEpsilon) {
                axis /= s;
                normal = normal*c + (axis ^ normal)*s + axis*(axis*normal)*(1.0-c);
            } else if (c < 0.0) {
                throw std::runtime_error("Source has a 180-degree tangent reversal; smooth or rebuild it");
            }
            normal = unit(normal-t*(normal*t), "Unable to transport the braid frame");
        }
        result[i] = {t, normal, unit(t ^ normal, "Invalid braid frame")};
        oldT = t;
    }
    return result;
}
struct Settings {
    double length=0, cycles=0, size=1, flatness=1, thickness=1, strandFlatness=1;
    double orientation=0, phase=0, quality=1;
    int segments=256, radialSides=12, guideSamples=0;
    MMatrix parentInverse;
    MVector referenceT = MVector(0, 1, 0), referenceN = MVector(1, 0, 0);
};
struct Strand {
    std::vector<MPoint> centers;
    std::vector<Frame> frames;
    std::vector<double> radii;
};
}

class BraidGenerator final : public MPxNode {
public:
    static MTypeId id;
    static MObject inputCurve, outputParentInverseMatrix, sourceCurve, assetRoot;
    static MObject frameReferenceTangent, frameReferenceNormal, sourceWorldMatrix;
    static MObject densityMode, pitch, cycles, size, flatness, strandThickness, strandFlatness;
    static MObject orientation, phase, sizeRamp, rotationRamp;
    static MObject longitudinalMode, lengthSegments, radialSides, autoQuality, guideSamples;
    static MObject outMesh, outGuideCurves, outLength, outCycles, outSegments, outStatus;
    static void* creator() { return new BraidGenerator; }
    static MStatus initialize();
    void postConstructor() override;
    MStatus compute(const MPlug&, MDataBlock&) override;
    SchedulingType schedulingType() const override { return kGloballySerial; }
private:
    Settings readSettings(MDataBlock&, MFnNurbsCurve&);
    std::array<Strand,3> sample(MFnNurbsCurve&, const Settings&, int count);
    MObject makeMesh(const Strand&, const Settings&);
    MObject makeGuide(const Strand&, const Settings&, int guide);
    void clearGeometry(MDataBlock&, const MObject&);
};

// Preserve the development ID for existing local scenes. Public builds must
// explicitly use a type ID registered to the project.
MTypeId BraidGenerator::id(static_cast<unsigned int>(kNodeTypeId));
#define ATTR(name) MObject BraidGenerator::name;
ATTR(inputCurve) ATTR(outputParentInverseMatrix) ATTR(sourceCurve) ATTR(assetRoot)
ATTR(frameReferenceTangent) ATTR(frameReferenceNormal) ATTR(sourceWorldMatrix)
ATTR(densityMode) ATTR(pitch) ATTR(cycles) ATTR(size) ATTR(flatness) ATTR(strandThickness) ATTR(strandFlatness)
ATTR(orientation) ATTR(phase) ATTR(sizeRamp) ATTR(rotationRamp)
ATTR(longitudinalMode) ATTR(lengthSegments) ATTR(radialSides) ATTR(autoQuality) ATTR(guideSamples)
ATTR(outMesh) ATTR(outGuideCurves) ATTR(outLength) ATTR(outCycles) ATTR(outSegments) ATTR(outStatus)
#undef ATTR

MStatus BraidGenerator::initialize() {
    MStatus st;
    MFnTypedAttribute typed;
    MFnNumericAttribute number;
    MFnMatrixAttribute matrix;
    MFnEnumAttribute enumeration;
    MFnMessageAttribute messageAttr;
    std::vector<MObject> inputs, outputs;
    auto addInput = [&](MObject obj) { inputs.push_back(obj); return addAttribute(obj); };
    auto scalar = [&](MObject& obj, const char* name, const char* shortName, double value, double minimum, double maximum) {
        obj = number.create(name, shortName, MFnNumericData::kDouble, value);
        number.setKeyable(true); number.setMin(minimum); number.setMax(maximum); addInput(obj);
    };
    auto integer = [&](MObject& obj, const char* name, const char* shortName, int value, int minimum, int maximum) {
        obj = number.create(name, shortName, MFnNumericData::kInt, value);
        number.setKeyable(true); number.setMin(minimum); number.setMax(maximum); addInput(obj);
    };
    inputCurve = typed.create("inputCurve", "ic", MFnData::kNurbsCurve);
    typed.setStorable(false); typed.setDisconnectBehavior(MFnAttribute::kReset); addInput(inputCurve);
    outputParentInverseMatrix = matrix.create("outputParentInverseMatrix", "opim"); addInput(outputParentInverseMatrix);
    sourceCurve = messageAttr.create("sourceCurve", "sc"); addInput(sourceCurve);
    assetRoot = messageAttr.create("assetRoot", "ar"); addAttribute(assetRoot);
    frameReferenceTangent = number.create("frameReferenceTangent", "frt", MFnNumericData::k3Double);
    number.setDefault(0.0, 1.0, 0.0); number.setHidden(true); number.setStorable(true); addInput(frameReferenceTangent);
    frameReferenceNormal = number.create("frameReferenceNormal", "frn", MFnNumericData::k3Double);
    number.setDefault(1.0, 0.0, 0.0); number.setHidden(true); number.setStorable(true); addInput(frameReferenceNormal);
    sourceWorldMatrix = matrix.create("sourceWorldMatrix", "swm");
    matrix.setHidden(true); matrix.setStorable(true); addInput(sourceWorldMatrix);
    densityMode = enumeration.create("densityMode", "dm", 0);
    enumeration.addField("pitch", 0); enumeration.addField("fixedCycles", 1); enumeration.setKeyable(true); addInput(densityMode);
    scalar(pitch, "pitch", "pt", 4.0, 0.001, 1000000.0);
    scalar(cycles, "cycles", "cy", 3.0, 0.001, 500.0);
    scalar(size, "size", "sz", 1.0, 0.0001, 1000000.0);
    scalar(flatness, "flatness", "fl", 1.0, 0.01, 10.0);
    scalar(strandThickness, "strandThickness", "sth", 1.0, 0.01, 4.0);
    scalar(strandFlatness, "strandFlatness", "sfl", 1.0, 0.01, 10.0);
    scalar(orientation, "orientation", "ori", 0.0, -360000.0, 360000.0);
    scalar(phase, "phase", "ph", 0.0, -360000.0, 360000.0);
    sizeRamp = MRampAttribute::createCurveRamp("sizeRamp", "sr", &st); if (!st) return st; addInput(sizeRamp);
    rotationRamp = MRampAttribute::createCurveRamp("rotationRamp", "rr", &st); if (!st) return st; addInput(rotationRamp);
    longitudinalMode = enumeration.create("longitudinalMode", "lm", 0);
    enumeration.addField("auto", 0); enumeration.addField("manual", 1); enumeration.setKeyable(true); addInput(longitudinalMode);
    integer(lengthSegments, "lengthSegments", "ls", 256, 8, kMaxSegments);
    integer(radialSides, "radialSides", "rs", 12, 3, 64);
    scalar(autoQuality, "autoQuality", "aq", 1.0, 0.1, 8.0);
    integer(guideSamples, "guideSamples", "gs", 0, 0, kMaxSegments+1);
    outMesh = typed.create("outMesh", "om", MFnData::kMesh);
    typed.setArray(true); typed.setUsesArrayDataBuilder(true); typed.setStorable(false); typed.setWritable(false);
    outputs.push_back(outMesh); addAttribute(outMesh);
    outGuideCurves = typed.create("outGuideCurves", "ogc", MFnData::kNurbsCurve);
    typed.setArray(true); typed.setUsesArrayDataBuilder(true); typed.setStorable(false); typed.setWritable(false);
    outputs.push_back(outGuideCurves); addAttribute(outGuideCurves);
    for (auto item : {std::make_pair(&outLength, "outLength"), std::make_pair(&outCycles, "outCycles")}) {
        *item.first = number.create(item.second, item.first == &outLength ? "ol" : "oc", MFnNumericData::kDouble, 0.0);
        number.setStorable(false); number.setWritable(false); outputs.push_back(*item.first); addAttribute(*item.first);
    }
    outSegments = number.create("outSegments", "os", MFnNumericData::kInt, 0);
    number.setStorable(false); number.setWritable(false); outputs.push_back(outSegments); addAttribute(outSegments);
    MFnStringData strings;
    outStatus = typed.create("outStatus", "ost", MFnData::kString, strings.create("Input curve is not connected"));
    typed.setStorable(false); typed.setWritable(false); outputs.push_back(outStatus); addAttribute(outStatus);
    for (const auto& in : inputs) for (const auto& out : outputs) { st = attributeAffects(in, out); if (!st) return st; }
    return MS::kSuccess;
}

void BraidGenerator::postConstructor() {
    MFloatArray positions, sizes, rotations;
    MIntArray interpolations;
    positions.append(0.0f); positions.append(1.0f);
    sizes.append(1.0f); sizes.append(1.0f);
    rotations.append(0.0f); rotations.append(0.0f);
    interpolations.append(MRampAttribute::kLinear); interpolations.append(MRampAttribute::kLinear);
    MRampAttribute(thisMObject(), sizeRamp).setRamp(sizes, positions, interpolations);
    MRampAttribute(thisMObject(), rotationRamp).setRamp(rotations, positions, interpolations);
}

Settings BraidGenerator::readSettings(MDataBlock& block, MFnNurbsCurve& curve) {
    if (curve.form() != MFnNurbsCurve::kOpen) throw std::runtime_error("Input must be an open NURBS curve");
    Settings s;
    s.length = curve.length(1.0e-6);
    if (!std::isfinite(s.length) || s.length < 1.0e-6) throw std::runtime_error("Input curve is empty or shorter than 0.000001 cm");
    const double p = block.inputValue(pitch).asDouble();
    s.cycles = block.inputValue(densityMode).asShort() == 0 ? s.length / p : block.inputValue(cycles).asDouble();
    s.size = block.inputValue(size).asDouble(); s.flatness = block.inputValue(flatness).asDouble();
    s.thickness = block.inputValue(strandThickness).asDouble(); s.strandFlatness = block.inputValue(strandFlatness).asDouble();
    s.orientation = block.inputValue(orientation).asDouble() * kPi / 180.0;
    s.phase = block.inputValue(phase).asDouble() * kPi / 180.0;
    s.quality = block.inputValue(autoQuality).asDouble();
    s.parentInverse = block.inputValue(outputParentInverseMatrix).asMatrix();
    const MMatrix sourceMatrix = block.inputValue(sourceWorldMatrix).asMatrix();
    auto tangentHandle = block.inputValue(frameReferenceTangent), normalHandle = block.inputValue(frameReferenceNormal);
    const double* referenceTangent = tangentHandle.asDouble3();
    const double* referenceNormal = normalHandle.asDouble3();
    s.referenceT = unit(MVector(referenceTangent[0], referenceTangent[1], referenceTangent[2])*sourceMatrix,
                       "Stored frame tangent or source transform is invalid; use Rebind");
    const MVector transformedN = MVector(referenceNormal[0], referenceNormal[1], referenceNormal[2])*sourceMatrix;
    s.referenceN = unit(transformedN-s.referenceT*(transformedN*s.referenceT),
                       "Stored frame normal or source transform is invalid; use Rebind");
    for (const double val : {s.cycles, s.size, s.flatness, s.thickness, s.strandFlatness, s.quality})
        if (!std::isfinite(val) || val <= 0.0) throw std::runtime_error("Braid dimensions and density must be finite positive values");
    if (!std::isfinite(s.orientation) || !std::isfinite(s.phase)) throw std::runtime_error("Braid angles must be finite");
    for (unsigned row = 0; row < 4; ++row) for (unsigned column = 0; column < 4; ++column)
        if (!std::isfinite(s.parentInverse[row][column])) throw std::runtime_error("Asset parent matrix contains nonfinite values");
    const double determinant = s.parentInverse.det4x4();
    if (!std::isfinite(determinant) || std::abs(determinant) < 1.0e-15)
        throw std::runtime_error("Asset parent scale is singular or too extreme; restore a usable scale");
    s.radialSides = std::clamp(block.inputValue(radialSides).asInt(), 3, 64);
    s.guideSamples = block.inputValue(guideSamples).asInt();
    if (s.guideSamples != 0) s.guideSamples = std::clamp(s.guideSamples, 16, kMaxSegments+1);
    if (block.inputValue(longitudinalMode).asShort() == 1) {
        s.segments = std::clamp(block.inputValue(lengthSegments).asInt(), 8, kMaxSegments);
    } else {
        // Density and curvature contribute independently. A curved source needs more samples
        // even when its braid is almost straight or has less than one complete cycle.
        double totalTurn = 0.0;
        MVector previous;
        const int probeCount = std::clamp(static_cast<int>(curve.numSpans()) * 4, 64, 512);
        for (int i = 0; i <= probeCount; ++i) {
            const double u = curve.findParamFromLength(s.length * i / probeCount);
            MVector t = unit(curve.tangent(u, MSpace::kObject), "Curve contains a zero tangent");
            if (i) totalTurn += std::acos(std::clamp(previous*t, -1.0, 1.0));
            previous = t;
        }
        const double desired = std::max({8.0, std::ceil(s.cycles*48.0*s.quality),
                                       std::ceil(totalTurn/(kPi/90.0)*s.quality)});
        if (desired > double(kMaxSegments))
            throw std::runtime_error("Automatic mesh resolution exceeds 8192 segments; increase pitch, lower cycles/quality, or use Manual resolution");
        s.segments = static_cast<int>(std::clamp(desired, 8.0, double(kMaxSegments)));
    }
    return s;
}

std::array<Strand,3> BraidGenerator::sample(MFnNurbsCurve& curve, const Settings& s, int count) {
    std::vector<MPoint> points(count);
    std::vector<MVector> tangents(count);
    MFloatArray positions, sizes, rotations;
    positions.setLength(count);
    for (int i = 0; i < count; ++i) {
        const double t = double(i)/(count-1);
        const double u = curve.findParamFromLength(s.length*t);
        check(curve.getPointAtParam(u, points[i], MSpace::kObject), "Sample source curve");
        tangents[i] = curve.tangent(u, MSpace::kObject);
        if (!finite(points[i]) || !finite(tangents[i])) throw std::runtime_error("Input curve contains nonfinite coordinates");
        positions[i] = static_cast<float>(t);
    }
    auto frames = transportFrames(points, &tangents, s.referenceN, s.referenceT);
    MStatus st;
    MRampAttribute rampSize(thisMObject(), sizeRamp, &st); check(st, "Read size ramp");
    MRampAttribute rampRotation(thisMObject(), rotationRamp, &st); check(st, "Read rotation ramp");
    sampleRamp(rampSize, positions, sizes, "Evaluate size ramp");
    sampleRamp(rampRotation, positions, rotations, "Evaluate rotation ramp");
    if (sizes.length() != unsigned(count) || rotations.length() != unsigned(count)) throw std::runtime_error("Ramp evaluation failed");
    std::array<Strand,3> strands;
    for (int k = 0; k < 3; ++k) {
        auto& strand = strands[k]; strand.centers.resize(count); strand.radii.resize(count);
        MVector initialNormal;
        for (int i = 0; i < count; ++i) {
            if (!std::isfinite(sizes[i]) || !std::isfinite(rotations[i])) throw std::runtime_error("Ramp entries must be finite");
            const double t = double(i)/(count-1), scale = std::max(0.0, double(sizes[i]));
            const double angle = s.orientation + rotations[i]*kPi/180.0;
            const MVector n = frames[i].n*std::cos(angle) + frames[i].b*std::sin(angle);
            const MVector b = frames[i].b*std::cos(angle) - frames[i].n*std::sin(angle);
            const double theta = kTau*(s.cycles*t + double(k)/3.0) + s.phase;
            strand.centers[i] = points[i] + n*(0.35*s.size*scale*std::cos(theta))
                                          + b*(0.20*s.size*s.flatness*scale*std::sin(2.0*theta));
            strand.radii[i] = 0.15*s.size*s.thickness*scale;
            if (!i) initialNormal = n;
        }
        strand.frames = transportFrames(strand.centers, nullptr, initialNormal);
    }
    return strands;
}

MObject BraidGenerator::makeMesh(const Strand& strand, const Settings& s) {
    const int rows = static_cast<int>(strand.centers.size()), sides = s.radialSides;
    MPointArray points;
    MIntArray counts, vertices, uvIds;
    MFloatArray u, v;
    points.setLength(rows*sides); counts.setLength((rows-1)*sides);
    vertices.setLength((rows-1)*sides*4); uvIds.setLength((rows-1)*sides*4);
    u.setLength(rows*(sides+1)); v.setLength(rows*(sides+1));
    for (int i = 0; i < rows; ++i) {
        const auto& frame = strand.frames[i]; const double radius = strand.radii[i];
        for (int j = 0; j < sides; ++j) {
            const double a = kTau*j/sides;
            MPoint point = strand.centers[i] + frame.n*(radius*std::cos(a)) + frame.b*(radius*s.strandFlatness*std::sin(a));
            points[i*sides+j] = point * s.parentInverse;
        }
        for (int j = 0; j <= sides; ++j) { u[i*(sides+1)+j] = float(j)/sides; v[i*(sides+1)+j] = float(i)/(rows-1); }
    }
    for (int i = 0; i < rows-1; ++i) for (int j = 0; j < sides; ++j) {
        const int face = i*sides+j, a = i*sides+j, b = i*sides+(j+1)%sides;
        counts[face] = 4;
        vertices[face*4] = a; vertices[face*4+1] = b;
        vertices[face*4+2] = b+sides; vertices[face*4+3] = a+sides;
        const int uv = i*(sides+1)+j;
        uvIds[face*4] = uv; uvIds[face*4+1] = uv+1;
        uvIds[face*4+2] = uv+sides+2; uvIds[face*4+3] = uv+sides+1;
    }
    MStatus st;
    MFnMeshData data; MObject result = data.create(&st); check(st, "Create output mesh data");
    MFnMesh mesh;
    mesh.create(points.length(), counts.length(), points, counts, vertices, result, &st); check(st, "Create braid mesh");
    check(mesh.setUVs(u, v), "Set braid UVs"); check(mesh.assignUVs(counts, uvIds), "Assign braid UVs");
    for (int edge = 0; edge < mesh.numEdges(); ++edge) check(mesh.setEdgeSmoothing(edge, true), "Smooth braid edge");
    check(mesh.cleanupEdgeSmoothing(), "Finish braid smoothing");
    return result;
}

MObject BraidGenerator::makeGuide(const Strand& strand, const Settings& s, int guide) {
    MPointArray points;
    points.setLength(static_cast<unsigned>(strand.centers.size()));
    const double ring = guide == 0 ? 0.0 : 0.96, angle = kTau*(guide-1)/8.0;
    for (unsigned i = 0; i < points.length(); ++i) {
        const auto& frame = strand.frames[i]; const double r = strand.radii[i]*ring;
        points[i] = (strand.centers[i] + frame.n*(r*std::cos(angle))
                   + frame.b*(r*s.strandFlatness*std::sin(angle))) * s.parentInverse;
    }
    // Interpolating edit points keep the extracted guide roots on their exact tube roots.
    MStatus st;
    MFnNurbsCurveData data; MObject result = data.create(&st); check(st, "Create guide data");
    MFnNurbsCurve guideCurve;
    guideCurve.createWithEditPoints(points, 3, MFnNurbsCurve::kOpen, false, false, true, result, &st);
    check(st, "Create cubic guide curve");
    return result;
}

void BraidGenerator::clearGeometry(MDataBlock& block, const MObject& attr) {
    const unsigned n = attr == outMesh ? 3u : 27u;
    MStatus st;
    MArrayDataHandle output = block.outputArrayValue(attr, &st); check(st, "Read output array");
    MArrayDataBuilder builder(&block, attr, n, &st); check(st, "Create output array builder");
    for (unsigned i = 0; i < n; ++i) {
        MObject empty;
        if (attr == outMesh) { MFnMeshData data; empty = data.create(); }
        else { MFnNurbsCurveData data; empty = data.create(); }
        builder.addElement(i).setMObject(empty);
    }
    check(output.set(builder), "Set empty output array"); output.setAllClean();
}

MStatus BraidGenerator::compute(const MPlug& plug, MDataBlock& block) {
    const MObject attr = plug.attribute();
    const bool meshRequested = attr == outMesh, guideRequested = attr == outGuideCurves;
    if (!meshRequested && !guideRequested && attr != outLength && attr != outCycles && attr != outSegments && attr != outStatus)
        return MS::kUnknownParameter;
    Settings s;
    std::string status = "OK";
    bool guideOnlyError = false;
    try {
        MStatus st;
        // Deleting a source transform can leave its connected shape in Maya's
        // history graph. The registered transform identity is authoritative.
        if (!MPlug(thisMObject(), sourceCurve).isDestination())
            throw std::runtime_error("Source transform identity is missing; select the source and use Rebind");
        if (!MPlug(thisMObject(), inputCurve).isDestination())
            throw std::runtime_error("Input curve is not connected; select the source and use Rebind");
        // worldSpace carries the DAG transform on the geometry data object. Applying it
        // explicitly is required for world arc length, nonuniform scales and root positions.
        MObject input = block.inputValue(inputCurve, &st).asNurbsCurveTransformed(); check(st, "Read source curve");
        if (input.isNull()) throw std::runtime_error("Input curve is not connected; select the source and use Rebind");
        MFnNurbsCurve curve(input, &st); check(st, "Read source curve data");
        s = readSettings(block, curve);
        if (meshRequested || guideRequested) {
            int count = s.segments+1;
            if (guideRequested) {
                const double desired = std::max(64.0, std::ceil(s.cycles*64.0)+1.0);
                if (!s.guideSamples && desired > double(kMaxSegments+1)) {
                    guideOnlyError = true;
                    throw std::runtime_error("Automatic guide resolution exceeds 8193 samples; increase pitch, lower cycles, or set guideSamples explicitly");
                }
                count = s.guideSamples ? s.guideSamples : static_cast<int>(desired);
            }
            const auto strands = sample(curve, s, count);
            const unsigned n = meshRequested ? 3u : 27u;
            MArrayDataHandle output = block.outputArrayValue(attr, &st); check(st, "Read output array");
            MArrayDataBuilder builder(&block, attr, n, &st); check(st, "Create output array builder");
            for (unsigned i = 0; i < n; ++i) {
                const MObject result = meshRequested ? makeMesh(strands[i], s) : makeGuide(strands[i/9], s, i%9);
                builder.addElement(i).setMObject(result);
            }
            check(output.set(builder), "Set braid outputs"); output.setAllClean();
        }
    } catch (const std::exception& error) {
        status = error.what();
        if (!guideOnlyError) { s = Settings(); s.segments = 0; }
        try {
            // Invalid source data clears all stale geometry. A guide-only sampling limit
            // leaves a valid manual-resolution mesh intact until extraction is adjusted.
            if (!guideOnlyError) clearGeometry(block, outMesh);
            clearGeometry(block, outGuideCurves);
        } catch (...) { return MS::kFailure; }
    }
    auto setDouble = [&](const MObject& output, double value) { auto handle = block.outputValue(output); handle.setDouble(value); handle.setClean(); };
    setDouble(outLength, s.length); setDouble(outCycles, s.cycles);
    auto segmentsHandle = block.outputValue(outSegments); segmentsHandle.setInt(s.segments); segmentsHandle.setClean();
    auto statusHandle = block.outputValue(outStatus); statusHandle.setString(status.c_str()); statusHandle.setClean();
    block.setClean(plug);
    return MS::kSuccess;
}

MStatus initializePlugin(MObject object) {
    MFnPlugin plugin(object, "BraidTools", "1.0.0", "Any");
    MStatus st = plugin.registerNode("braidToolsGenerator", BraidGenerator::id, BraidGenerator::creator, BraidGenerator::initialize);
    if (!st) return st;
    MGlobal::executePythonCommand(
        "try:\n"
        "    import braidtools.lifecycle as _bt_lifecycle\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    _bt_lifecycle.install()\n", false, false);
    return st;
}

MStatus uninitializePlugin(MObject object) {
    MGlobal::executePythonCommand(
        "try:\n"
        "    import braidtools.lifecycle as _bt_lifecycle\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    _bt_lifecycle.uninstall()\n", false, false);
    MFnPlugin plugin(object);
    return plugin.deregisterNode(BraidGenerator::id);
}
