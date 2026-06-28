"""series membership overrides + fx rates

Adds two tables backing manual series-membership editing (W8):

- ``series_membership_overrides`` — durable ``pin``/``exclude`` edits to a
  recurring ``(sender_id, kind_id, currency)`` series' computed membership.
- ``fx_rates`` — a reference FX snapshot (base = USD) so a document pinned into
  a series whose currency differs can be converted, date-aware, into the series
  currency.

The FX seed is a *researched approximate* yearly snapshot (Jan 1, 2015-2026)
for the currencies most likely to appear in the corpus. Values are
end-of-period-ish averages of 1 unit in USD and are intended as a sensible
default; add rows to refine accuracy. USD is 1.0 by definition and handled in
code, so it is not stored.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-28 16:30:00.000000

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# currency -> {year: value of 1 unit in USD}. Approximate yearly reference rates.
_FX_SNAPSHOT: dict[str, dict[int, str]] = {
    "EUR": {
        2015: "1.110",
        2016: "1.107",
        2017: "1.130",
        2018: "1.181",
        2019: "1.120",
        2020: "1.142",
        2021: "1.183",
        2022: "1.053",
        2023: "1.082",
        2024: "1.082",
        2025: "1.080",
        2026: "1.090",
    },
    "GBP": {
        2015: "1.529",
        2016: "1.355",
        2017: "1.288",
        2018: "1.335",
        2019: "1.277",
        2020: "1.284",
        2021: "1.376",
        2022: "1.237",
        2023: "1.244",
        2024: "1.272",
        2025: "1.270",
        2026: "1.280",
    },
    "CHF": {
        2015: "1.039",
        2016: "1.016",
        2017: "1.016",
        2018: "1.023",
        2019: "1.006",
        2020: "1.065",
        2021: "1.094",
        2022: "1.047",
        2023: "1.113",
        2024: "1.133",
        2025: "1.120",
        2026: "1.120",
    },
    "JPY": {
        2015: "0.00826",
        2016: "0.00920",
        2017: "0.00888",
        2018: "0.00905",
        2019: "0.00917",
        2020: "0.00937",
        2021: "0.00909",
        2022: "0.00760",
        2023: "0.00713",
        2024: "0.00650",
        2025: "0.00660",
        2026: "0.00670",
    },
    "CAD": {
        2015: "0.783",
        2016: "0.755",
        2017: "0.771",
        2018: "0.772",
        2019: "0.754",
        2020: "0.746",
        2021: "0.798",
        2022: "0.768",
        2023: "0.741",
        2024: "0.731",
        2025: "0.720",
        2026: "0.730",
    },
    "AUD": {
        2015: "0.752",
        2016: "0.744",
        2017: "0.767",
        2018: "0.748",
        2019: "0.695",
        2020: "0.690",
        2021: "0.752",
        2022: "0.694",
        2023: "0.664",
        2024: "0.660",
        2025: "0.650",
        2026: "0.660",
    },
    "SEK": {
        2015: "0.119",
        2016: "0.117",
        2017: "0.117",
        2018: "0.115",
        2019: "0.106",
        2020: "0.109",
        2021: "0.116",
        2022: "0.099",
        2023: "0.094",
        2024: "0.095",
        2025: "0.095",
        2026: "0.096",
    },
    "NOK": {
        2015: "0.124",
        2016: "0.119",
        2017: "0.121",
        2018: "0.123",
        2019: "0.114",
        2020: "0.106",
        2021: "0.116",
        2022: "0.102",
        2023: "0.095",
        2024: "0.092",
        2025: "0.093",
        2026: "0.094",
    },
    "DKK": {
        2015: "0.149",
        2016: "0.149",
        2017: "0.151",
        2018: "0.158",
        2019: "0.150",
        2020: "0.153",
        2021: "0.159",
        2022: "0.141",
        2023: "0.145",
        2024: "0.145",
        2025: "0.145",
        2026: "0.146",
    },
}


def upgrade() -> None:
    op.create_table(
        "series_membership_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "pin",
                "exclude",
                name="series_override_action",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["senders.id"],
            name=op.f("fk_series_membership_overrides_sender_id_senders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["kinds.id"],
            name=op.f("fk_series_membership_overrides_kind_id_kinds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_series_membership_overrides_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_membership_overrides")),
        sa.UniqueConstraint(
            "sender_id",
            "kind_id",
            "currency",
            "document_id",
            name="series_membership_overrides_series_document",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_series_membership_overrides_sender_id"),
        "series_membership_overrides",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_membership_overrides_kind_id"),
        "series_membership_overrides",
        ["kind_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_series_membership_overrides_document_id"),
        "series_membership_overrides",
        ["document_id"],
        unique=False,
    )

    fx_rates = op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("rate_to_base", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rates")),
        sa.UniqueConstraint("currency", "as_of", name="fx_rates_currency_as_of"),
    )
    op.create_index(op.f("ix_fx_rates_currency"), "fx_rates", ["currency"], unique=False)

    op.bulk_insert(
        fx_rates,
        [
            {"currency": currency, "as_of": date(year, 1, 1), "rate_to_base": rate}
            for currency, by_year in _FX_SNAPSHOT.items()
            for year, rate in by_year.items()
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fx_rates_currency"), table_name="fx_rates")
    op.drop_table("fx_rates")
    op.drop_index(
        op.f("ix_series_membership_overrides_document_id"),
        table_name="series_membership_overrides",
    )
    op.drop_index(
        op.f("ix_series_membership_overrides_kind_id"),
        table_name="series_membership_overrides",
    )
    op.drop_index(
        op.f("ix_series_membership_overrides_sender_id"),
        table_name="series_membership_overrides",
    )
    op.drop_table("series_membership_overrides")
