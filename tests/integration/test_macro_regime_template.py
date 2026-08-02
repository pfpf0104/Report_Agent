"""macro_regime 템플릿이 실제 컨텍스트로 끝까지 렌더링되는지 확인한다.

WeasyPrint(PDF 변환)는 건드리지 않는다 — render_html은 순수 Jinja 렌더링만
하므로 GTK 없는 환경에서도 안전하다(test_pdf_service.py의 지연 임포트
회귀와 별개). 여기서 잡으려는 건 템플릿이 참조하는 컨텍스트 키가 실제
빌더가 만드는 키와 어긋나는 것 — Jinja는 기본적으로 undefined 변수를
조용히 빈 문자열로 렌더링해서, 오타가 있어도 예외 없이 통과해버린다.
"""
from datetime import date

from app.computation.regime.dashboard_context import build_macro_regime_context
from app.db.base import SessionLocal
from app.rendering.pdf_service import render_html


def test_macro_regime_template_renders_end_to_end():
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date.today())
    finally:
        db.close()

    html = render_html("macro_regime/report.html", context)

    assert "MACRO REGIME OBSERVATIONS" in html
    assert "CallRank" in html
    assert "MetroGuard" in html
    assert "밸류에이션" in html


def test_macro_regime_template_includes_regime_section_when_available():
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date.today())
    finally:
        db.close()

    html = render_html("macro_regime/report.html", context)

    if context["regime_available"]:
        assert context["regime_quadrant"] in html


def test_macro_regime_template_includes_cross_asset_section_when_available():
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date.today())
    finally:
        db.close()

    html = render_html("macro_regime/report.html", context)

    if context["cross_asset_available"]:
        assert "CROSS-ASSET VIEW" in html
