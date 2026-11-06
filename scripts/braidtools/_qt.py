"""Small Qt5/Qt6 adapter using Maya's bundled binding; no extra dependency."""
import sys

# Maya's mixin imports the host binding before ui.py imports this module.
# Prefer that binding if a user has also installed the other one separately.
_host_binding = getattr(sys.modules.get("maya.app.general.mayaMixin"), "_qtImported", None)
if _host_binding == "PySide2":
    from PySide2 import QtCore, QtGui, QtWidgets
elif _host_binding == "PySide6":
    from PySide6 import QtCore, QtGui, QtWidgets
else:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets


def mouse_position(event):
    """Return floating-point local mouse coordinates on both Qt generations."""
    method = getattr(event, "position", None)
    return method() if method is not None else event.localPos()


def dialog_exec(dialog):
    """Qt5's exec_ was renamed to exec in Qt6."""
    method = getattr(dialog, "exec", None)
    return method() if method is not None else dialog.exec_()
