"""a stored colour for the two split axes

Revision ID: 0037
Revises: 0036

A split value's colour, so a value is the same colour in every chart it appears
in (spec §10.3). Both split axes get one: ``facet_values`` for a facet split and
``senders`` for ``split=sender``, which is a real column and so a real axis.

**Nullable, and null is the normal state.** A null colour means "derive the
palette slot from the key", which is what makes a legend stably and accessibly
coloured before anyone has chosen anything — so this migration invents no data.
A NOT NULL column would have to, and would then own the palette: changing it
later would need a second data migration, and a value created afterwards would
need a colour picked at insert.

The CHECK is written out because **nothing else would enforce the format**. A
plain ``String`` accepts any text, and the lesson 0034 and 0036 already paid for
is that a declarative type does not create a constraint on its own
(``sa.Enum(native_enum=False)`` creates none at all). Without it the column
takes ``rebeccapurple``, ``#1f7`` or a sentence, and the first anyone knows is a
legend that renders nothing.

The column is deliberately wider than a hex colour needs (``String(32)``, not
``String(7)``): a tightly-sized column would itself refuse an over-length
value, but as a Postgres length error, not an ``IntegrityError`` — a second,
uncoordinated enforcer with the wrong failure mode. The CHECK is the only thing
that judges *format*; the column width exists only so nothing absurd gets
stored.

``name=`` carries the convention-relative suffix only, on both sides of the
migration. Alembic's ``"ck"`` template is
``"ck_%(table_name)s_%(constraint_name)s"`` and substitutes an explicit name
*into* the token, so a name already carrying the prefix is prefixed twice in
the live database (the note in 0036). This is not only a ``create_check_constraint``
gotcha: ``op.drop_constraint(..., type_="check")`` runs the given name through
the same naming convention (``Base.metadata`` carries one — models.py), so the
downgrade must also pass ``"colour_hex"``, not ``"ck_<table>_colour_hex"``.



Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Six-digit hex with a leading hash, either case. Anchored at both ends, so a
#: valid colour with trailing text is refused rather than truncated.
_HEX = "colour ~ '^#[0-9a-fA-F]{6}$'"


def upgrade() -> None:
    for table in ("facet_values", "senders"):
        op.add_column(table, sa.Column("colour", sa.String(length=32), nullable=True))
        op.create_check_constraint("colour_hex", table, _HEX)


def downgrade() -> None:
    for table in ("facet_values", "senders"):
        # Same pitfall as the create side: a naming_convention is active on
        # Base.metadata (models.py), so a plain string given to a "check"-typed
        # drop_constraint is *also* run through the "ck" template. Passing the
        # already-prefixed name here produces
        # ck_<table>_ck_<table>_colour_hex against the live database — pass the
        # convention-relative suffix only, exactly as create_check_constraint
        # does above.
        op.drop_constraint("colour_hex", table, type_="check")
        op.drop_column(table, "colour")
