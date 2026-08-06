"""데이터 품질 게이트 테스트.

핵심은 test_value_range_catches_percent_vs_bp_unit_error — BOK이 주는 퍼센트
값을 bp 자리에 그대로 넣는 100배 오류를 실제로 잡아내는지 확인한다. 이 오류는
예외를 던지지 않고 그럴듯한 숫자로 남기 때문에 자동 검사가 아니면 못 잡는다.
"""
from datetime import date, timedelta

import pytest

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.quality import (
    ERROR,
    WARNING,
    check_has_financial_statements,
    check_missing_business_days,
    check_outliers,
    check_staleness,
    check_value_range,
    run_quality_gate,
)

CODES = ["TESTKTB3Y", "TESTEQ", "TESTETF", "TESTKRWETF", "TESTUSDETF", "TESTSPREAD", "TESTINDEX", "TESTFSONLY"]


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    )).delete(synchronize_session=False)
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


def test_value_range_accepts_negative_spread_for_macro_index(db):
    """MACRO_INDEX(스프레드)는 금리곡선 역전 시 음수가 될 수 있다(예: T10Y2Y)
    — MACRO(금리, 항상 양수)와 반대로 하한이 음수여야 정상값을 오탐하지
    않는다."""
    asset = _asset(db, "TESTSPREAD", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 30), -1.5)  # 역전된 금리곡선 스프레드

    assert check_value_range(asset, _rows(db, asset)) == []


def test_value_range_accepts_dollar_index_scale_for_macro_index(db):
    """달러지수(DTWEXBGS) 같은 무차원 지수는 100 안팎이라 MACRO(bp, 수백대)
    범위와 자릿수 자체가 다르다."""
    asset = _asset(db, "TESTINDEX", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 30), 120.71)

    assert check_value_range(asset, _rows(db, asset)) == []


def test_value_range_catches_macro_index_off_by_orders_of_magnitude(db):
    """MACRO_INDEX도 완전히 틀린 자릿수(예: bp로 착각해 100배 부풀린 값)는
    여전히 잡아야 한다 — 범위를 넓힌 것이 검사 무력화를 뜻하지 않는다."""
    asset = _asset(db, "TESTINDEX", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 30), 12_071.0)  # 120.71을 100배 부풀린 상태

    issues = check_value_range(asset, _rows(db, asset))

    assert len(issues) == 1
    assert issues[0].severity == ERROR


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


def test_outlier_uses_absolute_move_for_macro_spread_near_zero(db):
    """MACRO_INDEX(스프레드류, T10Y2Y 등)는 0 근처를 오가는 %p 값이라 상대변동%을
    쓰면 절대값이 작을 때 발산한다 — 2026-08 실측: 0.04→-0.05(9bp 변화)가
    상대변동 225%로 잡혀 매일 오탐이 났다. 절대변동으로 봐야 정상 변동을
    이상치로 오판하지 않는다."""
    asset = _asset(db, "TESTSPREAD", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 29), 0.04)
    _add(db, asset, date(2026, 7, 30), -0.05)  # 절대변동 0.09 — 임계(3.0) 이내

    assert check_outliers(asset, _rows(db, asset)) == []


def test_outlier_flags_large_absolute_move_for_macro_spread(db):
    asset = _asset(db, "TESTSPREAD", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 29), 0.5)
    _add(db, asset, date(2026, 7, 30), 4.0)  # 절대변동 3.5 — 임계(3.0) 초과

    issues = check_outliers(asset, _rows(db, asset))

    assert len(issues) == 1
    assert issues[0].severity == WARNING
    assert "+3.50" in issues[0].detail


def test_outlier_absolute_move_exactly_at_threshold_is_not_flagged(db):
    """경계값(임계와 정확히 같은 변동)은 잡히면 안 된다 — 코드가 `>`(초과)를
    쓰지 `>=`(이상)를 쓰지 않는다는 걸 명시적으로 고정한다."""
    asset = _asset(db, "TESTSPREAD", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 29), 0.0)
    _add(db, asset, date(2026, 7, 30), 3.0)  # 절대변동이 임계(3.0)와 정확히 같음

    assert check_outliers(asset, _rows(db, asset)) == []


def test_outlier_absolute_move_just_over_threshold_is_flagged(db):
    asset = _asset(db, "TESTSPREAD", AssetType.MACRO_INDEX.value, currency="USD")
    _add(db, asset, date(2026, 7, 29), 0.0)
    _add(db, asset, date(2026, 7, 30), 3.01)  # 임계(3.0)를 근소하게 초과

    issues = check_outliers(asset, _rows(db, asset))

    assert len(issues) == 1


def test_outlier_uses_absolute_move_for_macro_rate_at_low_bp_level(db):
    """MACRO(금리, bp)도 저금리 구간에서는 절대값이 작아 상대변동%이 발산한다
    — 2026-08 실측: 2021년 US1MO가 3~10bp대에서 1bp만 움직여도 상대변동
    30~50%로 잡혀 65건의 오탐이 났다."""
    asset = _asset(db, "TESTKTB3Y", AssetType.MACRO.value)
    _add(db, asset, date(2026, 7, 29), 5.0)
    _add(db, asset, date(2026, 7, 30), 7.0)  # 절대변동 2bp — 임계(50bp) 이내, 상대변동은 40%

    assert check_outliers(asset, _rows(db, asset)) == []


def test_outlier_flags_large_absolute_move_for_macro_rate(db):
    """실제 정책금리 급변동(2022년 연준 75bp 자이언트스텝)은 여전히 잡혀야
    한다 — 범위를 절대변동으로 바꾼 것이 검사 무력화를 뜻하지 않는다."""
    asset = _asset(db, "TESTKTB3Y", AssetType.MACRO.value)
    _add(db, asset, date(2026, 7, 29), 158.0)
    _add(db, asset, date(2026, 7, 30), 233.0)  # 절대변동 75bp — 임계(50bp) 초과

    issues = check_outliers(asset, _rows(db, asset))

    assert len(issues) == 1
    assert issues[0].severity == WARNING


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


def test_has_financial_statements_flags_missing_data(db):
    asset = _asset(db, "TESTFSONLY", AssetType.EQUITY.value, currency="USD")
    issues = check_has_financial_statements(db, asset, as_of=date(2026, 7, 30))
    assert len(issues) == 1
    assert issues[0].severity == ERROR
    assert issues[0].check == "has_financial_statements"


def test_has_financial_statements_passes_when_row_visible(db):
    asset = _asset(db, "TESTFSONLY", AssetType.EQUITY.value, currency="USD")
    db.add(FactFinancialQuarterly(
        asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=2,
        knowledge_date=date(2026, 3, 19), bps=64.24, roe=0.19, source="test",
    ))
    db.commit()

    assert check_has_financial_statements(db, asset, as_of=date(2026, 7, 30)) == []


def test_has_financial_statements_respects_point_in_time_cutoff(db):
    """knowledge_date가 미래인 행은 아직 '없는' 것으로 취급해야 한다."""
    asset = _asset(db, "TESTFSONLY", AssetType.EQUITY.value, currency="USD")
    db.add(FactFinancialQuarterly(
        asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=2,
        knowledge_date=date(2027, 1, 1), bps=64.24, roe=0.19, source="test",
    ))
    db.commit()

    issues = check_has_financial_statements(db, asset, as_of=date(2026, 7, 30))
    assert len(issues) == 1


def test_gate_skips_price_checks_for_financial_statements_only_assets(db, monkeypatch):
    """FINANCIAL_STATEMENTS_ONLY_CODES에 등록된 자산(마이크론 등)은
    fact_market_daily가 없어도 has_data 등 가격 기반 검사로 ERROR가 나면
    안 된다 — 2026-08 실측: MU가 정상 상태인데도 has_data ERROR로 잡혔다.
    운영 코드 "MU"는 다른 테스트/실제 데이터와 충돌할 수 있어 격리된 코드로
    오버라이드 매핑에 임시로 추가해 검증한다."""
    import app.ingestion.quality as quality_module

    monkeypatch.setattr(
        quality_module, "FINANCIAL_STATEMENTS_ONLY_CODES",
        quality_module.FINANCIAL_STATEMENTS_ONLY_CODES | {"TESTFSONLY"},
    )

    asset = _asset(db, "TESTFSONLY", AssetType.EQUITY.value, currency="USD")
    as_of = date(2026, 7, 30)
    db.add(FactFinancialQuarterly(
        asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=2,
        knowledge_date=date(2026, 3, 19), bps=64.24, roe=0.19, source="test",
    ))
    db.commit()

    report = run_quality_gate(db, as_of=as_of, asset_codes=["TESTFSONLY"])

    assert report.ok, [str(i) for i in report.issues]


def test_gate_uses_staleness_override_for_specific_assets(db, monkeypatch):
    """USDINDEX(연준 달러지수)는 발표 자체가 실측으로 확인한 결과 며칠 지연되므로
    기본 7일 임계값이 아니라 15일을 쓴다 — 2026-08 실측: observation_end가
    조회일보다 9일 전이라 기본 임계값으로는 매일 오탐이 났다. 실제 코드
    "USDINDEX"는 운영 자산이라(unique 제약) 격리된 코드로 오버라이드
    매핑에 임시로 추가해 검증한다."""
    import app.ingestion.quality as quality_module

    monkeypatch.setitem(quality_module.STALE_AFTER_DAYS_OVERRIDE, "TESTINDEX", 15)

    asset = _asset(db, "TESTINDEX", AssetType.MACRO_INDEX.value, currency="USD")
    as_of = date(2026, 8, 2)
    _add(db, asset, as_of - timedelta(days=9), 120.71)  # 9일 경과 — 기본 7일 초과

    report = run_quality_gate(db, as_of=as_of, asset_codes=["TESTINDEX"])

    assert report.ok, [str(i) for i in report.issues]


def test_gate_uses_extended_staleness_override_for_fhfa_housing_index(db, monkeypatch):
    """FHFA 전미 주택가격지수(USHPIFHFA)는 분기 발표(약 92일)+공표 지연(약
    148일)이 겹쳐 정상 상태에서도 240일 가까이 벌어질 수 있다 — 2026-08
    실측: 218일 경과 상태가 MACRO_ECONOMIC 공통 임계(150일)로 오탐 처리된
    것을 발견하고 코드별 250일 예외를 추가했다. 실제 코드 "USHPIFHFA"는
    운영 자산이라(unique 제약) 격리된 코드로 오버라이드 매핑에 임시로
    추가해 검증한다(다른 STALE_AFTER_DAYS_OVERRIDE 테스트와 동일 패턴)."""
    import app.ingestion.quality as quality_module

    monkeypatch.setitem(quality_module.STALE_AFTER_DAYS_OVERRIDE, "TESTINDEX", 250)

    asset = _asset(db, "TESTINDEX", AssetType.MACRO_ECONOMIC.value, currency="USD")
    as_of = date(2026, 8, 7)
    _add(db, asset, as_of - timedelta(days=218), 713.09)  # 실측과 동일한 경과일

    report = run_quality_gate(db, as_of=as_of, asset_codes=["TESTINDEX"])

    assert report.ok, [str(i) for i in report.issues]
