"""리포트 디자인 색상/타이포의 단일 진실 공급원(SSOT).

CSS(`app/rendering/static/css/tokens.css`)와 matplotlib 차트가 같은 색상을
쓰도록, 두 곳 모두 이 파일에 정의된 값을 그대로 옮겨 쓴다. 값을 바꿀 때는
반드시 tokens.css도 함께 갱신한다.
"""

# 브랜드 / 레이아웃 색상
NAVY_900 = "#1a2b4c"   # 상단 바, 메인 타이틀
NAVY_700 = "#2c4570"   # 섹션 라벨, 강조 텍스트
GRAY_600 = "#6b7280"   # 캡션, 보조 텍스트, 푸터
GRAY_300 = "#d7dbe0"   # 카드/테이블 구분선
GRAY_100 = "#f4f5f7"   # 프로세스 스텝 카드 배경

# 시맨틱 색상 — 한국 시장 관행: 상승/우호 = 레드, 하락/불리 = 블루
UP = "#c0392b"
DOWN = "#2f6fb0"
NEUTRAL = GRAY_600

# 콜아웃 박스 3종 (좌측 액센트 바 + 연한 배경)
CALLOUT = {
    "info": {"bg": "#eaf1fb", "border": NAVY_900, "text": NAVY_900},
    "warning": {"bg": "#fdf1e0", "border": "#c47f2c", "text": "#8a5a17"},
    "success": {"bg": "#eaf5ec", "border": "#3f8556", "text": "#255c39"},
}

# matplotlib/seaborn 차트 팔레트 — 라인 차트는 주력·벤치마크·비교군 3계열 고정
CHART_PALETTE = {
    "primary": NAVY_900,      # 주력 시리즈 (예: Top1, MetroGuard-KR)
    "secondary": "#5b8fd1",   # 벤치마크 시리즈 (예: SPY, D3 중립)
    "tertiary": GRAY_600,     # 비교군 (점선)
    "up": UP,
    "down": DOWN,
    "categorical": [NAVY_900, "#5b8fd1", "#3f8556", "#c47f2c"],
}

FONT_FAMILY = "Pretendard, 'Noto Sans KR', sans-serif"


def semantic_color(value: float) -> str:
    """부호에 따라 상승(레드)/하락(블루)/중립 색상을 반환한다."""
    if value > 0:
        return UP
    if value < 0:
        return DOWN
    return NEUTRAL
