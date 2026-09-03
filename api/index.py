"""Vercel serverless entry point — re-exports the FastAPI app."""

# ── pkg_resources shim ────────────────────────────────────────────────
# razorpay does `import pkg_resources` at the top level.
# Python 3.12 removed setuptools from the stdlib, and Vercel's uv-based
# runtime doesn't always vendor it.  This shim provides the functions
# razorpay actually uses (get_distribution, require) via importlib.metadata
# so the import succeeds without setuptools.
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
                # Strip version specifiers (e.g. 'razorpay>=1.0' → 'razorpay')
                clean = name.split(">=")[0].split("<=")[0].split("==")[0]
                clean = clean.split("!=")[0].split("<")[0].split(">")[0].strip()
                self._dist = importlib.metadata.distribution(clean)
                self.version = self._dist.version
                self.project_name = clean

        def _require(*requirements):
            """Minimal shim for pkg_resources.require()."""
            result = []
            for req in requirements:
                result.append(_DistShim(req))
            return result

        _mod.get_distribution = _DistShim  # callable(name) → obj with .version
        _mod.require = _require            # callable(*names) → list of dist shims
        _mod.DistributionNotFound = importlib.metadata.PackageNotFoundError
        sys.modules["pkg_resources"] = _mod
# ── end shim ──────────────────────────────────────────────────────────

from backend.main import app
