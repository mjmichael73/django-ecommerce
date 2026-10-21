"""Postgres configuration must never fall back to default credentials.

Card g89b45f5 — Phase 1.1 (critical security).
Connection-pooling support — card a8g83e7c.
"""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from ecommerce.db_config import postgres_database

_BASE_ENV = {
    'POSTGRES_DB': 'shop',
    'POSTGRES_USER': 'shopuser',
    'POSTGRES_PASSWORD': 's3cret-pass',
    'POSTGRES_HOST': 'db.internal',
    'POSTGRES_PORT': '5433',
}


class PostgresDatabaseTests(SimpleTestCase):
    def test_missing_password_is_rejected(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'POSTGRES_PASSWORD'):
            postgres_database({'POSTGRES_DB': 'ecommerce'})

    def test_empty_password_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database({'POSTGRES_DB': 'ecommerce', 'POSTGRES_PASSWORD': ''})

    def test_explicit_credentials_pass_through(self):
        cfg = postgres_database(dict(_BASE_ENV))
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

    # ------------------------------------------------------------------
    # CONN_MAX_AGE / connection pooling — card a8g83e7c
    # ------------------------------------------------------------------

    def _expected_base(self):
        """The 6 core keys the function always returns, in insertion order."""
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'shop',
            'USER': 'shopuser',
            'PASSWORD': 's3cret-pass',
            'HOST': 'db.internal',
            'PORT': '5433',
        }

    def test_db_conn_max_age_unset_adds_no_keys(self):
        env = dict(_BASE_ENV)
        env.pop('DB_CONN_MAX_AGE', None)
        cfg = postgres_database(env)
        self.assertEqual(cfg, self._expected_base())

    def test_db_conn_max_age_empty_adds_no_keys(self):
        cfg = postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': ''})
        self.assertEqual(cfg, self._expected_base())

    def test_db_conn_max_age_zero(self):
        cfg = postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': '0'})
        self.assertEqual(cfg['CONN_MAX_AGE'], 0)
        self.assertNotIn('CONN_HEALTH_CHECKS', cfg)

    def test_db_conn_max_age_positive(self):
        cfg = postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': '60'})
        self.assertEqual(cfg['CONN_MAX_AGE'], 60)
        self.assertTrue(cfg['CONN_HEALTH_CHECKS'])

    def test_db_conn_max_age_positive_preserves_other_keys(self):
        cfg = postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': '30'})
        self.assertEqual(cfg['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(cfg['NAME'], 'shop')
        self.assertEqual(cfg['USER'], 'shopuser')
        self.assertEqual(cfg['PASSWORD'], 's3cret-pass')
        self.assertEqual(cfg['HOST'], 'db.internal')
        self.assertEqual(cfg['PORT'], '5433')

    def test_db_conn_max_age_negative_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': '-1'})

    def test_db_conn_max_age_non_integer_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database({**_BASE_ENV, 'DB_CONN_MAX_AGE': 'abc'})

    def test_db_conn_max_age_with_password_missing(self):
        """Password check still fires before CONN_MAX_AGE validation."""
        with self.assertRaisesMessage(ImproperlyConfigured, 'POSTGRES_PASSWORD'):
            postgres_database({'POSTGRES_DB': 'shop', 'DB_CONN_MAX_AGE': '60'})
