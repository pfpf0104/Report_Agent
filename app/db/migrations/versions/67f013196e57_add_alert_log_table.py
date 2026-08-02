"""add alert_log table

Revision ID: 67f013196e57
Revises: af13c95018cc
Create Date: 2026-08-02 17:00:34.761380

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67f013196e57'
down_revision: Union[str, None] = 'af13c95018cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # autogenerate가 파티션 자식 테이블(fact_market_daily_2024 등)을 모델
    # 메타데이터에 없다는 이유로 삭제하려 했다 — bb4138c52233과 동일한 이유로
    # (수동 DDL로 만든 것들이라 SQLAlchemy가 모른다) alert_log 추가만 남긴다.
    op.create_table('alert_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('telegram_sent', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('alert_log')
