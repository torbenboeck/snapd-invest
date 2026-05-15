"""account saxo identity

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-15 10:00:00

Adds nullable saxo_client_key / saxo_account_key / saxo_account_id columns
to `accounts`. T-001-A only sets `saxo_account_id` (human label) at create-
account time; T-001-B will populate the opaque keys from
/port/v1/accounts/me when it implements order placement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("saxo_client_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("saxo_account_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("saxo_account_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("saxo_account_id")
        batch.drop_column("saxo_account_key")
        batch.drop_column("saxo_client_key")
