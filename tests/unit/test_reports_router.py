"""reports_router.py의 리포트 타입 등록을 확인한다.

reports_router는 pdf_service를 import하지만 pdf_service 자체가 weasyprint를
지연 임포트하므로(test_pdf_service.py 참고) 이 모듈을 임포트하는 것 자체는
GTK 없는 환경에서도 안전하다 — 실제 PDF 렌더링(stream_report 엔드포인트
호출)만 WeasyPrint를 필요로 한다.
"""
from app.api.routers.reports_router import ReportType, _CONTEXT_BUILDERS


def test_macro_regime_is_registered_as_report_type():
    """4번째 리포트(Macro Regime Observations, Phase 3-3)가 실제로 등록돼
    있는지 확인한다 — enum에만 있고 빌더 매핑이 빠지면 라우팅은 되지만
    500 에러가 난다."""
    assert ReportType.macro_regime.value == "macro_regime"
    assert ReportType.macro_regime in _CONTEXT_BUILDERS


def test_all_report_types_have_context_builders():
    for report_type in ReportType:
        assert report_type in _CONTEXT_BUILDERS, f"{report_type}에 컨텍스트 빌더가 없다"
