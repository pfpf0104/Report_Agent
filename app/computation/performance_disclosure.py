"""성과 보고 준비 상태 — 실제 백테스트 엔진이 생기기 전까지 쓰는 공시 콘텐츠.

## 왜 성과 숫자를 싣지 않는가

이전 버전은 CallRank에 "Top 1 연환산 순수익률 42.1%", "Treasury 조정 Sharpe 2.05"
같은 지표와 우상향 누적 곡선을 실었고, MetroGuard에는 연도별 초과수익 표를 실었다.
캡션에 "합성 예시"라고 붙어 있긴 했지만 그 숫자들의 실체는 다음과 같았다:

    rng = np.random.default_rng(as_of.toordinal())
    steps = rng.normal(0.028, 0.05, size=24)

즉 **전략을 백테스트한 결과가 아니라 난수**였다. 시드가 고정돼 있어 매번 같은 값이
나올 뿐, 42.1%라는 숫자는 CallRank의 랭킹 로직과 아무 인과관계가 없다.

가설적(hypothetical) 성과를 공시와 함께 싣는 것과, 전략과 무관한 난수를 성과처럼
배치하는 것은 다른 문제다. 후자는 캡션으로 방어되지 않는다 — GIPS가 금지하는
"실현되지 않은 성과의 실적 제시"에 해당할 소지가 있고, 무엇보다 읽는 사람을
오도한다. 그래서 숫자와 차트를 제거하고 이 페이지로 대체했다.

## 언제 다시 실을 수 있나

MASTER_PLAN.md Phase 1(리스크·성과 엔진)에서 실제 워크포워드 백테스트 엔진을
구현한 뒤, 아래 GIPS 요건을 충족하는 형태로만 복원한다.
"""
from __future__ import annotations

# GIPS Composite Report 필수 요소 — 성과 페이지 복원 시 충족해야 할 체크리스트.
# 출처: GIPS Standards for Firms 2020 (references/README.md 참고).
GIPS_REQUIREMENTS = [
    {
        "title": "01 · 최소 5년 연간 수익률",
        "body": "설정 이후 5년 미만이면 전 기간. 임의 구간을 잘라 유리한 기간만 보여주는 것을 막는다.",
    },
    {
        "title": "02 · 벤치마크 병기 필수",
        "body": "동일 기간 벤치마크 수익률을 나란히 제시한다. 벤치마크 없는 절대수익 단독 제시는 인정되지 않는다.",
    },
    {
        "title": "03 · 3년 연환산 사후 표준편차",
        "body": "각 연도 말 기준 포트폴리오와 벤치마크의 36개월 ex-post 표준편차를 함께 공시한다.",
    },
]

PENDING_NOTICE_TITLE = "성과 수치는 의도적으로 비워 두었다"
PENDING_NOTICE_BODY = (
    "이 리포트에는 아직 수익률·Sharpe·누적성과 지표를 싣지 않는다. 실제 워크포워드 "
    "백테스트 엔진(거래비용·슬리피지·리밸런싱 반영)이 구현되기 전까지, 어떤 성과 "
    "숫자도 검증 가능한 근거를 갖지 못하기 때문이다. 이전 버전에 있던 지표는 전략 "
    "로직과 무관한 난수였으므로 제거했다."
)

METHODOLOGY_READY_TITLE = "다만 방법론은 이미 검증 가능하다"
METHODOLOGY_READY_BODY = (
    "성과와 별개로, 이 리포트의 계산 로직 자체는 원문 보고서의 수치 예시로 검산돼 "
    "있다(예: MetroGuard의 A⁻=27.5bp → g≈0.968 → D*≈1.06년, 밸류에이션의 시나리오별 "
    "적정가를 원 단위로 재현). 앞 페이지들의 숫자는 전부 이 검증된 로직의 실제 출력이다."
)


def build_performance_pending_context() -> dict:
    """성과 페이지를 대체하는 컨텍스트. CallRank·MetroGuard가 공유한다."""
    return {
        "performance_pending_title": PENDING_NOTICE_TITLE,
        "performance_pending_body": PENDING_NOTICE_BODY,
        "methodology_ready_title": METHODOLOGY_READY_TITLE,
        "methodology_ready_body": METHODOLOGY_READY_BODY,
        "gips_requirements": GIPS_REQUIREMENTS,
    }
