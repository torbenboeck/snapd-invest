"""instrument saxo identity

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-16 10:00:00

Adds nullable saxo_uic / saxo_asset_type / saxo_currency_decimals columns
to `instruments`. Populated lazily by `ensure_saxo_instrument` (T-001-B)
on first use against Saxo SIM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.add_column(sa.Column("saxo_uic", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("saxo_asset_type", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("saxo_currency_decimals", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.drop_column("saxo_currency_decimals")
        batch.drop_column("saxo_asset_type")
        batch.drop_column("saxo_uic")
