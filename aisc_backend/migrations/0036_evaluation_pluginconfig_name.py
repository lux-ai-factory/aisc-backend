from django.db import migrations, models
from django.db.migrations.operations.fields import AddField


class AddFieldIfMissing(AddField):
    """AddField that never fails when the column already exists: for Postgres
    it emits `ADD COLUMN IF NOT EXISTS` (reuses the existing column), and for
    other backends it introspects and skips the add when it is already there."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        to_model = to_state.apps.get_model(app_label, self.model_name)
        field = to_model._meta.get_field(self.name)

        # PostgreSQL has no trouble with a pre-existing column here: never fail.
        if schema_editor.connection.vendor == 'postgresql':
            column = field.db_column or field.name
            definition = field.db_parameters(connection=schema_editor.connection)
            sql = (
                f"ALTER TABLE {schema_editor.quote_name(to_model._meta.db_table)} "
                f"ADD COLUMN IF NOT EXISTS {schema_editor.quote_name(column)} "
                f"{definition['type']}"
            )
            sql += " NULL" if field.null else " NOT NULL"
            if field.has_default():
                default = field.default() if callable(field.default) else field.default
                sql += f" DEFAULT {schema_editor.quote_value(default)}"
            schema_editor.execute(sql)
            return

        # Other backends (e.g. sqlite dev/test): introspect and skip if present.
        table = to_model._meta.db_table
        column_name = field.db_column or field.name
        with schema_editor.connection.cursor() as cursor:
            columns = {
                info.name
                for info in schema_editor.connection.introspection.get_table_description(cursor, table)
            }
        if column_name in columns:
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    # Adds an optional, non-unique display name to evaluations.
    # Tolerates a pre-existing `name` column (e.g. partial deploy).
    dependencies = [
        ('aisc_backend', '0035_report_version'),
    ]

    operations = [
        AddFieldIfMissing(
            model_name='evaluation',
            name='name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
