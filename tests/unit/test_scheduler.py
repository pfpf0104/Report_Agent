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
        "app.ingestion.jobs.ingest_global_rates",
        "app.ingestion.jobs.ingest_financial_statements",
        "app.ingestion.jobs.ingest_macro_indicators",
        "quality_gate",
    }
    # 부동산 실거래 job은 NotImplementedError를 던지는 스텁이라 스케줄에서 제외된다.
    assert "app.ingestion.jobs.ingest_real_estate_deals" not in job_ids


def test_quality_gate_runs_after_daily_ingestion_jobs():
    """품질 게이트(07:45)는 일간 인제스천(07:30)보다 늦게 돌아야 그날 새로
    들어온 데이터가 검사 대상에 포함된다 — Phase 5-3."""
    scheduler = create_scheduler()
    job = scheduler.get_job("quality_gate")

    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "*"
    assert fields["hour"] == "7"
    assert fields["minute"] == "45"


def test_financial_statements_is_scheduled_weekly_not_daily():
    """회귀 테스트: 이 job이 스케줄러에 아예 빠져 있어 DART BPS가 자동 적재되지
    않던 버그가 있었다(밸류에이션 리포트가 운영 중 항상 폴백 경로로 떨어짐).
    사업보고서는 연 1회 공시라 일간이 아니라 주간이어야 한다."""
    scheduler = create_scheduler()
    job = scheduler.get_job("app.ingestion.jobs.ingest_financial_statements")

    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "mon"
    assert fields["hour"] == "7"
    # 일간 job(07:30)과 겹치지 않게 어긋나 있어야 한다
    assert fields["minute"] == "40"


def test_macro_indicators_is_scheduled_weekly_offset_from_financial_statements():
    """거시경제 지표는 월간·분기 발표라 DART와 같은 근거로 주간이어야 한다
    (scheduler.py docstring 참고). 07:40(DART)과 겹치지 않게 07:50이어야 한다."""
    scheduler = create_scheduler()
    job = scheduler.get_job("app.ingestion.jobs.ingest_macro_indicators")

    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "mon"
    assert fields["hour"] == "7"
    assert fields["minute"] == "50"


def test_daily_jobs_run_every_day_at_0730():
    scheduler = create_scheduler()
    for job_id in (
        "app.ingestion.jobs.ingest_macro_rates",
        "app.ingestion.jobs.ingest_equity_prices",
        "app.ingestion.jobs.ingest_korean_equity_prices",
        "app.ingestion.jobs.ingest_global_rates",
    ):
        fields = {f.name: str(f) for f in scheduler.get_job(job_id).trigger.fields}
        assert fields["day_of_week"] == "*"
        assert fields["hour"] == "7"
        assert fields["minute"] == "30"


def test_create_scheduler_uses_cron_in_kst():
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


def test_run_job_safely_sends_alert_on_failure(monkeypatch):
    """job이 실패하면 alert_log에 남아야 한다 — ingestion_run에만 남으면
    사람이 GET /ingestion/alerts로 확인할 방법이 없다."""
    import app.ingestion.scheduler as scheduler_module

    calls = []
    monkeypatch.setattr(
        scheduler_module, "send_alert",
        lambda db, category, source, severity, message: calls.append(
            (category, source, severity, message)
        ),
    )

    def failing_job():
        raise RuntimeError("boom")
    failing_job.__module__ = "test_module"

    scheduler_module._run_job_safely(failing_job)

    assert len(calls) == 1
    category, source, severity, message = calls[0]
    assert category == "job_failure"
    assert source == "test_module"
    assert severity == "error"
    assert "boom" in message


def test_run_quality_gate_safely_sends_alert_when_gate_reports_errors(monkeypatch):
    import app.ingestion.scheduler as scheduler_module
    from app.ingestion.quality import ERROR, QualityIssue, QualityReport

    calls = []
    monkeypatch.setattr(
        scheduler_module, "send_alert",
        lambda db, category, source, severity, message: calls.append(
            (category, source, severity, message)
        ),
    )
    bad_report = QualityReport(
        as_of=__import__("datetime").date.today(),
        issues=[QualityIssue(ERROR, "value_range", "KTB1Y", "단위 오류 의심")],
    )
    monkeypatch.setattr(scheduler_module, "run_quality_gate", lambda db, as_of: bad_report)

    scheduler_module._run_quality_gate_safely()

    assert len(calls) == 1
    category, source, severity, message = calls[0]
    assert category == "quality_gate"
    assert severity == "error"
    assert "KTB1Y" in message


def test_run_quality_gate_safely_sends_no_alert_when_gate_passes(monkeypatch):
    import app.ingestion.scheduler as scheduler_module
    from app.ingestion.quality import QualityReport

    calls = []
    monkeypatch.setattr(
        scheduler_module, "send_alert",
        lambda db, category, source, severity, message: calls.append(
            (category, source, severity, message)
        ),
    )
    clean_report = QualityReport(as_of=__import__("datetime").date.today(), issues=[])
    monkeypatch.setattr(scheduler_module, "run_quality_gate", lambda db, as_of: clean_report)

    scheduler_module._run_quality_gate_safely()

    assert calls == []


def test_run_quality_gate_safely_alerts_when_gate_itself_raises(monkeypatch):
    """게이트 실행 자체가 예외를 던지면(DB 접속 실패 등) 그것도 알려야 한다 —
    "검사를 못 돌렸다"를 "이상 없었다"로 착각하면 안 된다."""
    import app.ingestion.scheduler as scheduler_module

    calls = []
    monkeypatch.setattr(
        scheduler_module, "send_alert",
        lambda db, category, source, severity, message: calls.append(
            (category, source, severity, message)
        ),
    )

    def _raise(db, as_of):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(scheduler_module, "run_quality_gate", _raise)

    scheduler_module._run_quality_gate_safely()

    assert len(calls) == 1
    assert "db unreachable" in calls[0][3]
