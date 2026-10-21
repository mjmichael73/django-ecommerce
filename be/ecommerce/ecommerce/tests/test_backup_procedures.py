"""Tests for backup procedures (card 36d3gdd6 — Phase 4.2).

These tests inspect files in the repository checkout (docker/backup/,
docker-compose.yml, Makefile) and unit-test the pure-function retain.py
helper. They skip themselves when the repo root is absent — i.e. when the
test suite runs inside the app image (which only contains be/ecommerce and
has no docker/backup/ checkout).

The compose-config tests additionally require the `docker` binary on PATH;
they skip when docker is unavailable (e.g. inside the app image). On the host
they run for real:

    docker run --rm -v "$(pwd)":/mnt -w /mnt/be/ecommerce --entrypoint python \
        django-ecommerce-web manage.py test ecommerce.tests.test_backup_procedures -v2
"""
import importlib.util
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from django.test import SimpleTestCase


def _find_repo_root() -> Path | None:
    """Walk up from this test file to find the repo root (has docker-compose.yml)."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / 'docker-compose.yml').is_file():
            if (candidate / 'docker' / 'backup').is_dir():
                return candidate
            return candidate
    return None


REPO_ROOT = _find_repo_root()


def _load_retain_module():
    """Dynamically load docker/backup/retain.py (it's not on the app's path)."""
    spec = importlib.util.spec_from_file_location(
        "retain", str(REPO_ROOT / 'docker' / 'backup' / 'retain.py')
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RetainHelperTests(SimpleTestCase):
    """Unit tests for the pure-function retain.py selector."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image — '
                'docker/backup is not copied in)'
            )
        cls.retain = _load_retain_module()

    def test_select_retention_keeps_all_when_fewer_than_keep_n(self):
        entries = ["db-20240101T000000Z.dump", "db-20240201T000000Z.dump"]
        keep, delete = self.retain.select_retention(entries, keep_n=14)
        self.assertEqual(len(keep), 2)
        self.assertEqual(delete, [])

    def test_select_retention_keeps_newest_n_deletes_rest(self):
        # 20 entries (2024-01-01 .. 2024-01-20, all at 00:00:00Z), keep 5.
        entries = [f"db-202401{i:02d}T000000Z.dump" for i in range(1, 21)]
        keep, delete = self.retain.select_retention(entries, keep_n=5)
        self.assertEqual(len(keep), 5)
        self.assertEqual(len(delete), 15)
        # Keep list is newest-first (day 20 down to 16).
        self.assertEqual(keep[0], "db-20240120T000000Z.dump")
        self.assertEqual(keep[-1], "db-20240116T000000Z.dump")
        # Delete list is the tail of the desc-sorted order: 6th-newest first.
        self.assertEqual(delete[0], "db-20240115T000000Z.dump")
        self.assertEqual(delete[-1], "db-20240101T000000Z.dump")

    def test_select_retention_exact_lists(self):
        entries = [
            "media-20240301T000000Z.tar.gz",
            "media-20240101T000000Z.tar.gz",
            "media-20240201T000000Z.tar.gz",
        ]
        keep, delete = self.retain.select_retention(entries, keep_n=2)
        self.assertEqual(keep, [
            "media-20240301T000000Z.tar.gz",
            "media-20240201T000000Z.tar.gz",
        ])
        self.assertEqual(delete, ["media-20240101T000000Z.tar.gz"])

    def test_select_retention_empty_entries(self):
        keep, delete = self.retain.select_retention([], keep_n=3)
        self.assertEqual(keep, [])
        self.assertEqual(delete, [])

    def test_parse_args_dry_run_and_keep_n(self):
        args = self.retain.parse_args(["--dry-run", "--keep-n", "7", "a", "b"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.keep_n, 7)
        self.assertEqual(args.entries, ["a", "b"])

    def test_parse_args_defaults(self):
        args = self.retain.parse_args(["a", "b"])
        self.assertFalse(args.dry_run)
        self.assertEqual(args.keep_n, 14)
        self.assertEqual(args.entries, ["a", "b"])

    def test_parse_args_invalid_keep_n_raises(self):
        with self.assertRaises(SystemExit):
            self.retain.parse_args(["--keep-n", "not-a-number"])

    def test_main_emits_keep_list_to_stdout_for_rotation(self):
        """Regression for card 36d3gdd6 review (Critical #1).

        backup.sh's real-run rotation shells out to retain.py and consumes the
        KEEP list from stdout to decide what to delete (delete everything NOT
        in the keep list). This test pins that contract: main() must print the
        keep list to stdout (one per line) and NOT the delete plan (which goes
        to stderr only under --dry-run).
        """
        import io
        import contextlib
        entries = [f"db-202401{i:02d}T000000Z.dump" for i in range(1, 11)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.retain.main(["--keep-n", "3", *entries])
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        # Keep list = 3 newest, newest-first (days 10, 9, 8).
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "db-20240110T000000Z.dump")
        self.assertEqual(lines[-1], "db-20240108T000000Z.dump")
        # The 4th-newest (deleted) must NOT appear in the keep list.
        self.assertNotIn("db-20240107T000000Z.dump", lines)

    def test_main_dry_run_writes_plan_to_stderr(self):
        """Under --dry-run the keep/delete plan goes to stderr (not stdout),
        so a bare stdout capture yields the keep list only for deletion logic."""
        import io
        import contextlib
        entries = [f"db-202401{i:02d}T000000Z.dump" for i in range(1, 6)]
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.retain.main(["--keep-n", "2", "--dry-run", "--kind", "db", *entries])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines(), [
            "db-20240105T000000Z.dump",
            "db-20240104T000000Z.dump",
        ])
        self.assertIn("would delete: db-20240103T000000Z.dump", err.getvalue())


class BackupScriptTextTests(SimpleTestCase):
    """Static checks on backup.sh content."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )
        cls.backup_sh = (REPO_ROOT / 'docker' / 'backup' / 'backup.sh').read_text(
            encoding='utf-8'
        )

    def test_backup_sh_has_strict_mode(self):
        self.assertIn('set -eu', self.backup_sh)

    def test_backup_sh_invokes_pg_dump_fc(self):
        self.assertIn('pg_dump -Fc', self.backup_sh)

    def test_backup_sh_invokes_retain_py(self):
        self.assertIn('retain.py', self.backup_sh)

    def test_backup_sh_dry_run_writes_nothing(self):
        # --dry-run branch must exist and must not run pg_dump/tar.
        self.assertIn('--dry-run', self.backup_sh)

    def test_backup_sh_writes_to_backups_dir(self):
        self.assertIn('/backups', self.backup_sh)


class RestoreScriptTextTests(SimpleTestCase):
    """Static checks on restore.sh content — critical safety guards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )
        cls.restore_sh = (REPO_ROOT / 'docker' / 'backup' / 'restore.sh').read_text(
            encoding='utf-8'
        )

    def test_restore_sh_references_scratch_db(self):
        self.assertIn('ecommerce_restore_drill', self.restore_sh)

    def test_restore_sh_never_writes_to_live_db(self):
        """Hard guard: destructive commands target ONLY the scratch db / postgres.

        Every `DROP DATABASE` and every `pg_restore -d` / `psql -d` target must
        reference only `ecommerce_restore_drill` (the scratch db) or `postgres`
        (the maintenance db used for DDL). Any reference to the live db name
        `ecommerce` or the `$LIVE_DB`/`$POSTGRES_DB` variables in a destructive
        command position is rejected — this catches variable-indirection
        loopholes the negative regexes alone would miss.
        """
        import re as _re
        sh = self.restore_sh
        # Strip full-line comments so comment text mentioning 'ecommerce' doesn't
        # trip the live-db-name guard.
        code = "\n".join(
            line for line in sh.splitlines() if not line.lstrip().startswith("#")
        )
        # Every DROP DATABASE target must be the scratch db (variable or literal).
        allowed_scratch = ('"${SCRATCH_DB}"', '${SCRATCH_DB}', 'ecommerce_restore_drill',
                           '"${SCRATCH_DB}"')
        for m in _re.finditer(r'(?mi)DROP\s+DATABASE\s+IF\s+EXISTS\s+(\S+)', code):
            target = m.group(1).rstrip(';').strip().strip('"').strip("'")
            self.assertIn(
                target, allowed_scratch,
                f'DROP DATABASE must only target the scratch db, got: {target!r}'
            )
        # Every pg_restore -d / psql -d target must be the scratch db or postgres.
        for m in _re.finditer(r'(?m)(?<!\S)(?:pg_restore|psql)\s+.*?-\s*d\s+(\S+)', code):
            target = m.group(1).rstrip(';').strip().strip('"').strip("'")
            safe = (
                'ecommerce_restore_drill' in target
                or '${SCRATCH_DB}' in target
                or '$SCRATCH_DB' in target
                or target == 'postgres'
            )
            self.assertTrue(
                safe,
                f'pg_restore/psql -d targets must be scratch db or postgres, got: {target!r}'
            )
        # Negative: no destructive command may reference the live db variable.
        for pat in (
            r'DROP\s+DATABASE.*LIVE_DB',
            r'DROP\s+DATABASE.*"\$POSTGRES_DB"',
        ):
            self.assertNotRegex(code, pat, f'live db must not be targeted: {pat}')

    def test_restore_sh_live_db_name_only_in_comments(self):
        """The literal live db name 'ecommerce' may only appear in comments/echo,
        never in a DROP DATABASE or pg_restore -d destructive position."""
        import re as _re
        sh = self.restore_sh
        # Only inspect non-comment lines for destructive statements.
        code = "\n".join(
            line for line in sh.splitlines() if not line.lstrip().startswith("#")
        )
        for m in _re.finditer(r'(?mi)DROP\s+DATABASE\b[^;]*;', code):
            stmt = m.group(0)
            self.assertNotIn(
                'ecommerce', stmt,
                f'live db name must not appear in DROP DATABASE: {stmt!r}'
            )

    def test_restore_sh_confirms_scratch(self):
        self.assertIn('CONFIRM=scratch', self.restore_sh)
        self.assertIn('pg_restore -l', self.restore_sh)

    def test_restore_sh_has_strict_mode(self):
        self.assertIn('set -eu', self.restore_sh)


class ComposeBackupProfileTests(SimpleTestCase):
    """Verify docker-compose.yml has the backup profile wired (host shell only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )
        if shutil.which('docker') is None:
            raise unittest.SkipTest('docker binary not available on this host')
        cls._docker = shutil.which('docker')

    def _compose_config(self, extra_env=None):
        env = {
            "SECRET_KEY": "test",
            "POSTGRES_PASSWORD": "test",
            "POSTGRES_USER": "ecommerce",
            "POSTGRES_DB": "ecommerce",
            "POSTGRES_HOST": "postgres:5432",
        }
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [self._docker, 'compose', '--profile', 'backup', 'config'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(
            proc.returncode, 0,
            f'docker compose config failed: {proc.stderr}'
        )
        return proc.stdout

    def test_compose_config_contains_backup_service(self):
        out = self._compose_config()
        self.assertRegex(out, r'(?m)^  backup:\n')

    def test_compose_config_backup_uses_postgres_alpine_image(self):
        out = self._compose_config()
        self.assertIn('postgres:18-alpine', out)

    def test_compose_config_backup_mounts_media_readonly(self):
        out = self._compose_config()
        self.assertIn('media_data:/media', out)
        self.assertIn('read_only', out)

    def test_compose_config_contains_redis_backup_when_env_set(self):
        out = self._compose_config(extra_env={"BACKUP_REDIS": "1"})
        self.assertRegex(out, r'(?m)^  redis-backup:\n')
        self.assertIn('redis:8-alpine', out)

    def test_compose_config_redis_backup_is_profile_gated(self):
        out = self._compose_config()
        self.assertRegex(out, r'(?m)^  redis-backup:\n')


class MakefileBackupTargetsTests(SimpleTestCase):
    """Verify Makefile backup targets exist."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )
        cls.makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')

    def test_has_backup_target(self):
        self.assertRegex(self.makefile, r'(?m)^backup:\s*##')

    def test_has_backup_restore_target(self):
        self.assertRegex(self.makefile, r'(?m)^backup-restore:\s*##')

    def test_has_backup_dry_run_target(self):
        self.assertRegex(self.makefile, r'(?m)^backup-dry-run:\s*##')

    def test_has_backup_redis_target(self):
        self.assertRegex(self.makefile, r'(?m)^backup-redis:\s*##')

    def test_targets_use_profile_backup(self):
        self.assertIn('--profile backup', self.makefile)


class GitignoreBackupsTests(SimpleTestCase):
    """Verify backups/ is gitignored."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if REPO_ROOT is None:
            raise unittest.SkipTest(
                'repository root not present (running inside the app image)'
            )
        cls.gitignore = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8')

    def test_backups_dir_is_gitignored(self):
        self.assertRegex(self.gitignore, r'(?m)^backups/$')
