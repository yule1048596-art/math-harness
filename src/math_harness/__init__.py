"""Workspace-isolated mathematical learning harness."""

from math_harness.service import MathHarnessService
from math_harness.version import VERSION

__version__ = VERSION

__all__ = ["MathHarnessService", "__version__"]
