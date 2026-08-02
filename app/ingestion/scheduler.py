"""정해진 주기로 인제스천 job을 자동 실행하는 스케줄러.

ingestion_router.py의 수동 트리거(POST /ingestion/trigger/{job_name})와 별개로,
Cloudflare Tunnel로 상시 노출되는 로컬 PC에서 사람 개입 없이 데이터가
갱신되도록 한다. 부동산 실거래(ingest_real_estate_deals)는 아직 미구현
(NotImplementedError)이라 스케줄에서 제외한다.

주기는 데이터가 실제로 바뀌는 빈도에 맞춘다:

  - 일간(07:30 KST): 시세·매크로 금리(한국·미국). 매 거래일 새 값이 나온다.
  - 일간(07:45 KST): 품질 게이트(app/ingestion/quality.py). 위 인제스천이
    끝난 뒤 실행해야 그날 새로 들어온 데이터가 검사 대상에 포함된다. 오류가
    있으면(단위 이상·스테일·결측) 알림을 보낸다 — 이전에는 사람이 GET
    /ingestion/quality를 수동 조회해야만 알 수 있었다(Phase 5-3).
  - 주간(월 07:40 KST): DART 재무제표. reprt_code=11011(사업보고서)은 연 1회
    공시되므로 매일 조회하면 API 쿼터만 소모한다. 다만 신규 보고서가 뜨는
    시점을 너무 늦게 잡으면 안 되므로 주 1회로 둔다. 일간 job과 10분 어긋나게
    배치해 동시 실행을 피한다.

각 job은 이미 track_ingestion_run으로 성공/실패를 ingestion_run 테이블에
기록하므로, 여기서는 실패해도 다음 job 실행을 막지 않도록 개별적으로 감싸고
실패 시 send_alert로 텔레그램 알림 + alert_log 기록을 추가한다(job 자체가
raise한 예외를 여기서 잡아 alert_log에 남기지 않으면, DB 기록은 ingestion_run
에만 남고 사람이 놓치기 쉽다).
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.base import SessionLocal
from app.ingestion.alerting import send_alert
from app.ingestion.jobs import (
    ingest_equity_prices,
    ingest_financial_statements,
    ingest_global_rates,
    ingest_korean_equity_prices,
    ingest_macro_rates,
)
from app.ingestion.quality import run_quality_gate

logger = logging.getLogger("app.ingestion.scheduler")

KST = ZoneInfo("Asia/Seoul")

_DAILY_JOBS = (
    ingest_macro_rates.run,
    ingest_equity_prices.run,
    ingest_korean_equity_prices.run,
    ingest_global_rates.run,
)

# 사업보고서는 연 1회 공시 — 위 docstring의 주기 설계 근거 참고.
_WEEKLY_JOBS = (ingest_financial_statements.run,)


def _run_job_safely(job) -> None:
    try:
        job()
    except Exception as exc:
        logger.exception("스케줄된 인제스천 job 실패: %s", job.__module__)
        db = SessionLocal()
        try:
            send_alert(
                db, category="job_failure", source=job.__module__, severity="error",
                message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            db.close()


def _run_quality_gate_safely() -> None:
    """일간 인제스천 뒤 품질 게이트를 돌리고, 오류가 있으면 알린다.

    게이트 자체가 실패(예외)하면 그것도 알린다 — "검사를 못 돌렸다"를 사람이
    "검사를 돌렸는데 이상 없었다"로 착각하면 안 된다.
    """
    from datetime import date

    db = SessionLocal()
    try:
        report = run_quality_gate(db, as_of=date.today())
        if not report.ok:
            send_alert(
                db, category="quality_gate", source="quality_gate", severity="error",
                message=report.summary() + "\n" + "\n".join(str(i) for i in report.errors),
            )
    except Exception as exc:
        logger.exception("품질 게이트 실행 실패")
        send_alert(
            db, category="quality_gate", source="quality_gate", severity="error",
            message=f"품질 게이트 실행 자체가 실패했다: {type(exc).__name__}: {exc}",
        )
    finally:
        db.close()


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

    scheduler.add_job(
        _run_quality_gate_safely,
        trigger="cron",
        hour=7,
        minute=45,
        id="quality_gate",
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
