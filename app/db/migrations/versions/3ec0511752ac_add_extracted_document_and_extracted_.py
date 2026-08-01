"""add extracted_document and extracted_value tables

Revision ID: 3ec0511752ac
Revises: bb4138c52233
Create Date: 2026-08-01 17:11:53.646020

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3ec0511752ac'
down_revision: Union[str, None] = 'bb4138c52233'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 참고: autogenerate가 파티션 자식 테이블(fact_market_daily_2024 등)을 모델
    # 메타데이터에 없다는 이유로 삭제하려 했다 — bb4138c52233과 동일한 이유로
    # 새 테이블 생성만 남기고 나머지는 제거했다.
    op.create_table('extracted_document',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('filename', sa.String(length=256), nullable=False),
    sa.Column('file_hash', sa.String(length=64), nullable=False),
    sa.Column('storage_path', sa.String(length=512), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('extraction_method', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_hash')
    )
    op.create_table('extracted_value',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=256), nullable=False),
    sa.Column('value', sa.Numeric(precision=24, scale=4), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('context_snippet', sa.Text(), nullable=True),
    sa.Column('extraction_confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('verification_status', sa.String(length=16), nullable=False),
    sa.Column('verification_details', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['extracted_document.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('extracted_value')
    op.drop_table('extracted_document')
