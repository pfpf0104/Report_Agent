"""add ingestion_run table

Revision ID: bb4138c52233
Revises: 33127d4b27e8
Create Date: 2026-07-31 20:02:42.903863

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb4138c52233'
down_revision: Union[str, None] = '33127d4b27e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 참고: autogenerate가 파티션 자식 테이블(fact_market_daily_2024 등)을 모델
    # 메타데이터에 없다는 이유로 삭제하려 했다 — 이전 마이그레이션에서 수동 DDL로
    # 만든 것들이라 SQLAlchemy가 모른다. ingestion_run 추가만 남기고 나머지는 제거했다.
    op.create_table(
        'ingestion_run',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_summary', sa.Text(), nullable=True),
        sa.Column('raw_archive_path', sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('ingestion_run')
