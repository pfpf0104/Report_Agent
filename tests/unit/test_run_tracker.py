import pytest

from app.db.base import SessionLocal
from app.db.models.ingestion_run import IngestionRun
from app.ingestion.run_tracker import track_ingestion_run


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(IngestionRun).delete()
    session.commit()
    session.close()


def test_track_ingestion_run_success(db):
    with track_ingestion_run(db, "dart") as run:
        run.raw_archive_path = "data/raw_archive/dart/x.json"

    saved = db.query(IngestionRun).filter_by(source="dart").one()
    assert saved.status == "success"
    assert saved.finished_at is not None
    assert saved.raw_archive_path == "data/raw_archive/dart/x.json"


def test_track_ingestion_run_failure_records_error_and_reraises(db):
    with pytest.raises(ValueError):
        with track_ingestion_run(db, "bok"):
            raise ValueError("boom")

    saved = db.query(IngestionRun).filter_by(source="bok").one()
    assert saved.status == "failed"
    assert "boom" in saved.error_summary
    assert saved.finished_at is not None
