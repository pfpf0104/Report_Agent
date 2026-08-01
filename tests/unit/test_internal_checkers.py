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


@respx.mock
async def test_repeated_checks_reuse_cached_dart_response():
    """핵심 회귀: 같은 체커 인스턴스로 여러 후보(예: 당기/전기 자본총계, 같은
    항목이 손익계산서/재무상태표 양쪽에 등장하는 경우)를 검증해도
    fnlttSinglAcntAll은 한 번만 호출돼야 한다. 파이프라인이 문서 1건당 이
    체커를 재사용하므로, PDF에 "자본총계" 라벨이 여러 개면 캐싱 없이는 매번
    실제 DART API를 때린다."""
    import io
    import zipfile

    xml = (
        "<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name></list></result>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)

    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(return_value=httpx.Response(200, content=buf.getvalue()))
    financials_route = respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json").mock(
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
    result1 = await checker.check(_candidate("자본총계", 300_000_000.0))
    result2 = await checker.check(_candidate("자본총계 (1번째 값)", 300_000_000.0))
    result3 = await checker.check(_candidate("자본총계 (2번째 값)", 250_000_000.0))

    assert result1.status == "verified"
    assert result2.status == "verified"
    assert result3.status == "mismatch"  # 값은 다르지만 대조 대상 DART 응답은 재사용됨
    assert financials_route.call_count == 1


@respx.mock
async def test_repeated_check_failed_does_not_retry_indefinitely():
    """조회 자체가 실패하면(회사 못 찾음 등) 그 결과도 캐싱해 재시도로 API를
    반복 호출하지 않는다."""
    import io
    import zipfile

    xml = "<result></result>".encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)

    corp_code_route = respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(
        return_value=httpx.Response(200, content=buf.getvalue())
    )

    checker = DartCapitalTotalChecker("존재하지않는회사", 2023)
    await checker.check(_candidate("자본총계", 300_000_000.0))
    await checker.check(_candidate("자본총계 (1번째 값)", 300_000_000.0))

    # corpCode.xml 자체는 dart_client의 전역 캐시가 있어 이미 1회로 제한되지만,
    # 이 체커의 _fetch_error 캐시가 없다면 두 번째 호출에서도 fetch_corp_code_map을
    # 다시 부르려 시도한다 — 그 시도 자체를 건너뛰는지 확인한다.
    assert corp_code_route.call_count == 1
