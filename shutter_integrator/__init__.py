"""Offline shutter integrator for frame-exported 2D animation.

Integrates linear, premultiplied photon subframes over the shutter
interval with a shutter weight function, producing motion-blurred
output frames with normalized color and alpha.
"""

from .weights import ShutterWeight, UNIFORM, TRIANGLE, get_weight
from .integrator import integrate_frames, cell_boundaries, IntegrationResult
from .manifest import OutputFrame, SubFrame, Manifest, load_manifest

__all__ = [
    "ShutterWeight",
    "UNIFORM",
    "TRIANGLE",
    "get_weight",
    "integrate_frames",
    "cell_boundaries",
    "IntegrationResult",
    "OutputFrame",
    "SubFrame",
    "Manifest",
    "load_manifest",
]

__version__ = "0.1.0"
