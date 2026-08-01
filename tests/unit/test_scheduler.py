from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.ingestion.scheduler import create_scheduler


def test_create_scheduler_registers_all_ingestion_jobs_except_unimplemented():
    scheduler = create_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}

    assert job_ids == {
        "app.ingestion.jobs.ingest_macro_rates",
        "app.ingestion.jobs.ingest_equity_prices",
        "app.ingestion.jobs.ingest_korean_equity_prices",
    }
    # 부동산 실거래 job은 NotImplementedError를 던지는 스텁이라 스케줄에서 제외된다.
    assert "app.ingestion.jobs.ingest_real_estate_deals" not in job_ids


def test_create_scheduler_uses_daily_cron_in_kst():
    scheduler = create_scheduler()
    for job in scheduler.get_jobs():
        assert isinstance(job.trigger, CronTrigger)
        assert str(job.trigger.timezone) == "Asia/Seoul"


async def test_scheduler_starts_and_stops_cleanly():
    # AsyncIOScheduler.start()는 실행 중인 이벤트 루프를 요구한다(app/main.py의
    # FastAPI startup 이벤트 안에서 호출되는 것과 동일한 조건).
    scheduler = create_scheduler()
    assert isinstance(scheduler, AsyncIOScheduler)
    scheduler.start()
    assert scheduler.running
    scheduler.shutdown(wait=False)  # main.py의 shutdown 훅과 동일 — 예외 없이 반환되면 충분


def test_run_job_safely_swallows_exceptions_and_continues():
    from app.ingestion.scheduler import _run_job_safely

    def failing_job():
        raise RuntimeError("boom")

    _run_job_safely(failing_job)  # 예외를 삼키고 조용히 로깅만 해야 한다(재발생 안 함)
