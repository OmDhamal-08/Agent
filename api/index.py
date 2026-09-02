"""Vercel serverless entry point — re-exports the FastAPI app."""

# ── pkg_resources shim ────────────────────────────────────────────────
# razorpay==1.4.2 does `import pkg_resources` at the top level.
# Python 3.12 removed setuptools from the stdlib, and Vercel's uv-based
# runtime doesn't always vendor it.  This shim provides the one function
# razorpay actually uses (get_distribution) via importlib.metadata so the
# import succeeds without setuptools.
import sys

if "pkg_resources" not in sys.modules:
    try:
        import pkg_resources  # noqa: F401 — already available, nothing to do
    except ImportError:
        import importlib.metadata
        import types

        _mod = types.ModuleType("pkg_resources")

        class _DistShim:
            """Minimal shim returned by get_distribution()."""
            def __init__(self, name):
                self._dist = importlib.metadata.distribution(name)
                self.version = self._dist.version
                self.project_name = name

        _mod.get_distribution = _DistShim  # callable(name) → obj with .version
        _mod.DistributionNotFound = importlib.metadata.PackageNotFoundError
        sys.modules["pkg_resources"] = _mod
# ── end shim ──────────────────────────────────────────────────────────

from backend.main import app
