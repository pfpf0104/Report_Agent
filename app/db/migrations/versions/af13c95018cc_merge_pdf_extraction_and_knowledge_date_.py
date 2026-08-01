"""merge PDF extraction and knowledge_date branches

Revision ID: af13c95018cc
Revises: 3ec0511752ac, c81f3a5e2d47
Create Date: 2026-08-01 17:56:15.283155

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af13c95018cc'
down_revision: Union[str, None] = ('3ec0511752ac', 'c81f3a5e2d47')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
