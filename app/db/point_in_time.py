"""Point-in-time 조회 헬퍼 — look-ahead bias를 구조적으로 막는다.

fact 테이블의 각 행은 두 개의 시점을 갖는다:

  - 사건 시점 (trade_date / fiscal_year+quarter / deal_date): 무슨 일이 언제 일어났나
  - 취득 시점 (knowledge_date): 그 사건을 우리가 언제부터 알 수 있었나

백테스트나 과거 시점 리포트를 만들 때 사건 시점만 필터링하면, 그 당시에는
아직 공시되지 않았던 데이터를 쓰게 된다(look-ahead bias). 예를 들어 삼성전자
2025 사업보고서는 fiscal_year=2025지만 실제 공시는 2026년 3월경이므로,
2025-06-30 기준 리포트가 이 BPS를 쓰면 미래를 당겨쓰는 것이다.

이 모듈의 함수를 거치면 그런 행은 자동으로 걸러진다. **fact 테이블을 조회하는
모든 코드는 이 헬퍼를 쓴다** — 직접 filter를 짜면 knowledge_date를 빠뜨리기 쉽다.

사용 예:
    q = visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
    q = q.filter_by(asset_id=asset_id).order_by(FactMarketDaily.trade_date.desc())
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Query


def visible_as_of(query: Query, model, as_of: date | None) -> Query:
    """as_of 시점에 알 수 있었던 행만 남긴다.

    as_of=None이면 필터를 걸지 않는다 — "현재 시점에서 아는 전부"를 뜻하며,
    실시간 대시보드처럼 과거 재현이 목적이 아닌 조회에서만 쓴다. 백테스트나
    과거 기준일 리포트에서는 절대 None을 넘기면 안 된다.
    """
    if as_of is None:
        return query
    return query.filter(model.knowledge_date <= as_of)
