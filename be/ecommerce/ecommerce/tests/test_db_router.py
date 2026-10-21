"""Tests for read-replica configuration + routing (card 79e887g9).

SimpleTestCase, pure-function / pure-class tests — no database connection.
Covers the full truth table for ``parse_replica_urls`` and
``PrimaryReplicaRouter``, and the settings-level wiring (DATABASE_ROUTERS
absent when unconfigured — tested on the builder output, not the live
settings object, to stay hermetic).
"""
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from ecommerce.db_config import parse_replica_urls, _parse_replica_url
from ecommerce.db_router import PrimaryReplicaRouter

# A minimal env that satisfies postgres_database's POSTGRES_PASSWORD / POSTGRES_DB
# requirements — used so we can exercise the *replica* parsing path which is
# independent of the primary password rule.
_POSTGRES_ENV = {
    'POSTGRES_DB': 'shop',
    'POSTGRES_USER': 'shopuser',
    'POSTGRES_PASSWORD': 's3cret',
    'POSTGRES_HOST': 'db.internal',
    'POSTGRES_PORT': '5433',
}


def _model_like(db):
    """Build a real-Django-shaped model instance stand-in.

    Django model instances carry a ``Model._state`` attribute whose ``db``
    field records which database the instance was loaded/saved from. Tests
    must exercise the actual ``getattr(obj, '_state', None) → getattr(state, 'db', None)``
    chain against that shape — not a loose ``mock.Mock`` whose every attribute
    returns another Mock.
    """
    return SimpleNamespace(_state=SimpleNamespace(db=db))


# ---------------------------------------------------------------------------
# Replica URL parsing
# ---------------------------------------------------------------------------

class ParseReplicaUrlsTests(SimpleTestCase):
    def test_unset_returns_empty(self):
        self.assertEqual(parse_replica_urls({}), {})

    def test_empty_string_returns_empty(self):
        self.assertEqual(parse_replica_urls({'DB_REPLICA_URLS': ''}), {})

    def test_only_blank_segments_returns_empty(self):
        self.assertEqual(
            parse_replica_urls({'DB_REPLICA_URLS': ' , , '}),
            {},
        )

    def test_single_url(self):
        urls = 'postgres://user:pw@replica1:5432/shop'
        result = parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': urls})
        self.assertEqual(list(result.keys()), ['replica_0'])
        cfg = result['replica_0']
        self.assertEqual(cfg['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(cfg['NAME'], 'shop')
        self.assertEqual(cfg['USER'], 'user')
        self.assertEqual(cfg['PASSWORD'], 'pw')
        self.assertEqual(cfg['HOST'], 'replica1')
        self.assertEqual(cfg['PORT'], '5432')

    def test_two_urls_positioned_replica_0_and_1(self):
        urls = 'postgres://u:p@r1:5432/shop,postgres://u:p@r2:5432/shop'
        result = parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': urls})
        self.assertEqual(set(result.keys()), {'replica_0', 'replica_1'})
        self.assertEqual(result['replica_0']['HOST'], 'r1')
        self.assertEqual(result['replica_1']['HOST'], 'r2')

    def test_blank_segment_ignored_in_middle(self):
        urls = 'postgres://u:p@r1:5432/shop, ,postgres://u:p@r2:5432/shop'
        result = parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': urls})
        self.assertEqual(set(result.keys()), {'replica_0', 'replica_1'})

    def test_field_mapping_host_port_name(self):
        urls = 'postgres://alpha:beta@replica.host:6543/mydb'
        result = parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': urls})
        cfg = result['replica_0']
        self.assertEqual(cfg['NAME'], 'mydb')
        self.assertEqual(cfg['USER'], 'alpha')
        self.assertEqual(cfg['PASSWORD'], 'beta')
        self.assertEqual(cfg['HOST'], 'replica.host')
        self.assertEqual(cfg['PORT'], '6543')

    def test_default_port_when_omitted(self):
        urls = 'postgres://u:p@r1/shop'
        result = parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': urls})
        self.assertEqual(result['replica_0']['PORT'], '5432')

    def test_malformed_url_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': 'not-a-url'})

    def test_missing_hostname_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            parse_replica_urls({**_POSTGRES_ENV, 'DB_REPLICA_URLS': 'postgres:///shop'})


class ReplicaConnMaxAgeTests(SimpleTestCase):
    """The DB_CONN_MAX_AGE truth table must apply to replica aliases too."""

    def _base(self):
        return dict(_POSTGRES_ENV)

    def test_replica_unset_conn_max_age_adds_no_pooling_keys(self):
        result = parse_replica_urls(self._base() | {
            'DB_REPLICA_URLS': 'postgres://u:p@r1:5432/shop',
        })
        cfg = result['replica_0']
        self.assertNotIn('CONN_MAX_AGE', cfg)
        self.assertNotIn('CONN_HEALTH_CHECKS', cfg)

    def test_replica_conn_max_age_zero(self):
        result = parse_replica_urls(self._base() | {
            'DB_REPLICA_URLS': 'postgres://u:p@r1:5432/shop',
            'DB_CONN_MAX_AGE': '0',
        })
        cfg = result['replica_0']
        self.assertEqual(cfg['CONN_MAX_AGE'], 0)
        self.assertNotIn('CONN_HEALTH_CHECKS', cfg)

    def test_replica_conn_max_age_positive(self):
        result = parse_replica_urls(self._base() | {
            'DB_REPLICA_URLS': 'postgres://u:p@r1:5432/shop',
            'DB_CONN_MAX_AGE': '60',
        })
        cfg = result['replica_0']
        self.assertEqual(cfg['CONN_MAX_AGE'], 60)
        self.assertTrue(cfg['CONN_HEALTH_CHECKS'])

    def test_replica_conn_max_age_negative_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            parse_replica_urls(self._base() | {
                'DB_REPLICA_URLS': 'postgres://u:p@r1:5432/shop',
                'DB_CONN_MAX_AGE': '-1',
            })


# ---------------------------------------------------------------------------
# Router truth table
# ---------------------------------------------------------------------------

class PrimaryReplicaRouterTests(SimpleTestCase):
    def test_db_for_write_always_default(self):
        router = PrimaryReplicaRouter({'replica_0': {}, 'replica_1': {}})
        self.assertIsNone(router.db_for_write(None))

    def test_db_for_read_no_replicas_returns_none(self):
        router = PrimaryReplicaRouter()
        self.assertIsNone(router.db_for_read(None))

    def test_db_for_read_with_replicas_is_configured_alias(self):
        aliases = {'replica_0': {}, 'replica_1': {}}
        router = PrimaryReplicaRouter(aliases)
        # Seed random so the choice is deterministic for the assertion.
        with mock.patch('ecommerce.db_router.random.choice', return_value='replica_1'):
            self.assertEqual(router.db_for_read(None), 'replica_1')

    def test_db_for_read_no_replicas_inert(self):
        """When no replicas are configured, reads fall back to default."""
        router = PrimaryReplicaRouter(aliases={})
        result = router.db_for_read(None)
        self.assertIsNone(result)  # None → Django uses 'default'

    def test_allow_relation_default_and_replica(self):
        router = PrimaryReplicaRouter({'replica_0': {}})
        obj_default = _model_like('default')
        obj_replica = _model_like('replica_0')
        self.assertTrue(router.allow_relation(obj_default, obj_replica))
        self.assertTrue(router.allow_relation(obj_replica, obj_default))
        self.assertTrue(router.allow_relation(obj_default, obj_default))

    def test_allow_relation_outside_db_set_is_none(self):
        router = PrimaryReplicaRouter({'replica_0': {}})
        obj_other = _model_like('some_other_db')
        obj_default = _model_like('default')
        self.assertIsNone(router.allow_relation(obj_other, obj_default))

    def test_allow_migrate_only_default(self):
        router = PrimaryReplicaRouter({'replica_0': {}, 'replica_1': {}})
        self.assertTrue(router.allow_migrate('default', 'shop'))
        self.assertFalse(router.allow_migrate('replica_0', 'shop'))
        self.assertFalse(router.allow_migrate('replica_1', 'shop'))

    def test_router_with_no_aliases_still_inert(self):
        """A router with zero configured replicas never routes reads elsewhere."""
        router = PrimaryReplicaRouter(aliases={})
        self.assertIsNone(router.db_for_write(None))
        self.assertIsNone(router.db_for_read(None))
        self.assertFalse(router.allow_migrate('replica_0', 'shop'))


# ---------------------------------------------------------------------------
# Settings-level wiring (hermetic — inspect the builder, not live settings)
# ---------------------------------------------------------------------------

class SettingsWiringTests(SimpleTestCase):
    """Verify the settings builder produces the right structure.

    These tests re-run the *same logic* settings.py uses (parse_replica_urls +
    the conditional DATABASE_ROUTERS assignment) against synthetic envs, so
    they stay hermetic — they never import the real settings module and never
    touch a database.
    """

    def _settings_block(self, env):
        """Mirror the settings.py logic without importing Django settings.

        Returns (databases_dict, routers_list).
        """
        databases = {'default': {}}  # placeholder for the primary
        replica_aliases = parse_replica_urls(env)
        databases.update(replica_aliases)
        routers = (
            ['ecommerce.db_router.PrimaryReplicaRouter']
            if replica_aliases else []
        )
        return databases, routers

    def test_unconfigured_no_routers_no_aliases(self):
        databases, routers = self._settings_block(dict(_POSTGRES_ENV))
        self.assertNotIn('replica_0', databases)
        self.assertEqual(routers, [])

    def test_unconfigured_empty_string(self):
        databases, routers = self._settings_block(
            _POSTGRES_ENV | {'DB_REPLICA_URLS': ''}
        )
        self.assertNotIn('replica_0', databases)
        self.assertEqual(routers, [])

    def test_unconfigured_blank_segments_only(self):
        databases, routers = self._settings_block(
            _POSTGRES_ENV | {'DB_REPLICA_URLS': ' , , '}
        )
        self.assertNotIn('replica_0', databases)
        self.assertEqual(routers, [])

    def test_configured_adds_aliases_and_router(self):
        databases, routers = self._settings_block({
            **_POSTGRES_ENV,
            'DB_REPLICA_URLS': 'postgres://u:p@r1:5432/shop',
        })
        self.assertIn('replica_0', databases)
        self.assertEqual(
            routers, ['ecommerce.db_router.PrimaryReplicaRouter'],
        )

    def test_primary_database_shape_unchanged_when_replicas_off(self):
        """The unset-invariant: postgres_database must return the 6 core keys
        with no pooling keys when DB_CONN_MAX_AGE is absent — unchanged by this
        card."""
        # This is a sanity re-check of the pre-existing postgres_database
        # behaviour to document that replicas don't alter the primary.
        from ecommerce.db_config import postgres_database
        env = dict(_POSTGRES_ENV)
        env.pop('DB_CONN_MAX_AGE', None)
        cfg = postgres_database(env)
        core_keys = {'ENGINE', 'NAME', 'USER', 'PASSWORD', 'HOST', 'PORT'}
        self.assertEqual(set(cfg.keys()), core_keys)
        self.assertNotIn('CONN_MAX_AGE', cfg)
        self.assertNotIn('CONN_HEALTH_CHECKS', cfg)
