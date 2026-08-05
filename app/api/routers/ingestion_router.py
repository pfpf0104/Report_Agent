from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models.alert_log import AlertLog
from app.ingestion.quality import run_quality_gate
from app.ingestion.jobs import (
    backfill_equity_prices,
    backfill_financial_statements,
    backfill_global_rates,
    backfill_housing_indicators,
    backfill_korean_equity_prices,
    backfill_macro_indicators,
    backfill_macro_rates,
    ingest_equity_prices,
    ingest_financial_statements,
    ingest_global_rates,
    ingest_housing_indicators,
    ingest_korean_equity_prices,
    ingest_macro_indicators,
    ingest_macro_rates,
    ingest_micron_financials,
    ingest_real_estate_deals,
)

router = APIRouter()

# 값으로 `module.run` 함수 객체가 아니라 **모듈**을 담는다.
#
# 함수 객체를 담으면 임포트 시점에 참조가 고정돼, 테스트가 `patch.object(job_module,
# "run")`으로 대체해도 여기 저장된 원본이 그대로 실행된다. 실제로 그 상태에서
# 유닛 테스트가 BOK ECOS를 진짜로 호출하고 DB에 쓰고 있었다(샌드박스에서 프록시
# 403으로 드러남 — 네트워크가 열린 환경에서는 조용히 통과하며 API 쿼터를 먹는다).
# 모듈을 담고 호출 시점에 `.run`을 꺼내면 패치가 정상 동작한다.
_JOBS = {
    "equity_prices": ingest_equity_prices,
    "korean_equity_prices": ingest_korean_equity_prices,
    "macro_rates": ingest_macro_rates,
    "global_rates": ingest_global_rates,
    "macro_indicators": ingest_macro_indicators,
    "financial_statements": ingest_financial_statements,
    "micron_financials": ingest_micron_financials,
    "housing_indicators": ingest_housing_indicators,
    "real_estate_deals": ingest_real_estate_deals,
    # 5년 히스토리 백필(Phase 0-2). 매일 도는 스케줄러 대상이 아니라 일회성/
    # 재해복구용이라 별도 job 이름으로 등록한다 — 전부 재개 가능(idempotent)해
    # 여러 번 트리거해도 안전하다.
    "backfill_macro_rates": backfill_macro_rates,
    "backfill_global_rates": backfill_global_rates,
    "backfill_macro_indicators": backfill_macro_indicators,
    "backfill_equity_prices": backfill_equity_prices,
    "backfill_korean_equity_prices": backfill_korean_equity_prices,
    "backfill_financial_statements": backfill_financial_statements,
    "backfill_housing_indicators": backfill_housing_indicators,
}


@router.post("/trigger/{job_name}")
def trigger_job(job_name: str, background_tasks: BackgroundTasks) -> dict:
    module = _JOBS.get(job_name)
    if module is None:
        return {"error": f"unknown job: {job_name}", "known_jobs": list(_JOBS)}
    background_tasks.add_task(module.run)
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


@router.get("/alerts")
def list_alerts(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    """job 실패·품질게이트 오류 알림 이력(app/ingestion/alerting.py).

    텔레그램 전송이 실패해도(자격증명 없음, 네트워크 오류 등) 이 테이블에는
    항상 남는다 — telegram_sent로 실제 전송 여부를 구분해서 본다.
    """
    rows = (
        db.query(AlertLog)
        .order_by(AlertLog.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return {
        "alerts": [
            {
                "id": r.id,
                "category": r.category,
                "source": r.source,
                "severity": r.severity,
                "message": r.message,
                "telegram_sent": r.telegram_sent,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
