"""DART/BOK ECOS/FRED/FMP 실제 연결을 로컬 PC에서 검증하는 스크립트.

이 원격 세션은 아웃바운드 네트워크 정책상 이 4개 API 도메인에 직접 접근할 수
없어(직접 curl과 WebFetch 둘 다 403 확인), 실제 응답 기반 검증은 사용자의
로컬 PC에서 실행해야 한다. 이 스크립트가 그 역할을 한다.

실행 방법 (프로젝트 루트에서):
    python -m pip install -r requirements.txt
    python scripts/verify_external_apis.py

Windows PowerShell + venv를 쓴다면:
    .venv\\Scripts\\python.exe -m pip install -r requirements.txt
    .venv\\Scripts\\python.exe scripts\\verify_external_apis.py

각 API는 비용/호출 한도를 최소화한 단일 요청만 보낸다(GET 전용, 데이터 변경 없음).
API 키는 .env.local에서만 읽고, 값/헤더/응답 전문은 절대 출력하지 않는다 —
성공 여부·HTTP 상태 코드·스키마 검증 결과·소요 시간만 표로 보여준다.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402

USER_AGENT = "ReportAgent/0.1 (+https://github.com/pfpf0104/Report_Agent; verify-script)"
TIMEOUT = 10.0
MAX_RETRIES = 2


@dataclass
class VerifyResult:
    api: str
    ok: bool
    http_status: int | None
    schema_ok: bool
    elapsed_ms: float
    detail: str


def _get_with_retry(url: str, params: dict) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                return client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.0 * attempt)
    assert last_exc is not None
    raise last_exc


def verify_fred() -> VerifyResult:
    api = "FRED"
    if not settings.fred_api_key:
        return VerifyResult(api, False, None, False, 0.0, "REPORT_AGENT_FRED_API_KEY 미설정")

    start = time.perf_counter()
    try:
        resp = _get_with_retry(
            "https://api.stlouisfed.org/fred/series/observations",
            {
                "series_id": "DGS10",  # 10년 미국 국채금리, 단일 series
                "api_key": settings.fred_api_key,
                "file_type": "json",
                "limit": 1,  # 최소 호출
                "sort_order": "desc",
            },
        )
        elapsed = (time.perf_counter() - start) * 1000
        if resp.status_code != 200:
            return VerifyResult(api, False, resp.status_code, False, elapsed, f"HTTP {resp.status_code}")
        payload = resp.json()
        if "error_message" in payload:
            return VerifyResult(api, False, resp.status_code, False, elapsed, f"API 오류: {payload['error_message']}")
        obs = payload.get("observations", [])
        schema_ok = bool(obs) and "date" in obs[0] and "value" in obs[0]
        try:
            float(obs[0]["value"]) if obs and obs[0]["value"] != "." else None
        except (ValueError, KeyError):
            schema_ok = False
        return VerifyResult(api, schema_ok, resp.status_code, schema_ok, elapsed, "OK" if schema_ok else "필드 파싱 실패")
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return VerifyResult(api, False, None, False, elapsed, f"{type(exc).__name__}")


def verify_bok() -> VerifyResult:
    api = "BOK ECOS"
    if not settings.bok_api_key:
        return VerifyResult(api, False, None, False, 0.0, "REPORT_AGENT_BOK_API_KEY 미설정")

    start = time.perf_counter()
    try:
        # 단일 통계표(722Y001=시장금리), 단일 기간(최근 1개월), 국고채(3년)=0101000
        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{settings.bok_api_key}/json/kr/"
            "1/5/722Y001/D/20260701/20260731/0101000"
        )
        resp = _get_with_retry(url, {})
        elapsed = (time.perf_counter() - start) * 1000
        if resp.status_code != 200:
            return VerifyResult(api, False, resp.status_code, False, elapsed, f"HTTP {resp.status_code}")
        payload = resp.json()
        if "RESULT" in payload:
            result = payload["RESULT"]
            return VerifyResult(
                api, False, resp.status_code, False, elapsed,
                f"API 오류: {result.get('CODE')} {result.get('MESSAGE')}",
            )
        rows = payload.get("StatisticSearch", {}).get("row", [])
        schema_ok = bool(rows) and "TIME" in rows[0] and "DATA_VALUE" in rows[0]
        if schema_ok:
            try:
                float(rows[0]["DATA_VALUE"])
            except (ValueError, KeyError):
                schema_ok = False
        return VerifyResult(api, schema_ok, resp.status_code, schema_ok, elapsed, "OK" if schema_ok else "필드 파싱 실패")
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return VerifyResult(api, False, None, False, elapsed, f"{type(exc).__name__}")


def verify_dart() -> VerifyResult:
    api = "DART"
    if not settings.dart_api_key:
        return VerifyResult(api, False, None, False, 0.0, "REPORT_AGENT_DART_API_KEY 미설정")

    start = time.perf_counter()
    try:
        # 삼성전자 단일 corp_code로 회사개황 조회(가장 가벼운 엔드포인트)
        resp = _get_with_retry(
            "https://opendart.fss.or.kr/api/company.json",
            {"crtfc_key": settings.dart_api_key, "corp_code": "00126380"},
        )
        elapsed = (time.perf_counter() - start) * 1000
        if resp.status_code != 200:
            return VerifyResult(api, False, resp.status_code, False, elapsed, f"HTTP {resp.status_code}")
        payload = resp.json()
        if payload.get("status") != "000":
            return VerifyResult(
                api, False, resp.status_code, False, elapsed,
                f"API 오류: status={payload.get('status')} {payload.get('message')}",
            )
        schema_ok = "corp_name" in payload and "stock_code" in payload
        return VerifyResult(api, schema_ok, resp.status_code, schema_ok, elapsed, "OK" if schema_ok else "필드 파싱 실패")
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return VerifyResult(api, False, None, False, elapsed, f"{type(exc).__name__}")


def verify_fmp() -> VerifyResult:
    api = "FMP"
    if not settings.fmp_api_key:
        return VerifyResult(api, False, None, False, 0.0, "REPORT_AGENT_FMP_API_KEY 미설정")

    start = time.perf_counter()
    try:
        resp = _get_with_retry(
            "https://financialmodelingprep.com/api/v3/quote/AAPL",  # 단일 quote
            {"apikey": settings.fmp_api_key},
        )
        elapsed = (time.perf_counter() - start) * 1000
        if resp.status_code != 200:
            return VerifyResult(api, False, resp.status_code, False, elapsed, f"HTTP {resp.status_code}")
        payload = resp.json()
        if not payload:
            return VerifyResult(api, False, resp.status_code, False, elapsed, "빈 응답(키/심볼 확인 필요)")
        schema_ok = "symbol" in payload[0] and "price" in payload[0]
        if schema_ok:
            try:
                float(payload[0]["price"])
            except (ValueError, KeyError):
                schema_ok = False
        return VerifyResult(api, schema_ok, resp.status_code, schema_ok, elapsed, "OK" if schema_ok else "필드 파싱 실패")
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return VerifyResult(api, False, None, False, elapsed, f"{type(exc).__name__}")


def main() -> int:
    results = [verify_fred(), verify_bok(), verify_dart(), verify_fmp()]

    print(f"{'API':<10} {'성공':<6} {'HTTP':<6} {'스키마':<8} {'소요(ms)':<10} 상세")
    print("-" * 70)
    for r in results:
        print(
            f"{r.api:<10} {'O' if r.ok else 'X':<6} {str(r.http_status or '-'):<6} "
            f"{'O' if r.schema_ok else 'X':<8} {r.elapsed_ms:<10.0f} {r.detail}"
        )

    failed = [r for r in results if not r.ok]
    if failed:
        print()
        print("실패 API 재현 명령 / 권장 조치:")
        for r in failed:
            print(f"  - {r.api}: python scripts/verify_external_apis.py  (개별 함수: verify_{r.api.split()[0].lower()})")
            if "미설정" in r.detail:
                print(f"    -> .env.local에 해당 키를 채워 넣으세요.")
            elif r.http_status == 401 or r.http_status == 403:
                print(f"    -> 키가 유효한지, 활성화(승인)됐는지 발급처에서 확인하세요.")
            elif r.http_status is None:
                print(f"    -> 네트워크/방화벽 문제일 수 있습니다. VPN·프록시 설정을 확인하세요.")
            else:
                print(f"    -> 응답 스키마가 예상과 다릅니다. API 문서 변경 여부를 확인하세요.")
        return 1

    print()
    print("모든 API 검증 성공.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
