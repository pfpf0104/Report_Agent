"""데이터 품질 게이트 테스트.

핵심은 test_value_range_catches_percent_vs_bp_unit_error — BOK이 주는 퍼센트
값을 bp 자리에 그대로 넣는 100배 오류를 실제로 잡아내는지 확인한다. 이 오류는
예외를 던지지 않고 그럴듯한 숫자로 남기 때문에 자동 검사가 아니면 못 잡는다.
"""
from datetime import date, timedelta

import pytest

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.quality import (
    ERROR,
    WARNING,
    check_missing_business_days,
    check_outliers,
    check_staleness,
    check_value_range,
    run_quality_gate,
)

CODES = ["TESTKTB3Y", "TESTEQ", "TESTETF", "TESTKRWETF", "TESTUSDETF"]


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(CODES)).delete(synchronize_session=False)
    session.commit()
    session.close()


def _asset(db, code: str, asset_type: str, currency: str = "KRW") -> DimAsset:
    a = DimAsset(asset_type=asset_type, code=code, name_kr=code, currency=currency)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _add(db, asset, trade_date: date, close: float) -> None:
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=trade_date, knowledge_date=trade_date,
        close=close, adj_close=close, source="test",
    ))
    db.commit()


def _rows(db, asset):
    return (
        db.query(FactMarketDaily)
        .filter_by(asset_id=asset.asset_id)
        .order_by(FactMarketDaily.trade_date.asc())
        .all()
    )


# --- 단위 오류 검출 (이 모듈의 존재 이유) ---

def test_value_range_catches_percent_vs_bp_unit_error(db):
    """BOK ECOS는 국고채 금리를 "2.659"(퍼센트)로 준다. 이 값을 bp를 기대하는
    자리에 그대로 넣으면 100배 오류인데, 2.659는 그 자체로 그럴듯해 예외가 나지
    않는다. MACRO의 상식 범위(0~2000bp)로 자릿수를 검사해 잡아낸다."""
    asset = _asset(db, "TESTKTB3Y", AssetType.MACRO.value)
    _add(db, asset, date(2026, 7, 30), 2.659)  # 퍼센트를 그대로 넣은 상태

    issues = check_value_range(asset, _rows(db, asset))

    assert len(issues) == 1
    assert issues[0].severity == ERROR
    assert issues[0].check == "value_range"
    assert "×100" in issues[0].detail  # 조치 힌트가 붙어야 한다


def test_value_range_accepts_krw_etf_price_that_would_fail_usd_bounds(db):
    """KRW ETF는 좌수당 가격이 5~10만원대가 흔하다(예: 통안채1년 ETF 122260이
    103,960원). USD ETF와 같은 상한(100,000)을 쓰면 이런 정상 가격이 오탐으로
    잡힌다 — 2026-08 실측: 이 값이 매일 ERROR로 잡히던 것을 실제 알림에서
    발견하고 통화별 범위 분리로 수정했다."""
    asset = _asset(db, "TESTKRWETF", AssetType.ETF.value, currency="KRW")
    _add(db, asset, date(2026, 7, 30), 103_960.0)

    issues = check_value_range(asset, _rows(db, asset))

    assert issues == []


def test_value_range_still_catches_usd_etf_out_of_range(db):
    """USD ETF(XLE $59, SPY $747 수준)에 KRW 상한을 실수로 적용하면 이번엔
    반대로 진짜 단위 오류를 놓친다 — 통화별 범위가 양쪽 다 지켜지는지 확인한다."""
    asset = _asset(db, "TESTUSDETF", AssetType.ETF.value, currency="USD")
    _add(db, asset, date(2026, 7, 30), 5_950_000.0)  # 실제로는 $59.50인데 소수점이 밀린 상태를 가정

    issues = check_value_range(asset, _rows(db, asset))

    assert len(issues) == 1
    assert issues[0].severity == ERROR


def test_value_range_accepts_correctly_scaled_bp(db):
    asset = _asset(db, "TESTKTB3Y", AssetType.MACRO.value)
    _add(db, asset, date(2026, 7, 30), 265.9)  # 올바르게 bp로 변환된 상태
    assert check_value_range(asset, _rows(db, asset)) == []


def test_value_range_catches_equity_price_off_by_orders_of_magnitude(db):
    """삼성전자 주가가 원 단위가 아니라 천원 단위로 들어온 경우 등."""
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 30), 20.85)  # 208,500원이어야 하는데 자릿수가 날아감
    issues = check_value_range(asset, _rows(db, asset))
    assert len(issues) == 1
    assert issues[0].severity == ERROR


# --- 스테일 ---

def test_staleness_flags_old_data(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 1), 200_000)
    issues = check_staleness(asset, _rows(db, asset), as_of=date(2026, 7, 30))
    assert len(issues) == 1
    assert issues[0].severity == ERROR
    assert "29일 경과" in issues[0].detail


def test_staleness_accepts_recent_data_across_a_weekend(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 27), 200_000)
    assert check_staleness(asset, _rows(db, asset), as_of=date(2026, 7, 30)) == []


# --- 이상치 ---

def test_outlier_flags_implausible_daily_move(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 29), 200_000)
    _add(db, asset, date(2026, 7, 30), 400_000)  # +100%
    issues = check_outliers(asset, _rows(db, asset))
    assert len(issues) == 1
    assert issues[0].severity == WARNING
    assert "100.0%" in issues[0].detail


def test_outlier_ignores_normal_move(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 29), 200_000)
    _add(db, asset, date(2026, 7, 30), 206_000)  # +3%
    assert check_outliers(asset, _rows(db, asset)) == []


def test_outlier_survives_zero_previous_close_without_dividing_by_zero(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 29), 0.0)
    _add(db, asset, date(2026, 7, 30), 200_000)
    check_outliers(asset, _rows(db, asset))  # ZeroDivisionError가 나면 실패


# --- 결측 ---

def test_missing_business_days_flags_long_gap(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 1), 200_000)
    _add(db, asset, date(2026, 7, 30), 200_000)  # 사이 영업일 대량 결측
    issues = check_missing_business_days(asset, _rows(db, asset))
    assert len(issues) == 1
    assert issues[0].severity == WARNING


def test_missing_business_days_ignores_weekend(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    _add(db, asset, date(2026, 7, 24), 200_000)  # 금
    _add(db, asset, date(2026, 7, 27), 200_000)  # 월
    assert check_missing_business_days(asset, _rows(db, asset)) == []


# --- 게이트 전체 ---

def test_gate_reports_error_when_no_assets_at_all(db):
    """현재 DB 상태(전부 비어 있음)가 정확히 이 경로 — 조용히 통과하면 안 된다."""
    report = run_quality_gate(db, as_of=date(2026, 7, 30), asset_codes=["NONEXISTENT"])
    assert not report.ok
    assert report.errors[0].check == "has_assets"


def test_gate_reports_error_when_asset_has_no_visible_rows(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    # knowledge_date가 미래라 as_of 시점에는 보이지 않는다
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=date(2026, 7, 30), knowledge_date=date(2027, 1, 1),
        close=200_000, adj_close=200_000, source="test",
    ))
    db.commit()

    report = run_quality_gate(db, as_of=date(2026, 7, 30), asset_codes=["TESTEQ"])
    assert not report.ok
    assert report.errors[0].check == "has_data"


def test_gate_passes_on_clean_data(db):
    asset = _asset(db, "TESTEQ", AssetType.EQUITY.value)
    d = date(2026, 7, 30)
    for i in range(6, -1, -1):  # 최근 영업일 위주로 7일치
        day = d - timedelta(days=i)
        if day.weekday() < 5:
            _add(db, asset, day, 200_000 + i * 100)

    report = run_quality_gate(db, as_of=d, asset_codes=["TESTEQ"])
    assert report.ok, [str(i) for i in report.issues]
    assert "이상 없음" in report.summary()
