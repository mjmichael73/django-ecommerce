"""Postgres configuration must never fall back to default credentials.

Card g89b45f5 — Phase 1.1 (critical security).
"""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from ecommerce.db_config import postgres_database


class PostgresDatabaseTests(SimpleTestCase):
    def test_missing_password_is_rejected(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'POSTGRES_PASSWORD'):
            postgres_database({'POSTGRES_DB': 'ecommerce'})

    def test_empty_password_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database({'POSTGRES_DB': 'ecommerce', 'POSTGRES_PASSWORD': ''})

    def test_explicit_credentials_pass_through(self):
        cfg = postgres_database(
            {
                'POSTGRES_DB': 'shop',
                'POSTGRES_USER': 'shopuser',
                'POSTGRES_PASSWORD': 's3cret-pass',
                'POSTGRES_HOST': 'db.internal',
                'POSTGRES_PORT': '5433',
            }
        )
        self.assertEqual(cfg['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(cfg['NAME'], 'shop')
        self.assertEqual(cfg['USER'], 'shopuser')
        self.assertEqual(cfg['PASSWORD'], 's3cret-pass')
        self.assertEqual(cfg['HOST'], 'db.internal')
        self.assertEqual(cfg['PORT'], '5433')

    def test_host_and_port_defaults_when_omitted(self):
        cfg = postgres_database({'POSTGRES_DB': 'shop', 'POSTGRES_PASSWORD': 'pw'})
        self.assertEqual(cfg['HOST'], 'localhost')
        self.assertEqual(cfg['PORT'], '5432')
