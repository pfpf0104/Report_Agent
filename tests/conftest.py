import pytest

import app.ingestion.connectors.http_utils as http_utils


@pytest.fixture(autouse=True)
def _isolate_raw_archive_dir(tmp_path, monkeypatch):
    """테스트가 실제 프로젝트의 data/raw_archive/에 파일을 남기지 않도록 격리한다."""
    monkeypatch.setattr(http_utils, "RAW_ARCHIVE_DIR", tmp_path / "raw_archive")
