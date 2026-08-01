"""fact 테이블에 knowledge_date(정보 취득시점) 추가

Point-in-time 정합성의 기반. 각 fact 행이 "언제부터 알 수 있었던 정보인가"를
기록해, as_of 시점 조회 시 그때 알 수 없었던 데이터가 섞이는 look-ahead bias를
구조적으로 막는다.

기존 컬럼과의 구분:
  - trade_date / fiscal_year+quarter / deal_date = 사건이 "발생한" 시점
  - knowledge_date                              = 그 사건을 "알게 된" 시점

예: 삼성전자 2025 사업보고서는 fiscal_year=2025지만 실제 공시는 2026년 3월경이다.
knowledge_date 없이 2025-06-30 기준으로 이 BPS를 쓰면 미래 정보를 당겨쓰는 셈이다.

이 마이그레이션은 손으로 작성했다 — alembic autogenerate는 SQLAlchemy가 모르는
파티션 자식 테이블을 drop하려 들기 때문에 이 프로젝트에서는 쓰지 않는다.
파티션 부모 테이블에 ADD COLUMN하면 Postgres가 자식 파티션까지 자동 전파한다.

Revision ID: c81f3a5e2d47
Revises: bb4138c52233
"""
from alembic import op
import sqlalchemy as sa

revision = "c81f3a5e2d47"
down_revision = "bb4138c52233"
branch_labels = None
depends_on = None

# (테이블, knowledge_date를 채울 기존 컬럼) — 백필 기본값 산출용.
_TABLES = (
    ("fact_market_daily", "trade_date"),
    ("fact_real_estate_deal", "deal_date"),
)


def upgrade() -> None:
    # 1) nullable로 먼저 추가한다. NOT NULL을 바로 걸면 기존 행이 있는 환경에서
    #    실패하므로, 추가 → 백필 → NOT NULL 승격의 3단계를 거친다.
    for table, _ in _TABLES:
        op.add_column(table, sa.Column("knowledge_date", sa.Date(), nullable=True))
    op.add_column("fact_financial_quarterly", sa.Column("knowledge_date", sa.Date(), nullable=True))

    # 2) 기존 행 백필. 날짜형 사건 컬럼이 있는 테이블은 그 값을 그대로 쓴다
    #    (일별 시세·실거래는 사건일에 알 수 있었다고 보는 것이 합리적 근사).
    for table, event_column in _TABLES:
        op.execute(f"UPDATE {table} SET knowledge_date = {event_column} WHERE knowledge_date IS NULL")

    #    재무제표는 사건(회계연도)과 공시시점의 간극이 커서 단순 근사가 위험하다.
    #    회계연도 말일로부터 90일 뒤를 보수적 추정치로 쓴다(사업보고서 법정 제출기한).
    op.execute(
        """
        UPDATE fact_financial_quarterly
        SET knowledge_date = (
            make_date(fiscal_year, fiscal_quarter * 3, 1)
            + INTERVAL '1 month' - INTERVAL '1 day'
            + INTERVAL '90 days'
        )::date
        WHERE knowledge_date IS NULL
        """
    )

    # 3) NOT NULL 승격. 이후 모든 insert는 knowledge_date를 명시해야 한다 —
    #    빠뜨리면 조용히 잘못된 값이 들어가는 대신 즉시 에러가 나도록 하는 것이 목적이다.
    for table, _ in _TABLES:
        op.alter_column(table, "knowledge_date", nullable=False)
    op.alter_column("fact_financial_quarterly", "knowledge_date", nullable=False)

    # 4) as_of 조회가 항상 이 컬럼으로 필터링하므로 인덱스를 건다.
    op.create_index("ix_fact_market_daily_knowledge_date", "fact_market_daily", ["knowledge_date"])
    op.create_index(
        "ix_fact_financial_quarterly_knowledge_date", "fact_financial_quarterly", ["knowledge_date"]
    )
    op.create_index(
        "ix_fact_real_estate_deal_knowledge_date", "fact_real_estate_deal", ["knowledge_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_fact_real_estate_deal_knowledge_date", table_name="fact_real_estate_deal")
    op.drop_index("ix_fact_financial_quarterly_knowledge_date", table_name="fact_financial_quarterly")
    op.drop_index("ix_fact_market_daily_knowledge_date", table_name="fact_market_daily")

    op.drop_column("fact_real_estate_deal", "knowledge_date")
    op.drop_column("fact_financial_quarterly", "knowledge_date")
    op.drop_column("fact_market_daily", "knowledge_date")
