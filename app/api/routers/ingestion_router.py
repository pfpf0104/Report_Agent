from fastapi import APIRouter, BackgroundTasks

from app.ingestion.jobs import (
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
}


@router.post("/trigger/{job_name}")
def trigger_job(job_name: str, background_tasks: BackgroundTasks) -> dict:
    job = _JOBS.get(job_name)
    if job is None:
        return {"error": f"unknown job: {job_name}", "known_jobs": list(_JOBS)}
    background_tasks.add_task(job)
    return {"status": "triggered", "job": job_name}
