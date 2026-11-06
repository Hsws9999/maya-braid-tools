"""Chinese dockable UI. Sliders and ramp drags update the live DG continuously."""
import math

from maya import cmds
from maya.api import OpenMaya as om
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from . import api
from ._qt import QtCore, QtGui, QtWidgets, dialog_exec, mouse_position

_panel = None
_OBJECT_NAME = "BraidToolsPanel"
_WORKSPACE_NAME = _OBJECT_NAME + "WorkspaceControl"


def _remove_callbacks(callbacks):
    """Safe even when Maya deletes the workspaceControl without closeEvent."""
    for callback in list(callbacks):
        try:
            om.MMessage.removeCallback(callback)
        except RuntimeError:
            pass
    callbacks[:] = []


class NumericRow(QtWidgets.QWidget):
    changed = QtCore.Signal(float)
    editStarted = QtCore.Signal()
    editFinished = QtCore.Signal()

    def __init__(self, label, minimum, maximum, slider_min=None, slider_max=None,
                 integer=False, tooltip="", parent=None):
        super().__init__(parent)
        self.integer = integer
        self.slider_min = minimum if slider_min is None else slider_min
        self.slider_max = maximum if slider_max is None else slider_max
        self._updating = False
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.label = QtWidgets.QLabel(label)
        self.label.setMinimumWidth(104)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(0 if integer else 4)
        self.spin.setSingleStep(1 if integer else 0.05)
        self.spin.setMinimumWidth(100)
        self.spin.setKeyboardTracking(False)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setMinimumWidth(90)
        layout.addWidget(self.label)
        layout.addWidget(self.spin)
        layout.addWidget(self.slider, 1)
        self.setToolTip(tooltip)
        self.spin.valueChanged.connect(self._from_spin)
        self.spin.editingFinished.connect(self.editFinished.emit)
        self.spin.installEventFilter(self)
        self.slider.valueChanged.connect(self._from_slider)
        self.slider.sliderPressed.connect(self.editStarted.emit)
        self.slider.sliderReleased.connect(self.editFinished.emit)

    def eventFilter(self, watched, event):
        if watched is self.spin and event.type() == QtCore.QEvent.Type.FocusIn:
            self.editStarted.emit()
        return super().eventFilter(watched, event)

    def set_value(self, value):
        self._updating = True
        try:
            self.spin.setValue(value)
            fraction = (value - self.slider_min) / max(1e-12, self.slider_max - self.slider_min)
            self.slider.setValue(int(round(min(1.0, max(0.0, fraction)) * 1000)))
        finally:
            self._updating = False

    def _from_spin(self, value):
        if self._updating:
            return
        self.set_value(value)
        self.changed.emit(value)

    def _from_slider(self, value):
        if self._updating:
            return
        number = self.slider_min + (self.slider_max - self.slider_min) * value / 1000.0
        if self.integer:
            number = round(number)
        self.set_value(number)
        self.changed.emit(number)


class RampCanvas(QtWidgets.QWidget):
    """Small editable ramp graph. Value/position fields supply exact input."""
    changed = QtCore.Signal(object)
    pointSelected = QtCore.Signal(int)
    editStarted = QtCore.Signal()
    editFinished = QtCore.Signal()

    def __init__(self, rotation=False, parent=None):
        super().__init__(parent)
        self.rotation = rotation
        self.entries = [(0.0, 0.0 if rotation else 1.0, 1),
                        (1.0, 0.0 if rotation else 1.0, 1)]
        self.selected = 0
        self._dragging = False
        self._range = None
        self.sampler = None
        self.setMinimumHeight(138)
        self.setMouseTracking(True)
        self.setToolTip("拖动控制点连续更新；双击图表添加点。下方可输入精确位置、数值和插值。")

    def set_entries(self, entries, keep_selection=True):
        self.entries = [tuple(entry) for entry in entries]
        if not keep_selection:
            self.selected = 0
        self.selected = max(0, min(self.selected, len(self.entries) - 1))
        self.update()

    def _rect(self):
        return QtCore.QRectF(42.0, 12.0, max(20.0, self.width() - 57.0), max(20.0, self.height() - 38.0))

    def _limits(self):
        if self._range is not None:
            return self._range
        values = [entry[1] for entry in self.entries] or [0.0]
        if self.rotation:
            lower, upper = min(-45.0, min(values)), max(45.0, max(values))
        else:
            lower, upper = 0.0, max(1.25, max(values) * 1.2)
        return lower, upper

    def _point(self, entry):
        rect = self._rect()
        lower, upper = self._limits()
        return QtCore.QPointF(rect.left() + entry[0] * rect.width(),
                             rect.bottom() - (entry[1] - lower) / (upper - lower) * rect.height())

    def _value(self, point):
        rect = self._rect()
        lower, upper = self._limits()
        position = min(1.0, max(0.0, (point.x() - rect.left()) / rect.width()))
        value = lower + (rect.bottom() - point.y()) / rect.height() * (upper - lower)
        return position, min(upper, max(lower, value))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self._rect()
        painter.fillRect(self.rect(), QtGui.QColor(39, 42, 46))
        painter.fillRect(rect, QtGui.QColor(27, 30, 34))
        painter.setPen(QtGui.QColor(70, 74, 80))
        for step in range(5):
            x = rect.left() + step * rect.width() / 4.0
            y = rect.top() + step * rect.height() / 4.0
            painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
            painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
        lower, upper = self._limits()
        painter.setPen(QtGui.QColor(175, 180, 187))
        painter.drawText(QtCore.QRectF(0, rect.top() - 7, 37, 18), QtCore.Qt.AlignmentFlag.AlignRight, "%.3g" % upper)
        painter.drawText(QtCore.QRectF(0, rect.bottom() - 9, 37, 18), QtCore.Qt.AlignmentFlag.AlignRight, "%.3g" % lower)
        painter.drawText(QtCore.QPointF(rect.left(), self.height() - 6), "根 0")
        painter.drawText(QtCore.QPointF(rect.right() - 24, self.height() - 6), "尖 1")
        if self.entries:
            samples = None
            if self.sampler is not None:
                try:
                    samples = self.sampler()
                except (RuntimeError, ValueError):
                    pass
            path = QtGui.QPainterPath(self._point((0.0, self.entries[0][1], 1)))
            if samples:
                path.moveTo(self._point((samples[0][0], samples[0][1], 1)))
                for position, value in samples[1:]:
                    path.lineTo(self._point((position, value, 1)))
            else:
                for entry in self.entries:
                    path.lineTo(self._point(entry))
            path.lineTo(self._point((1.0, self.entries[-1][1], 1)))
            painter.setPen(QtGui.QPen(QtGui.QColor(89, 180, 219), 2))
            painter.drawPath(path)
        for index, entry in enumerate(self.entries):
            point = self._point(entry)
            painter.setPen(QtGui.QPen(QtGui.QColor(22, 28, 34), 1))
            painter.setBrush(QtGui.QColor(245, 193, 94) if index == self.selected else QtGui.QColor(108, 190, 225))
            painter.drawEllipse(point, 6, 6)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self.entries:
            return
        point = mouse_position(event)
        index = min(range(len(self.entries)), key=lambda i: (self._point(self.entries[i]) - point).manhattanLength())
        if (self._point(self.entries[index]) - point).manhattanLength() > 20:
            return
        self.selected = index
        self._range = self._limits()
        self._dragging = True
        self.editStarted.emit()
        self.pointSelected.emit(index)
        self.update()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        position, value = self._value(mouse_position(event))
        entry = (position, value, self.entries[self.selected][2])
        self.entries[self.selected] = entry
        self.entries.sort(key=lambda item: item[0])
        self.selected = self.entries.index(entry)
        self.changed.emit(list(self.entries))
        self.pointSelected.emit(self.selected)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._range = None
            self.editFinished.emit()
            self.update()

    def mouseDoubleClickEvent(self, event):
        point = mouse_position(event)
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self._rect().contains(point):
            return
        self.editStarted.emit()
        position, value = self._value(point)
        entry = (position, value, 1)
        self.entries.append(entry)
        self.entries.sort(key=lambda item: item[0])
        self.selected = self.entries.index(entry)
        self.changed.emit(list(self.entries))
        self.pointSelected.emit(self.selected)
        self.editFinished.emit()
        self.update()


class RampEditor(QtWidgets.QWidget):
    changed = QtCore.Signal(object)
    editStarted = QtCore.Signal()
    editFinished = QtCore.Signal()

    def __init__(self, rotation=False, parent=None):
        super().__init__(parent)
        self._updating = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.canvas = RampCanvas(rotation)
        layout.addWidget(self.canvas)
        row = QtWidgets.QHBoxLayout()
        self.position = QtWidgets.QDoubleSpinBox()
        self.position.setDecimals(4)
        self.position.setRange(0, 1)
        self.position.setSingleStep(0.01)
        self.position.setKeyboardTracking(False)
        self.value = QtWidgets.QDoubleSpinBox()
        self.value.setDecimals(4)
        self.value.setRange(-100000 if rotation else 0, 100000)
        self.value.setSingleStep(5 if rotation else 0.05)
        self.value.setKeyboardTracking(False)
        self.interpolation = QtWidgets.QComboBox()
        self.interpolation.addItems(["无", "线性", "平滑", "样条"])
        for label, widget in [("位置", self.position), ("角度°" if rotation else "倍数", self.value)]:
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(widget)
        row.addWidget(self.interpolation)
        layout.addLayout(row)
        buttons = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("添加点")
        remove = QtWidgets.QPushButton("删除所选点")
        buttons.addWidget(add)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.canvas.changed.connect(self._canvas_changed)
        self.canvas.pointSelected.connect(self._point_selected)
        self.canvas.editStarted.connect(self.editStarted.emit)
        self.canvas.editFinished.connect(self.editFinished.emit)
        self.position.valueChanged.connect(self._field_changed)
        self.value.valueChanged.connect(self._field_changed)
        self.interpolation.currentIndexChanged.connect(self._field_changed)
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        self._point_selected(0)

    def set_entries(self, entries):
        self.canvas.set_entries(entries)
        self._point_selected(self.canvas.selected)

    def _point_selected(self, index):
        if not self.canvas.entries:
            return
        self._updating = True
        try:
            position, value, interpolation = self.canvas.entries[index]
            self.position.setValue(position)
            self.value.setValue(value)
            self.interpolation.setCurrentIndex(int(interpolation))
        finally:
            self._updating = False

    def _canvas_changed(self, entries):
        self._point_selected(self.canvas.selected)
        self.changed.emit(entries)

    def _field_changed(self, unused=None):
        if self._updating or not self.canvas.entries:
            return
        entry = (self.position.value(), self.value.value(), self.interpolation.currentIndex())
        self.canvas.entries[self.canvas.selected] = entry
        self.canvas.entries.sort(key=lambda item: item[0])
        self.canvas.selected = self.canvas.entries.index(entry)
        self.canvas.update()
        self.changed.emit(list(self.canvas.entries))

    def _add(self):
        entries = self.canvas.entries
        gaps = [(entries[i + 1][0] - entries[i][0], i) for i in range(len(entries) - 1)]
        if gaps:
            unused, index = max(gaps)
            entry = ((entries[index][0] + entries[index + 1][0]) / 2,
                     (entries[index][1] + entries[index + 1][1]) / 2, 1)
        else:
            entry = (1.0 if entries[0][0] < 0.5 else 0.0, entries[0][1], 1)
        entries.append(entry)
        entries.sort(key=lambda item: item[0])
        self.canvas.selected = entries.index(entry)
        self._canvas_changed(list(entries))
        self.canvas.update()

    def _remove(self):
        if len(self.canvas.entries) <= 1:
            return
        del self.canvas.entries[self.canvas.selected]
        self.canvas.selected = min(self.canvas.selected, len(self.canvas.entries) - 1)
        self._canvas_changed(list(self.canvas.entries))
        self.canvas.update()


class BraidToolsPanel(MayaQWidgetDockableMixin, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(_OBJECT_NAME)
        self.setWindowTitle("BraidTools · 麻花辫")
        self.setMinimumWidth(420)
        self.resize(465, 880)
        self._syncing = False
        self._edit_open = False
        self._edit_context = None
        self._callbacks = []
        self._disposed = False
        self._rebind_active = False
        self._delete_active = False
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        callbacks = self._callbacks
        self.destroyed.connect(lambda *unused: _remove_callbacks(callbacks))
        self._nodes = []
        self.rows = {}
        self._build()
        self._callbacks.extend([
            om.MEventMessage.addEventCallback("SelectionChanged", self._selection_changed),
            om.MEventMessage.addEventCallback("Undo", self._defer_refresh),
            om.MEventMessage.addEventCallback("Redo", self._defer_refresh),
            om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen, self._defer_refresh),
            om.MSceneMessage.addCallback(om.MSceneMessage.kAfterNew, self._defer_refresh),
        ])
        self.refresh()

    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        hint = QtWidgets.QLabel("选择开放 NURBS 曲线创建。编辑列表支持多选；视口选择已绑定对象会同步。")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        actions = QtWidgets.QHBoxLayout()
        create = QtWidgets.QPushButton("从所选曲线创建")
        refresh = QtWidgets.QPushButton("刷新列表")
        actions.addWidget(create, 2)
        actions.addWidget(refresh)
        outer.addLayout(actions)
        create.clicked.connect(lambda: self._run(api.create_from_selection))
        refresh.clicked.connect(self.refresh)
        self.assets = QtWidgets.QListWidget()
        self.assets.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.assets.setMaximumHeight(116)
        self.assets.setToolTip("这里选中的资产是下方编辑目标。Ctrl/Shift 多选。")
        self.assets.itemSelectionChanged.connect(self._asset_selection)
        outer.addWidget(self.assets)
        self.delete_button = QtWidgets.QPushButton("删除所选辫子…")
        self.delete_button.setObjectName("BraidToolsDeleteSelected")
        self.delete_button.setToolTip("删除列表中选中的实时辫子资产；保留源曲线和独立快照。与 Ramp 的删除点无关。")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_selected)
        outer.addWidget(self.delete_button)
        self.target_label = QtWidgets.QLabel()
        outer.addWidget(self.target_label)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        self.controls = QtWidgets.QWidget()
        scroll.setWidget(self.controls)
        self.form = QtWidgets.QVBoxLayout(self.controls)
        self.form.setContentsMargins(0, 0, 5, 0)

        geometry = self._section("形状与密度")
        self.density_mode = self._combo(geometry, "密度模式", ["固定节距（长度变化时自动增减循环）", "固定循环数（长度变化时拉伸）"])
        self.density_mode.currentIndexChanged.connect(lambda value: self._set("densityMode", value))
        self._numeric(geometry, "pitch", "节距 / cm", 0.001, 1000000, 0.1, 20,
                      tooltip="每次完整编织循环的世界空间长度。切换模式会换算，保持当前编织形状。")
        self._numeric(geometry, "cycles", "总循环数", 0.001, 500, 0.1, 30)
        self._numeric(geometry, "size", "整体尺寸 / cm", 0.0001, 1000000, 0.05, 10,
                      tooltip="名义包络宽度；尺寸 Ramp 同时缩放中心幅度与股线半径。")
        self._numeric(geometry, "flatness", "整体扁平度", 0.01, 10, 0.05, 3)
        self._numeric(geometry, "strandThickness", "股线粗细", 0.01, 4, 0.05, 3)
        self._numeric(geometry, "strandFlatness", "股线截面扁平", 0.01, 10, 0.05, 3)
        self._numeric(geometry, "orientation", "整体朝向 / °", -360000, 360000, -180, 180)

        strands = self._section("分股曲线 · 单独调整三股")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("每股控制点"))
        self.strand_samples = QtWidgets.QSpinBox()
        self.strand_samples.setObjectName("BraidToolsStrandSamples")
        self.strand_samples.setRange(4, 256)
        self.strand_samples.setValue(32)
        self.strand_samples.setToolTip("仅用于首次创建，之后点数固定；重复创建会保留已有修改。")
        row.addWidget(self.strand_samples)
        self.enable_strands_button = QtWidgets.QPushButton("创建三条分股控制曲线")
        self.enable_strands_button.setObjectName("BraidToolsEnableStrandControls")
        self.enable_strands_button.clicked.connect(
            lambda: self._run(lambda: api.enable_strand_controls(self._nodes, self.strand_samples.value())))
        row.addWidget(self.enable_strands_button, 1)
        strands.addLayout(row)
        self.strand_buttons = []
        for index in range(3):
            row = QtWidgets.QHBoxLayout()
            select = QtWidgets.QPushButton("选择第 %d 股控制点" % (index + 1))
            select.setObjectName("BraidToolsSelectStrand%d" % (index + 1))
            select.clicked.connect(lambda unused=False, strand=index: self._select_strand(strand))
            reset = QtWidgets.QPushButton("重置第 %d 股" % (index + 1))
            reset.setObjectName("BraidToolsResetStrand%d" % (index + 1))
            reset.clicked.connect(lambda unused=False, strand=index:
                                  self._run(lambda: api.reset_strand_controls(self._nodes, strand)))
            row.addWidget(select, 2)
            row.addWidget(reset)
            strands.addLayout(row)
            self.strand_buttons.extend([select, reset])
        self.reset_all_strands_button = QtWidgets.QPushButton("重置全部三股")
        self.reset_all_strands_button.setObjectName("BraidToolsResetAllStrands")
        self.reset_all_strands_button.clicked.connect(
            lambda: self._run(lambda: api.reset_strand_controls(self._nodes)))
        strands.addWidget(self.reset_all_strands_button)
        self.strand_buttons.append(self.reset_all_strands_button)
        self.strand_hint = QtWidgets.QLabel(
            "创建后选择控制点，再框选局部点并移动。主曲线和参数仍可编辑。\n"
            "控制曲线是平滑调整笼，不必贴合每个编织弯；新提取的导向线会匹配调整后的模型。")
        self.strand_hint.setWordWrap(True)
        strands.addWidget(self.strand_hint)

        size = self._section("尺寸 Ramp · 多选时同步此 Ramp")
        self.size_ramp = RampEditor()
        size.addWidget(self.size_ramp)
        self.size_ramp.changed.connect(lambda entries: self._set_ramp("sizeRamp", entries))
        self.size_ramp.editStarted.connect(self._begin_edit)
        self.size_ramp.editFinished.connect(self._end_edit)
        self.tip = NumericRow("末端尺寸", 0, 100000, 0, 2,
                              tooltip="只修改每条所选辫子尺寸 Ramp 最后一点的值，不增加第二层渐细。")
        size.addWidget(self.tip)
        self.tip.changed.connect(self._set_tip)
        self.tip.editStarted.connect(self._begin_edit)
        self.tip.editFinished.connect(self._end_edit)

        rotation = self._section("旋转 Ramp · 整体编织截面角度")
        self.rotation_ramp = RampEditor(rotation=True)
        rotation.addWidget(self.rotation_ramp)
        self.rotation_ramp.changed.connect(lambda entries: self._set_ramp("rotationRamp", entries))
        self.rotation_ramp.editStarted.connect(self._begin_edit)
        self.rotation_ramp.editFinished.connect(self._end_edit)

        advanced = self._section("高级设置")
        self._numeric(advanced, "phase", "编织相位 / °", -360000, 360000, -180, 180,
                      tooltip="改变编织循环起点，与整体朝向及旋转 Ramp 的截面角度分开。")

        quality = self._section("网格质量")
        self.longitudinal_mode = self._combo(quality, "轴向采样", ["自动（随循环与曲率）", "手动分段"])
        self.longitudinal_mode.currentIndexChanged.connect(lambda value: self._set("longitudinalMode", value))
        self._numeric(quality, "autoQuality", "自动质量", 0.1, 8, 0.25, 3)
        self._numeric(quality, "lengthSegments", "轴向分段", 8, 8192, 8, 1024, integer=True)
        self._numeric(quality, "radialSides", "径向边数", 3, 64, 3, 32, integer=True)
        quality_hint = QtWidgets.QLabel("导向线独立采样；修改网格质量不会改变已提取的导向线。")
        quality_hint.setWordWrap(True)
        quality.addWidget(quality_hint)

        source = self._section("源曲线与重新绑定")
        row = QtWidgets.QHBoxLayout()
        self.spans = QtWidgets.QSpinBox()
        self.spans.setRange(1, 8192)
        self.spans.setValue(16)
        self.history = QtWidgets.QCheckBox("保留重建历史")
        self.history.setChecked(True)
        row.addWidget(QtWidgets.QLabel("重建跨数"))
        row.addWidget(self.spans)
        row.addWidget(self.history)
        source.addLayout(row)
        rebuild = QtWidgets.QPushButton("重建所选资产的源曲线")
        rebuild.clicked.connect(lambda: self._run(lambda: api.rebuild_sources(self._nodes, self.spans.value(), self.history.isChecked())))
        source.addWidget(rebuild)
        row = QtWidgets.QHBoxLayout()
        select_source = QtWidgets.QPushButton("选择源曲线")
        rebind = QtWidgets.QPushButton("重新绑定路径…")
        rebind.setToolTip("固定列表中选中的单个资产，再从弹窗选择新路径。允许多条资产共享路径。")
        select_source.clicked.connect(self._select_sources)
        rebind.clicked.connect(self._rebind)
        row.addWidget(select_source)
        row.addWidget(rebind)
        source.addLayout(row)

        extract = self._section("独立快照")
        row = QtWidgets.QHBoxLayout()
        self.guide_samples = QtWidgets.QSpinBox()
        self.guide_samples.setRange(0, 8193)
        self.guide_samples.setSpecialValueText("自动")
        self.guide_samples.setValue(0)
        self.guide_samples.setToolTip("0 为自动；手动值至少 16。每股9条，合计27条导向线。")
        self.root_surfaces = QtWidgets.QCheckBox("创建 3 个 UV 根部面")
        self.root_surfaces.setChecked(True)
        row.addWidget(QtWidgets.QLabel("导向采样"))
        row.addWidget(self.guide_samples)
        row.addWidget(self.root_surfaces)
        extract.addLayout(row)
        guides = QtWidgets.QPushButton("提取 XGen 导向线（每条辫子 27 条）")
        guides.clicked.connect(lambda: self._run(lambda: api.extract_guides(self._nodes, self.root_surfaces.isChecked(), self.guide_samples.value())))
        extract.addWidget(guides)
        bake = QtWidgets.QPushButton("烘焙独立网格")
        bake.clicked.connect(lambda: self._run(lambda: api.bake(self._nodes)))
        extract.addWidget(bake)
        extract_hint = QtWidgets.QLabel("每次提取或烘焙都会创建新组，与实时资产断开。导向线可手动用于传统 XGen 的曲线转导向。")
        extract_hint.setWordWrap(True)
        extract.addWidget(extract_hint)
        self.form.addStretch(1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)

    def _section(self, title):
        group = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(9, 11, 9, 10)
        layout.setSpacing(4)
        self.form.addWidget(group)
        return layout

    def _combo(self, layout, label, choices):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(label))
        combo = QtWidgets.QComboBox()
        combo.addItems(choices)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _numeric(self, layout, name, label, minimum, maximum, slider_min, slider_max,
                 integer=False, tooltip=""):
        row = NumericRow(label, minimum, maximum, slider_min, slider_max, integer, tooltip)
        self.rows[name] = row
        layout.addWidget(row)
        row.changed.connect(lambda value, attr=name, is_int=integer: self._set(attr, int(value) if is_int else value))
        row.editStarted.connect(self._begin_edit)
        row.editFinished.connect(self._end_edit)

    def _begin_edit(self):
        if not self._edit_open and not self._syncing and self._nodes:
            self._edit_context = api.undo_chunk("BraidTools 连续调整")
            self._edit_context.__enter__()
            self._edit_open = True

    def _end_edit(self):
        if self._edit_open:
            self._edit_context.__exit__(None, None, None)
            self._edit_context = None
            self._edit_open = False

    def _set(self, name, value):
        if self._syncing or not self._nodes:
            return
        try:
            api.set_parameter(self._nodes, name, value)
            self._update_mode_controls()
            self._live_refresh()
            if name == "densityMode":
                self._load_values()
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _set_ramp(self, name, entries):
        if self._syncing or not self._nodes:
            return
        try:
            api.set_ramp(self._nodes, name, entries)
            if name == "sizeRamp":
                self.tip.set_value(entries[-1][1])
            self._live_refresh()
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _set_tip(self, value):
        if self._syncing or not self._nodes:
            return
        try:
            with api.undo_chunk("BraidTools 调整末端"):
                for node in self._nodes:
                    entries = api.get_ramp(node, "sizeRamp")
                    if entries:
                        position, unused, interpolation = entries[-1]
                        entries[-1] = (position, value, interpolation)
                        api.set_ramp([node], "sizeRamp", entries)
            self.size_ramp.set_entries(api.get_ramp(self._nodes[0], "sizeRamp"))
            self._live_refresh()
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _live_refresh(self):
        # No timer/debounce: each drag event updates native geometry now.
        if not cmds.about(batch=True):
            cmds.refresh(currentView=True, force=True)
        self._status()

    def _run(self, operation):
        self._end_edit()
        try:
            operation()
            self.refresh()
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _error(self, exc):
        self._end_edit()
        self.status.setText("操作未完成：" + str(exc))
        om.MGlobal.displayWarning("BraidTools: " + str(exc))

    def _select_sources(self):
        sources = [api.source_transform(node) for node in self._nodes if cmds.objExists(node)]
        sources = [source for source in sources if source]
        if sources:
            cmds.select(sources, replace=True)

    def _select_strand(self, strand):
        self._end_edit()
        try:
            controls = [api.strand_controls(node) for node in self._nodes]
            if not controls or any(len(items) != 3 for items in controls):
                raise ValueError("请先为所选资产创建分股控制曲线。")
            cmds.select([items[strand] + ".cv[*]" for items in controls], replace=True)
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _delete_selected(self):
        """Confirm and delete only the asset identities selected in this list."""
        if self._disposed or self._delete_active or self._rebind_active:
            return
        self._end_edit()
        self._refresh_timer.stop()
        self._delete_active = True
        self.delete_button.setEnabled(False)
        dialog = None
        message = None
        error = None
        try:
            items = self.assets.selectedItems()
            if not items:
                raise ValueError("请先在上方列表中选择要删除的辫子。")
            targets = []
            for item in items:
                handle = item.data(int(QtCore.Qt.ItemDataRole.UserRole) + 1)
                if (not isinstance(handle, om.MObjectHandle)
                        or not handle.isValid() or not handle.isAlive()
                        or om.MFnDependencyNode(handle.object()).typeName != api.NODE_TYPE):
                    raise ValueError("列表中的资产已不存在，请刷新后重新选择。")
                node = om.MFnDependencyNode(handle.object()).name()
                root = api._delete_root(node)
                root_handle = om.MObjectHandle(api._dag_path(root).node()) if root else None
                targets.append((handle, root_handle, root or node))

            dialog = QtWidgets.QDialog(self)
            dialog.setObjectName("BraidToolsDeleteDialog")
            dialog.setWindowTitle("删除所选辫子")
            dialog.resize(540, 300)
            layout = QtWidgets.QVBoxLayout(dialog)
            label = QtWidgets.QLabel("将删除以下 %d 条实时辫子资产：" % len(targets))
            layout.addWidget(label)
            names = QtWidgets.QListWidget()
            names.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
            for unused_handle, unused_root_handle, name in targets:
                names.addItem(name)
            layout.addWidget(names, 1)
            explanation = QtWidgets.QLabel(
                "保留源曲线，以及已经提取的导向线、根部面和烘焙网格。\n"
                "只删除上述列表目标；共享同一源曲线的其他辫子保持不变。\n"
                "删除后可按 Ctrl+Z 撤销。")
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            buttons = QtWidgets.QDialogButtonBox()
            confirm = buttons.addButton("删除 %d 条辫子" % len(targets),
                                        QtWidgets.QDialogButtonBox.ButtonRole.DestructiveRole)
            confirm.setObjectName("BraidToolsDeleteConfirm")
            confirm.setDefault(False)
            confirm.setAutoDefault(False)
            cancel = buttons.addButton("取消", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
            cancel.setObjectName("BraidToolsDeleteCancel")
            cancel.setAutoDefault(True)
            cancel.setDefault(True)
            cancel.setFocus()
            confirm.clicked.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog_exec(dialog) != QtWidgets.QDialog.DialogCode.Accepted:
                return

            nodes = []
            for handle, root_handle, unused_name in targets:
                if not (handle.isValid() and handle.isAlive()):
                    raise ValueError("待删除资产已发生变化，请刷新列表后重新确认。")
                fn = om.MFnDependencyNode(handle.object())
                if fn.typeName != api.NODE_TYPE:
                    raise ValueError("待删除资产类型已发生变化，未执行删除。")
                node = fn.name()
                root = api._delete_root(node)
                if root_handle is None:
                    if root:
                        raise ValueError("待删除资产组已发生变化，请重新确认。")
                elif (not root_handle.isValid() or not root_handle.isAlive() or not root
                      or api._dag_path(root).node() != root_handle.object()):
                    raise ValueError("待删除资产组已发生变化，请重新确认。")
                nodes.append(node)
            deleted = api.delete_assets(nodes)
            if not deleted:
                raise ValueError("没有可删除的辫子资产，请刷新列表后重新选择。")
            self._nodes = []
            self.assets.clearSelection()
            message = "已删除 %d 条辫子；源曲线和独立快照已保留。Ctrl+Z 可撤销。" % len(deleted)
        except (RuntimeError, ValueError) as exc:
            error = exc
        finally:
            self._delete_active = False
            if dialog is not None:
                dialog.deleteLater()
            # Deletion and rollback can emit selection/undo events. Reconcile
            # once without deriving targets from a possibly shared source curve.
            self._refresh_timer.stop()
            try:
                self.refresh(preserve_targets=True)
            except (RuntimeError, ValueError) as exc:
                if error is None:
                    error = exc
            if not self._disposed:
                if error is not None:
                    self._error(error)
                elif message is not None:
                    self.status.setText(message)

    def _rebind(self):
        self._end_edit()
        try:
            if len(self._nodes) != 1:
                raise ValueError("请先在列表中只选择一条要重新绑定的资产。")
            target = self._nodes[0]
            target_handle = om.MObjectHandle(api._plug(target, "message").node())
            curves = []
            for shape in cmds.ls(type="nurbsCurve", long=True) or []:
                try:
                    if not cmds.getAttr(shape + ".intermediateObject"):
                        curves.append(api._curve_transform(shape))
                except (RuntimeError, ValueError):
                    pass
            curves = sorted(api._unique(curves), key=str.casefold)
            if not curves:
                raise ValueError("场景中没有可绑定的开放 NURBS 曲线。")
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("重新绑定路径")
            dialog.resize(540, 380)
            layout = QtWidgets.QVBoxLayout(dialog)
            target_root = api.asset_root(target) or target
            label = QtWidgets.QLabel("固定目标：%s\n选择新的开放曲线；其他资产的绑定保持原状。" % target_root)
            label.setWordWrap(True)
            layout.addWidget(label)
            paths = QtWidgets.QListWidget()
            paths.setObjectName("BraidToolsRebindPaths")
            layout.addWidget(paths, 1)
            current_source = api.source_transform(target)
            for curve in curves:
                item = QtWidgets.QListWidgetItem(curve + ("  （当前路径）" if curve == current_source else ""))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, om.MObjectHandle(api._dag_path(curve).node()))
                paths.addItem(item)
                if curve == current_source:
                    paths.setCurrentItem(item)
            if paths.currentRow() < 0:
                paths.setCurrentRow(0)
            buttons = QtWidgets.QDialogButtonBox()
            accept = buttons.addButton("绑定", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton("取消", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            paths.itemDoubleClicked.connect(lambda *unused: accept.click())
            layout.addWidget(buttons)
            self._rebind_active = True
            try:
                if dialog_exec(dialog) != QtWidgets.QDialog.DialogCode.Accepted:
                    return
                source_handle = paths.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)
                if not (target_handle.isValid() and target_handle.isAlive()
                        and source_handle.isValid() and source_handle.isAlive()):
                    raise ValueError("资产或曲线已被删除，请重新选择。")
                target = om.MFnDependencyNode(target_handle.object()).name()
                curve = om.MDagPath.getAPathTo(source_handle.object()).fullPathName()
                api.rebind(target, curve)
                self._nodes = [target]
            finally:
                self._rebind_active = False
                dialog.deleteLater()
                self.refresh(preserve_targets=True)
        except (RuntimeError, ValueError) as exc:
            self._error(exc)

    def _asset_selection(self):
        if self._syncing or self._disposed or self._rebind_active or self._delete_active:
            return
        self._end_edit()
        self._nodes = [item.data(QtCore.Qt.ItemDataRole.UserRole) for item in self.assets.selectedItems()]
        self._load_values()

    def _selection_changed(self, *unused):
        if self._syncing or self._disposed or self._rebind_active or self._delete_active:
            return
        try:
            targets = api.generators_from_selection()
        except (RuntimeError, ValueError) as exc:
            self._end_edit()
            self._nodes = []
            self.refresh(preserve_targets=True)
            self._error(exc)
            return
        if targets:
            self._end_edit()
            self._nodes = targets
            self.refresh()

    def _defer_refresh(self, *unused):
        if not self._disposed and not self._rebind_active and not self._delete_active:
            self._refresh_timer.start(0)

    def refresh(self, *unused, preserve_targets=False):
        if self._syncing or self._disposed or self._rebind_active or self._delete_active:
            return
        self._syncing = True
        selection_error = None
        try:
            nodes = api.all_generators()
            try:
                selected = [] if preserve_targets else api.generators_from_selection()
            except (RuntimeError, ValueError) as exc:
                selected = []
                self._nodes = []
                selection_error = exc
            self._nodes = selected or [node for node in self._nodes if node in nodes]
            self.assets.clear()
            for node in nodes:
                source = api.source_transform(node)
                root = api.asset_root(node)
                label = source.rsplit("|", 1)[-1] if source else "源曲线缺失"
                label += "  →  " + (root.rsplit("|", 1)[-1] if root else "资产组缺失")
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, node)
                item.setData(int(QtCore.Qt.ItemDataRole.UserRole) + 1,
                             om.MObjectHandle(api._plug(node, "message").node()))
                item.setToolTip(node)
                self.assets.addItem(item)
                item.setSelected(node in self._nodes)
        finally:
            self._syncing = False
        self._load_values()
        if selection_error is not None:
            self._error(selection_error)

    def _load_values(self):
        self.controls.setEnabled(bool(self._nodes))
        self.delete_button.setEnabled(bool(self.assets.selectedItems())
                                      and not self._delete_active and not self._rebind_active)
        self.target_label.setText("编辑目标：%d 条%s" % (len(self._nodes), " · 显示首条数值；仅修改正在编辑的字段" if len(self._nodes) > 1 else ""))
        if not self._nodes:
            self.status.setText("未选择 BraidTools 资产。选择源曲线后可点击创建。")
            return
        node = self._nodes[0]
        self._syncing = True
        try:
            for name, row in self.rows.items():
                row.set_value(cmds.getAttr(node + "." + name))
            self.density_mode.setCurrentIndex(cmds.getAttr(node + ".densityMode"))
            self.longitudinal_mode.setCurrentIndex(cmds.getAttr(node + ".longitudinalMode"))
            size = api.get_ramp(node, "sizeRamp")
            rotation = api.get_ramp(node, "rotationRamp")
            self.size_ramp.set_entries(size)
            self.rotation_ramp.set_entries(rotation)
            self.size_ramp.canvas.sampler = lambda: api.sample_ramp(node, "sizeRamp")
            self.rotation_ramp.canvas.sampler = lambda: api.sample_ramp(node, "rotationRamp")
            if size:
                self.tip.set_value(size[-1][1])
            self._update_mode_controls()
            try:
                ready = all(bool(api.strand_controls(target)) for target in self._nodes)
            except (RuntimeError, ValueError):
                ready = False  # The status line reports the damaged graph.
            self.enable_strands_button.setEnabled(not ready)
            self.strand_samples.setEnabled(not ready)
            for button in self.strand_buttons:
                button.setEnabled(ready)
        finally:
            self._syncing = False
        self._status()

    def _update_mode_controls(self):
        pitch = self.density_mode.currentIndex() == 0
        self.rows["pitch"].setEnabled(pitch)
        self.rows["cycles"].setEnabled(not pitch)
        auto = self.longitudinal_mode.currentIndex() == 0
        self.rows["autoQuality"].setEnabled(auto)
        self.rows["lengthSegments"].setEnabled(not auto)

    def _status(self):
        if not self._nodes or not cmds.objExists(self._nodes[0]):
            return
        try:
            node = api.geometry_generator(self._nodes[0])
            status = cmds.getAttr(node + ".outStatus")
            text = "长度 %.3f cm · 循环 %.3f · 轴向 %d 段" % (
                cmds.getAttr(node + ".outLength"), cmds.getAttr(node + ".outCycles"), cmds.getAttr(node + ".outSegments"))
            if status != "OK":
                text += "\n" + status
            self.status.setText(text)
        except (RuntimeError, ValueError) as exc:
            self.status.setText(str(exc))

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._end_edit()
        self._refresh_timer.stop()
        _remove_callbacks(self._callbacks)

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)

    def dockCloseEventTriggered(self):
        self.dispose()


def show():
    """Open a fresh dockable panel without persisting global Maya preferences."""
    global _panel
    api.ensure_loaded()
    if _panel is not None:
        try:
            _panel.dispose()
            _panel.close()
            _panel.deleteLater()
        except RuntimeError:
            pass
    if cmds.workspaceControl(_WORKSPACE_NAME, query=True, exists=True):
        cmds.deleteUI(_WORKSPACE_NAME)
    _panel = BraidToolsPanel()
    _panel.show(dockable=True, area="right", floating=False, retain=False)
    return _panel
