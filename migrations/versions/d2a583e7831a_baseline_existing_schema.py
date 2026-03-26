"""baseline existing schema

Revision ID: d2a583e7831a
Revises: 39d69f3d871b
Create Date: 2025-10-16 18:44:30.323141

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2a583e7831a'
down_revision: Union[str, Sequence[str], None] = '39d69f3d871b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
