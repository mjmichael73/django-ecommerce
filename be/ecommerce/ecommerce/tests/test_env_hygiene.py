"""Repo hygiene guards for the environment/credential setup.

Card g89b45f5 — Phase 1.1 (critical security): no default Postgres
credentials in Compose or committed templates; `.env` is the only place
live credentials live, and the old `make env` bootstrap is gone.

The tests below inspect files at the repository root. Inside the app
image (which only contains `be/ecommerce`) there is no checkout to
inspect, so they skip themselves instead of failing.
"""
import re
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


class EnvHygieneTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )

    def _read(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding='utf-8')

    def test_compose_requires_postgres_password_with_no_fallback(self):
        compose = self._read('docker-compose.yml')
        # No `:-default` interpolation for the password anywhere.
        self.assertNotIn(
            'POSTGRES_PASSWORD:-',
            compose,
            'docker-compose.yml must not provide a default Postgres password',
        )
        # Every service that wires Postgres to the app must fail fast instead.
        # (Count env-entry lines — a bare substring match would also hit the
        # `${POSTGRES_DB:-…}` interpolation on the same line.)
        db_entries = len(re.findall(r'(?m)^\s+POSTGRES_DB:', compose))
        self.assertEqual(
            compose.count('POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?'),
            db_entries,
            'each service setting POSTGRES_DB must require POSTGRES_PASSWORD',
        )

    def test_env_example_ships_no_live_secrets(self):
        example = self._read('.env.example')
        for key in ('SECRET_KEY', 'POSTGRES_PASSWORD'):
            self.assertNotRegex(
                example,
                rf'(?m)^{key}=\S',
                f'.env.example must leave {key} blank (no committed secret values)',
            )

    def test_env_file_is_gitignored(self):
        self.assertRegex(
            self._read('.gitignore'),
            r'(?m)^\.env$',
            '.gitignore must ignore .env',
        )

    def test_env_is_not_tracked_by_git(self):
        # `.env` may exist on disk but must never be committed again.
        # (Git plumbing needs a git binary; skip if unavailable.)
        import shutil
        import subprocess

        git = shutil.which('git')
        if git is None or not (REPO_ROOT / '.git').exists():
            self.skipTest('git not available or not a checkout')
        out = subprocess.run(
            [git, 'ls-files', '.env'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(
            out.strip(),
            '',
            '.env must not be tracked by git (git rm --cached .env)',
        )

    def test_make_env_target_and_bootstrap_script_are_removed(self):
        makefile = self._read('Makefile')
        self.assertNotRegex(
            makefile,
            r'(?m)^env:',
            'the `make env` target was removed — copy .env.example to .env manually',
        )
        self.assertFalse(
            (REPO_ROOT / 'scripts' / 'bootstrap_env.py').exists(),
            'scripts/bootstrap_env.py existed only for `make env` and is removed',
        )
