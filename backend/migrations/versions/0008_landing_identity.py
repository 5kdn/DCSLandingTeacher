"""Store pilot/airframe on the landing, and backfill from the approach track.

A landing's aircraft was read from the ``objects`` row, which is mutable and
shared: Tacview reuses an object's hex id within a recording and the ingest
matches on (flight_id, acmi_id), so a later object overwrites the name and
pilot of the row an earlier landing points at. Measured before this
migration: 124 landings displayed an airframe disagreeing with the one in
their own ``approach_track``, including 7 UH-1H landings shown as "AIM_120".

``approach_track.airframe`` is the value captured at detection time, so it is
the correct one and the backfill uses it. The pilot was never stored there
and cannot be recovered for old rows; those keep falling back to the object
row, which is no worse than before.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_landing_identity"
down_revision = "0007_import_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("landings", sa.Column("pilot", sa.String(128), nullable=True))
    op.add_column("landings", sa.Column("airframe", sa.String(128), nullable=True))

    # Backfill from the approach track, which recorded the airframe at
    # detection time. json_extract is SQLite-specific; this project ships on
    # SQLite only (see docs/architecture.md), and the guard keeps the
    # migration from failing anywhere else rather than pretending to work.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                """
                UPDATE landings
                   SET airframe = json_extract(approach_track, '$.airframe')
                 WHERE approach_track IS NOT NULL
                   AND json_extract(approach_track, '$.airframe') IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    op.drop_column("landings", "airframe")
    op.drop_column("landings", "pilot")
