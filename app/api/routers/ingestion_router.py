from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.ingestion.quality import run_quality_gate
from app.ingestion.jobs import (
    backfill_equity_prices,
    backfill_financial_statements,
    backfill_korean_equity_prices,
    backfill_macro_rates,
    ingest_equity_prices,
    ingest_financial_statements,
    ingest_korean_equity_prices,
    ingest_macro_rates,
    ingest_real_estate_deals,
)

router = APIRouter()

_JOBS = {
    "equity_prices": ingest_equity_prices.run,
    "korean_equity_prices": ingest_korean_equity_prices.run,
    "macro_rates": ingest_macro_rates.run,
    "financial_statements": ingest_financial_statements.run,
    "real_estate_deals": ingest_real_estate_deals.run,
    # 5년 히스토리 백필(Phase 0-2). 매일 도는 스케줄러 대상이 아니라 일회성/
    # 재해복구용이라 별도 job 이름으로 등록한다 — 전부 재개 가능(idempotent)해
    # 여러 번 트리거해도 안전하다.
    "backfill_macro_rates": backfill_macro_rates.run,
    "backfill_equity_prices": backfill_equity_prices.run,
    "backfill_korean_equity_prices": backfill_korean_equity_prices.run,
    "backfill_financial_statements": backfill_financial_statements.run,
}


@router.post("/trigger/{job_name}")
def trigger_job(job_name: str, background_tasks: BackgroundTasks) -> dict:
    job = _JOBS.get(job_name)
    if job is None:
        return {"error": f"unknown job: {job_name}", "known_jobs": list(_JOBS)}
    background_tasks.add_task(job)
    return {"status": "triggered", "job": job_name}


@router.get("/quality")
def quality_gate(as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    """적재된 데이터의 품질을 점검한다(app/ingestion/quality.py).

    인제스천 직후 이 엔드포인트로 결과를 확인한다 — ingestion_run의 success는
    호출이 성공했다는 뜻일 뿐 숫자가 맞다는 보장이 아니다.
    """
    report = run_quality_gate(db, as_of=as_of or date.today())
    return {
        "as_of": report.as_of.isoformat(),
        "ok": report.ok,
        "summary": report.summary(),
        "errors": [str(i) for i in report.errors],
        "warnings": [str(i) for i in report.warnings],
    }
