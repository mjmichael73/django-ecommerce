"""Tests for Sentry env-var wiring in docker-compose.yml (card 5ggb4929).

Verifies that every documented SENTRY_* env var is passed to both the ``web``
and ``celery-worker`` services in docker-compose.yml, so operators can tune
scrubbing, integrations, and sampling from a single .env file.

The docker-compose.yml is bind-mounted read-only via the repo root mount
(``/repo``) during test runs (see Makefile). If the file isn't present
(e.g. local venv without Docker), tests are skipped.
"""
import os
import re

from django.test import SimpleTestCase

# The set of all SENTRY_* env vars documented in .env.example and sentry_config.
SENTRY_ENV_VARS = frozenset({
    'SENTRY_DSN',
    'SENTRY_ENVIRONMENT',
    'SENTRY_RELEASE',
    'SENTRY_TRACES_SAMPLE_RATE',
    'SENTRY_PROFILES_SAMPLE_RATE',
    'SENTRY_SEND_DEFAULT_PII',
    'SENTRY_SCRUB_REQUEST_HEADERS',
    'SENTRY_SCRUB_REQUEST_COOKIES',
    'SENTRY_SCRUB_PAYMENT_DATA',
    'SENTRY_SENSITIVE_FIELDS',
    'SENTRY_CELERY_ENABLED',
    'SENTRY_DJANGO_ENABLED',
})

# Services that must carry the full set of SENTRY_* vars.
SENTRY_SERVICES = frozenset({'web', 'celery-worker'})


def _compose_path() -> str | None:
    """Locate docker-compose.yml from the test container's mounts.

    The Makefile test target bind-mounts the repo root at ``/repo`` and its
    ``docker/`` subdirectory at ``/mnt/docker``. The compose file lives at
    the repo root.
    """
    for path in (
        '/repo/docker-compose.yml',
        '/home/ubuntu/projects/personal/django-ecommerce/docker-compose.yml',
    ):
        if os.path.isfile(path):
            return path
    return None


def _parse_service_env_vars(compose_text: str) -> dict[str, set[str]]:
    """Parse docker-compose.yml as text, extracting env var names per service.

    Uses a lightweight line-based parser — no PyYAML dependency. Each service
    block starts with ``  <name>:`` and env vars are ``      VAR_NAME: ...``.
    """
    services: dict[str, set[str]] = {}
    current_service: str | None = None

    for line in compose_text.splitlines():
        # Match a service definition at 2-space indent: "  web:" or "  celery-worker:"
        svc_match = re.match(r'^  ([a-zA-Z][a-zA-Z0-9_-]*):\s*$', line)
        if svc_match:
            current_service = svc_match.group(1)
            if current_service in SENTRY_SERVICES:
                services[current_service] = set()
            continue
        # Match env var lines inside a service's environment block (6-space indent).
        if current_service and current_service in services:
            env_match = re.match(r'^      ([A-Z_][A-Z0-9_]*):\s', line)
            if env_match:
                var_name = env_match.group(1)
                services[current_service].add(var_name)
    return services


class ComposeSentryWiringTests(SimpleTestCase):
    """Every documented SENTRY_* var is wired to web and celery-worker."""

    def setUp(self):
        self.compose_path = _compose_path()
        self._compose_text = None
        if self.compose_path:
            with open(self.compose_path) as f:
                self._compose_text = f.read()

    def test_compose_file_found(self):
        """docker-compose.yml is reachable from the test container."""
        self.assertIsNotNone(self.compose_path, 'docker-compose.yml not found')

    def test_all_services_have_sentry_vars(self):
        """All SENTRY_* vars appear in both web and celery-worker env blocks."""
        if not self.compose_path or not self._compose_text:
            self.skipTest('docker-compose.yml not found in test container')

        service_vars = _parse_service_env_vars(self._compose_text)
        for service_name in SENTRY_SERVICES:
            with self.subTest(service=service_name):
                self.assertIn(service_name, service_vars,
                              f'{service_name} service missing from compose')
                env_keys = service_vars[service_name]
                for var in SENTRY_ENV_VARS:
                    self.assertIn(
                        var, env_keys,
                        f'{var} missing from {service_name} environment in docker-compose.yml'
                    )

    def test_sentry_dsn_uses_var_substitution(self):
        """SENTRY_DSN is wired with ${VAR:-} interpolation (not a literal)."""
        if not self.compose_path or not self._compose_text:
            self.skipTest('docker-compose.yml not found in test container')

        for service_name in SENTRY_SERVICES:
            service_vars = _parse_service_env_vars(self._compose_text)
            if service_name not in service_vars:
                continue
            dsn_line = None
            for line in self._compose_text.splitlines():
                if f'      SENTRY_DSN:' in line:
                    dsn_line = line
                    break
            self.assertIsNotNone(dsn_line, f'SENTRY_DSN not found in {service_name}')
            self.assertIn(
                '${SENTRY_DSN', dsn_line,
                f'SENTRY_DSN in {service_name} should use env interpolation'
            )
