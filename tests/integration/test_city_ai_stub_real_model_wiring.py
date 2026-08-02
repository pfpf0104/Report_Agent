"""city_ai_stub이 global_rate_model.predict_change_bp를 실제로 호출·우선하는지 확인한다.

이 테스트는 운영 DB의 KTB1Y/KTB3Y/미국 금리곡선 유무에 좌우되지 않는
불변식만 검증한다 — "실측 예측이 나오면 city_ai_stub이 그 값을 그대로
쓴다"(둘이 같은 값)와 "실측 예측이 없으면 city_ai_stub이 합성값으로
남는다"는 이력 존재 여부와 무관하게 항상 성립해야 한다. 이전 버전은
"운영 DB에 반드시 학습 가능한 이력이 있다"를 전제해 assert했는데, 같은
스위트 안의 다른 테스트 파일(test_ingest_macro_rates.py 등)의 teardown이
KTB1Y/KTB3Y DimAsset을 지우면 실행 순서에 따라 실패하는 취약한 전제였다
(2026-08 실측 재현). 실제 이력을 갖춘 상태에서 두 함수가 실제로 같은 값을
내는지는 tests/integration/test_global_rate_model.py가 격리된 합성 데이터로
검증한다 — 여기서는 "연결 자체가 살아있는가"만 본다.
"""
from datetime import date

from app.computation.fixed_income.city_ai_stub import synthetic_city_ai_output
from app.computation.fixed_income.global_rate_model import predict_change_bp
from app.db.base import SessionLocal


def test_predicted_change_matches_real_model_output_whenever_it_is_available():
    """실측 예측이 나오든 안 나오든(운영 DB의 이력 상태에 좌우되지 않는),
    두 경로가 서로 어긋나면 안 된다 — city_ai_stub이 predict_change_bp를
    호출하고도 결과를 조용히 무시하는 회귀를 잡는다."""
    db = SessionLocal()
    try:
        expected = predict_change_bp(db, date.today())
        out = synthetic_city_ai_output(db, date.today())
    finally:
        db.close()

    if expected is not None:
        assert out["predicted_change_bp"] == expected
    else:
        # 이력이 부족한 상태라면 city_ai_stub도 합성값으로 남아야 한다 —
        # None을 무시하고 엉뚱한 값을 만들어내면 안 된다.
        assert isinstance(out["predicted_change_bp"], float)
