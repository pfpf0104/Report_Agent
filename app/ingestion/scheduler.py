"""정해진 주기로 인제스천 job을 자동 실행하는 스케줄러.

ingestion_router.py의 수동 트리거(POST /ingestion/trigger/{job_name})와 별개로,
Cloudflare Tunnel로 상시 노출되는 로컬 PC에서 사람 개입 없이 데이터가
갱신되도록 한다. 부동산 실거래(ingest_real_estate_deals)는 아직 미구현
(NotImplementedError)이라 스케줄에서 제외한다.

주기는 데이터가 실제로 바뀌는 빈도에 맞춘다:

  - 일간(07:30 KST): 시세·매크로 금리. 매 거래일 새 값이 나온다.
  - 주간(월 07:40 KST): DART 재무제표. reprt_code=11011(사업보고서)은 연 1회
    공시되므로 매일 조회하면 API 쿼터만 소모한다. 다만 신규 보고서가 뜨는
    시점을 너무 늦게 잡으면 안 되므로 주 1회로 둔다. 일간 job과 10분 어긋나게
    배치해 동시 실행을 피한다.

각 job은 이미 track_ingestion_run으로 성공/실패를 ingestion_run 테이블에
기록하므로, 여기서는 실패해도 다음 job 실행을 막지 않도록 개별적으로 감싸고
로깅만 추가한다.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.ingestion.jobs import (
    ingest_equity_prices,
    ingest_financial_statements,
    ingest_korean_equity_prices,
    ingest_macro_rates,
)

logger = logging.getLogger("app.ingestion.scheduler")

KST = ZoneInfo("Asia/Seoul")

_DAILY_JOBS = (
    ingest_macro_rates.run,
    ingest_equity_prices.run,
    ingest_korean_equity_prices.run,
)

# 사업보고서는 연 1회 공시 — 위 docstring의 주기 설계 근거 참고.
_WEEKLY_JOBS = (ingest_financial_statements.run,)


def _run_job_safely(job) -> None:
    try:
        job()
    except Exception:
        logger.exception("스케줄된 인제스천 job 실패: %s", job.__module__)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=KST)

    for job in _DAILY_JOBS:
        scheduler.add_job(
            _run_job_safely,
            trigger="cron",
            hour=7,
            minute=30,
            args=[job],
            id=job.__module__,
            misfire_grace_time=3600,
        )

    for job in _WEEKLY_JOBS:
        scheduler.add_job(
            _run_job_safely,
            trigger="cron",
            day_of_week="mon",
            hour=7,
            minute=40,
            args=[job],
            id=job.__module__,
            misfire_grace_time=3600,
        )

    return scheduler
