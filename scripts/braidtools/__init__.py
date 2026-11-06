"""BraidTools for Maya. Importing this package never edits a scene."""

__version__ = "1.0.0"

from .api import (  # noqa: F401
    bake, create, create_from_selection, extract_guides,
    generators_from_selection, rebind, rebuild_sources, set_density_mode,
    set_parameter, set_ramp,
)


def show():
    """Open the Chinese dockable BraidTools panel."""
    from .ui import show as _show
    return _show()
