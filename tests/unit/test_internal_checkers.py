import httpx
import pytest
import respx

import app.ingestion.connectors.dart_client as dart_client
from app.extraction.number_extractor import ExtractedValueCandidate
from app.validation.checkers.internal_checkers import DartCapitalTotalChecker


@pytest.fixture(autouse=True)
def _reset_corp_code_cache():
    dart_client._CORP_CODE_CACHE = None
    yield
    dart_client._CORP_CODE_CACHE = None


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(dart_client.settings, "dart_api_key", "test-key")


def _candidate(label: str, value: float) -> ExtractedValueCandidate:
    return ExtractedValueCandidate(
        label=label, value=value, unit=None, page_number=1, context_snippet="", extraction_confidence=0.9
    )


async def test_non_capital_total_label_is_not_applicable():
    checker = DartCapitalTotalChecker("삼성전자", 2023)
    result = await checker.check(_candidate("매출액", 1000.0))
    assert result.status == "not_applicable"


@respx.mock
async def test_matching_value_within_tolerance_is_verified(_corp_code_zip=None):
    import io
    import zipfile

    xml = (
        "<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name></list></result>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)

    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "자본총계", "thstrm_amount": "300,000,000"}],
            },
        )
    )

    checker = DartCapitalTotalChecker("삼성전자", 2023)
    result = await checker.check(_candidate("자본총계", 300_000_000.0))

    assert result.status == "verified"
    assert result.matched_value == 300_000_000.0


@respx.mock
async def test_diverging_value_is_mismatch():
    import io
    import zipfile

    xml = (
        "<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name></list></result>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)

    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "자본총계", "thstrm_amount": "300,000,000"}],
            },
        )
    )

    checker = DartCapitalTotalChecker("삼성전자", 2023)
    result = await checker.check(_candidate("자본총계", 100_000_000.0))

    assert result.status == "mismatch"


@respx.mock
async def test_unknown_company_yields_check_failed():
    import io
    import zipfile

    xml = "<result></result>".encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)

    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(return_value=httpx.Response(200, content=buf.getvalue()))

    checker = DartCapitalTotalChecker("존재하지않는회사", 2023)
    result = await checker.check(_candidate("자본총계", 300_000_000.0))

    assert result.status == "check_failed"
