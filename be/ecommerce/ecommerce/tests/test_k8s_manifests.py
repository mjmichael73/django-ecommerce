"""Django-side guard that delegates to the standalone k8s manifest validator.

Card a9cea652 — Phase 5 deployment: Kubernetes manifests.

The real assertions live in ``scripts/validate_k8s_manifests.py`` (a
standalone, Django-free script that parses YAML with ``yaml.safe_load``). This
test module re-runs that validator through the Django test suite so it is
exercised by ``make test`` and the CI ``test`` job — exactly like the Grafana
dashboard tests exercise ``docker/grafana/scripts/validate_dashboards.py``.

Skip rule: the production web image (``Dockerfile``) only runs
``COPY be/ecommerce /app``, so neither ``k8s/`` nor ``scripts/`` exist inside
the container. When the repo root cannot be discovered (running inside the
app image) the test skips with an explanatory message. In CI's ``test`` job the
full checkout IS present, so the validator runs for real there.

This mirrors the repo-root discovery used by ``test_env_hygiene.py`` and
``test_migration_strategy.py``.
"""
import os
import unittest
from pathlib import Path

from django.test import SimpleTestCase


def _find_repo_root() -> Path | None:
    """Walk up from this file until we find the repo root (docker-compose.yml)."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docker-compose.yml").is_file():
            return candidate
    return None


REPO_ROOT = _find_repo_root()


class K8sManifestsTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                "repository root not present (running inside the app image — "
                "the Dockerfile only copies be/ecommerce, so k8s/ and scripts/ "
                "are absent). The standalone validator in scripts/ is the source "
                "of truth and is exercised by CI's docker job."
            )

    def test_validator_script_exists(self):
        self.assertTrue(
            (REPO_ROOT / "scripts" / "validate_k8s_manifests.py").is_file(),
            "scripts/validate_k8s_manifests.py must exist (card a9cea652)",
        )

    def test_k8s_directory_exists(self):
        self.assertTrue(
            (REPO_ROOT / "k8s").is_dir(),
            "k8s/ directory must exist (card a9cea652)",
        )

    def test_validator_passes(self):
        """Run the standalone validator and assert it exits 0 (no failures).

        ``yaml`` is imported lazily so the test skips cleanly (rather than
        erroring) if PyYAML is unavailable in this environment — matching the
        brief's "skipUnless ... yaml imports" contract.
        """
        try:
            import yaml  # noqa: F401
        except ImportError:
            raise unittest.SkipTest(
                "PyYAML is not installed in this environment; the validator "
                "requires it (CI installs it before running the script)."
            )
        # Defer import so a missing script is a clear failure, not a skip.
        script = REPO_ROOT / "scripts" / "validate_k8s_manifests.py"
        if not script.is_file():
            raise FileNotFoundError(f"validator not found: {script}")

        # Run the validator in-process (it has a main() returning an exit code).
        import importlib.util

        spec = importlib.util.spec_from_file_location("validate_k8s_manifests", script)
        module = importlib.util.module_from_spec(spec)
        # The validator's _repo_root() walks up from __file__ to find k8s/, so
        # importing it standalone still resolves REPO_ROOT correctly.
        spec.loader.exec_module(module)
        exit_code = module.main()
        self.assertEqual(
            exit_code,
            0,
            "scripts/validate_k8s_manifests.py reported failures — see validator output.",
        )
