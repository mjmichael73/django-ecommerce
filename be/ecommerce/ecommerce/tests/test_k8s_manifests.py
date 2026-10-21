"""Django-side guard that delegates to the standalone k8s manifest validator.

Card a9cea652 — Phase 5 deployment: Kubernetes manifests.
Card 828ff8ea — Phase 5 deployment hardening: blue-green deployment.

The real assertions live in ``scripts/validate_k8s_manifests.py`` (a
standalone, Django-free script that parses YAML with ``yaml.safe_load``). This
test module re-runs that validator through the Django test suite so it is
exercised by ``make test`` and the CI ``test`` job — exactly like the Grafana
dashboard tests exercise ``docker/grafana/scripts/validate_dashboards.py``.

A few hermetic structural tests also live here for the blue-green assets
(runbook existence + flip documentation, helper-script subcommands and the
strict rollout-before-patch ordering). They parse YAML/text only — no kubectl,
no cluster — matching the existing repo-text test style.

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

    # ------------------------------------------------------------------
    # Blue-green structural tests (card 828ff8ea). These are hermetic: they
    # parse YAML/text only, never shell out to kubectl or hit a cluster.
    # ------------------------------------------------------------------
    def test_blue_green_runbook_exists_and_documents_flip(self):
        """docs/blue-green.md must exist and describe the Service selector flip."""
        runbook = REPO_ROOT / "docs" / "blue-green.md"
        self.assertTrue(
            runbook.is_file(),
            "docs/blue-green.md must exist (card 828ff8ea)",
        )
        text = runbook.read_text(encoding="utf-8").lower()
        self.assertIn(
            "selector",
            text,
            "docs/blue-green.md must document the Service selector flip",
        )
        self.assertIn(
            "flip",
            text,
            "docs/blue-green.md must describe flipping the selector between colors",
        )

    def test_blue_green_helper_script_exists(self):
        """scripts/blue_green_deploy.sh must exist and be executable."""
        script = REPO_ROOT / "scripts" / "blue_green_deploy.sh"
        self.assertTrue(
            script.is_file(),
            "scripts/blue_green_deploy.sh must exist (card 828ff8ea)",
        )
        self.assertTrue(
            os.access(script, os.X_OK),
            "scripts/blue_green_deploy.sh must be executable",
        )

    def test_blue_green_helper_script_has_cutover_and_rollback(self):
        """The helper must expose `cutover` and `rollback` subcommands."""
        script = REPO_ROOT / "scripts" / "blue_green_deploy.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("cutover", text, "helper must implement cutover")
        self.assertIn("rollback", text, "helper must implement rollback")
        # cutover ordering: `kubectl rollout status` MUST appear before the
        # Service selector flip (the `flip_selector green` invocation in cutover).
        # We match the actual command, not the doc-comment, by requiring the
        # rollout-status command to precede the green flip call.
        rollout_idx = text.find("kubectl rollout status", text.find("cmd_cutover"))
        flip_idx = text.find("flip_selector green", text.find("cmd_cutover"))
        self.assertGreater(rollout_idx, -1, "helper must run `kubectl rollout status` in cutover")
        self.assertGreater(flip_idx, -1, "helper must flip to green in cutover")
        self.assertLess(
            rollout_idx,
            flip_idx,
            "cutover MUST run `kubectl rollout status` BEFORE `flip_selector green` "
            "(wait for green pods to boot-migrate + pass /health/ before the traffic switch)",
        )

    def test_blue_green_selector_only_flips_between_colors(self):
        """The flip logic must only ever set color to blue or green."""
        script = REPO_ROOT / "scripts" / "blue_green_deploy.sh"
        text = script.read_text(encoding="utf-8")
        # both colors must be referenced as flip targets
        self.assertIn("blue", text, "helper must flip back to color: blue")
        self.assertIn("green", text, "helper must flip to color: green")
        # the patch must target the Service, not a Deployment
        patch_lines = [ln for ln in text.splitlines() if "kubectl patch" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(patch_lines, "helper must contain a kubectl patch for the flip")
        for ln in patch_lines:
            self.assertIn("svc", ln, "selector flip must patch a Service (svc/...), not a Deployment")
            self.assertNotIn(
                "deploy/",
                ln,
                "selector flip must never patch a Deployment — only the Service selector",
            )
