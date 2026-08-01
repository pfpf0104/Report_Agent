"""weasyprint가 GTK 네이티브 라이브러리 부재로 import 자체에 실패하는 환경
(예: GTK 런타임 없는 Windows)에서도 health/ingestion 라우터가 pdf_service를
안전하게 import할 수 있는지 확인한다.

실제로 로컬 환경에서 `from weasyprint import HTML`이 모듈 최상단에 있어 앱
전체가 기동 실패하는 문제가 재현됐다 — 지연 임포트로 고친 회귀 테스트다.
"""
import sys
from unittest import mock

import app.rendering.pdf_service as pdf_service


def test_pdf_service_module_does_not_bind_weasyprint_at_top_level():
    """HTML이 모듈 전역에 바인딩돼 있으면 최상단에서 import하는 것과 같다 —
    지연 임포트라면 함수 안에서만 지역 변수로 존재해야 한다."""
    assert not hasattr(pdf_service, "HTML")


def test_render_pdf_bytes_raises_clear_error_without_weasyprint():
    with mock.patch.dict(sys.modules, {"weasyprint": None}):
        try:
            pdf_service._render_pdf_bytes("<html></html>", "/tmp")
        except ImportError:
            pass
        else:
            raise AssertionError("weasyprint가 없으면 ImportError가 나야 한다")
