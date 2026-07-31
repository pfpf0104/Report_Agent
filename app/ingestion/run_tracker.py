"""인제스천 잡의 성공/실패 상태를 ingestion_run 테이블에 기록한다."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.ingestion_run import IngestionRun


@contextmanager
def track_ingestion_run(db: Session, source: str):
    run = IngestionRun(source=source, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        yield run
        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        run.error_summary = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
