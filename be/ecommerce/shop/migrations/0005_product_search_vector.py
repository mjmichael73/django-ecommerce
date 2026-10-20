# Full-text search infrastructure (card bf247ef9).
#
# Adds the Product.search_vector column plus — on PostgreSQL only — the
# backfill, the trigger that keeps it current on every INSERT/UPDATE, and the
# GIN index over it. Application code never writes the column.
#
# Everything PostgreSQL-specific lives inside the vendor-guarded RunPython:
# RunSQL runs unconditionally on every backend (it would break the project's
# SQLite fallback), and a GinIndex declared via AddIndex would try to emit
# ``USING gin`` on SQLite. The column itself is created by AddField: on
# Postgres SearchVectorField maps to ``tsvector``, on SQLite it is an unused
# column with no affinity implications.
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations

# Name lexemes are weight A, description lexemes weight B — see
# shop.search._NAME_WEIGHT / _DESCRIPTION_WEIGHT for the matching rank
# weights. One concatenated vector per row, recomputed by the trigger so no
# application code path can leave the index stale.
SQL_BACKFILL = """
    UPDATE shop_product
    SET search_vector =
        setweight(to_tsvector('pg_catalog.english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('pg_catalog.english', coalesce(description, '')), 'B')
"""

SQL_TRIGGER_FUNCTION = """
    CREATE OR REPLACE FUNCTION shop_product_search_vector_update() RETURNS trigger AS $$
    BEGIN
        new.search_vector :=
            setweight(to_tsvector('pg_catalog.english', coalesce(new.name, '')), 'A') ||
            setweight(to_tsvector('pg_catalog.english', coalesce(new.description, '')), 'B');
        RETURN new;
    END
    $$ LANGUAGE plpgsql;
"""

SQL_TRIGGER = """
    CREATE TRIGGER search_vector_update
    BEFORE INSERT OR UPDATE OF name, description ON shop_product
    FOR EACH ROW EXECUTE PROCEDURE shop_product_search_vector_update();
"""

SQL_INDEX = """
    CREATE INDEX IF NOT EXISTS shop_product_search_vector_gin
    ON shop_product USING gin(search_vector);
"""

SQL_DROP_TRIGGER = 'DROP TRIGGER IF EXISTS search_vector_update ON shop_product;'

SQL_DROP_INDEX = 'DROP INDEX IF EXISTS shop_product_search_vector_gin;'

SQL_DROP_TRIGGER_FUNCTION = (
    'DROP FUNCTION IF EXISTS shop_product_search_vector_update();'
)


def _is_postgres(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def forward_postgres(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SQL_BACKFILL)
        cursor.execute(SQL_TRIGGER_FUNCTION)
        cursor.execute(SQL_TRIGGER)
        cursor.execute(SQL_INDEX)


def backward_postgres(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SQL_DROP_TRIGGER)
        cursor.execute(SQL_DROP_INDEX)
        cursor.execute(SQL_DROP_TRIGGER_FUNCTION)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0004_productreview_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='search_vector',
            field=SearchVectorField(null=True, editable=False),
        ),
        migrations.RunPython(forward_postgres, backward_postgres),
    ]
