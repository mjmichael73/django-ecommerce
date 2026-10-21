"""CI migration-check guard test (card 65b22ag7).

Asserts that the CI workflow contains a ``makemigrations --check`` step so that
a model change without a corresponding migration fails the build.

The CI workflow file lives at the repository root and is NOT copied into the
app image (the Dockerfile only does ``COPY be/ecommerce /app``), so the tests
skip themselves when ``.github/workflows/ci.yml`` is absent — see
``test_env_hygiene.py`` for the same pattern.
"""
import unittest
from pathlib import Path

from django.test import SimpleTestCase


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / 'docker-compose.yml').is_file():
            return candidate
    return None


REPO_ROOT = _find_repo_root()


class MigrationStrategyCITests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ci_path = REPO_ROOT / '.github' / 'workflows' / 'ci.yml' if REPO_ROOT else None
        if ci_path is None or not ci_path.is_file():
            raise unittest.SkipTest(
                '.github/workflows/ci.yml is not present '
                '(running inside the app image — only be/ecommerce is copied)'
            )
        cls.ci_yaml = ci_path.read_text(encoding='utf-8')

    def test_ci_yaml_exists_and_is_readable(self):
        """The workflow file is reachable and non-empty."""
        self.assertTrue(self.ci_yaml.strip(), 'ci.yml must be non-empty')

    def test_makemigrations_check_step_present(self):
        """CI must run ``makemigrations --check`` to gate missing migrations."""
        self.assertIn('makemigrations', self.ci_yaml)
        self.assertIn('--check', self.ci_yaml)

    def test_makemigrations_check_uses_no_input(self):
        """The check step must pass ``--no-input`` so it never prompts."""
        # ``--no-input`` and ``--check`` appear on the same makemigrations run line.
        lines = [line for line in self.ci_yaml.splitlines() if 'makemigrations' in line]
        self.assertTrue(lines, 'expected at least one makemigrations line')
        joined = '\n'.join(lines)
        self.assertIn('--no-input', joined)
