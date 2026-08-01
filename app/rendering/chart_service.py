"""matplotlib 차트를 렌더링해 <img src="..."> 에 바로 쓰는 base64 PNG data URI로 반환한다.

색상은 app/core/design_tokens.py의 CHART_PALETTE를 그대로 쓴다 — HTML/CSS와
차트 색상이 어긋나지 않도록 하는 SSOT 규칙(그 파일 docstring 참고).
"""
from __future__ import annotations

import base64
import io
import logging
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from app.core.design_tokens import CHART_PALETTE, GRAY_300, semantic_color  # noqa: E402

logger = logging.getLogger("app.rendering")

# 한글 라벨 렌더링용. Pretendard는 woff2만 있어 matplotlib이 못 읽으므로
# HTML/CSS는 Pretendard, 차트는 시스템에 설치된 한글 폰트를 쓴다. 배포 대상
# OS마다 경로가 달라 리눅스(Noto Sans CJK)/Windows(맑은 고딕)/macOS(AppleSDGothicNeo)
# 후보를 모두 나열한다 — 실제 로컬 Windows PC에서 리눅스 경로만 있어 폰트를
# 못 찾고 DejaVu Sans로 조용히 폴백해(한글 라벨이 깨짐) 확인 후 추가함.
_KOREAN_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/AppleGothic.ttf",
]


def _register_korean_font() -> str | None:
    for path in _KOREAN_FONT_CANDIDATES:
        try:
            fm.fontManager.addfont(path)
            return fm.FontProperties(fname=path).get_name()
        except (FileNotFoundError, RuntimeError):
            continue
    return None


_FONT_NAME = _register_korean_font()
if _FONT_NAME:
    plt.rcParams["font.family"] = _FONT_NAME
else:
    logger.warning(
        "한글 폰트를 찾지 못해 matplotlib 기본 폰트(DejaVu Sans)로 폴백합니다 — "
        "차트의 한글 라벨이 네모(tofu)로 깨져 보일 수 있습니다. "
        "_KOREAN_FONT_CANDIDATES에 이 PC에 실제로 설치된 한글 폰트 경로를 추가하세요."
    )
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 9


def _fig_to_data_uri(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def line_chart(
    x_labels: list[str], series: dict[str, list[float]], *, figsize=(6.2, 2.6), max_x_ticks: int = 7
) -> str:
    """여러 시리즈를 그린다. 1번째=주력(실선), 2번째=벤치마크(실선), 3번째 이후=점선.

    max_x_ticks: x_labels가 이보다 많으면 등간격으로 골라 겹침을 막는다
    (첨부 보고서 4페이지처럼 30개월치를 6~7개 라벨로만 표시하는 방식).
    """
    fig, ax = plt.subplots(figsize=figsize)
    colors = CHART_PALETTE["categorical"]
    linestyles = ["-", "-", "--", ":"]
    x_positions = range(len(x_labels))
    for i, (name, values) in enumerate(series.items()):
        ax.plot(
            x_positions,
            values,
            label=name,
            color=colors[i % len(colors)],
            linestyle=linestyles[i % len(linestyles)],
            linewidth=1.8,
        )

    if len(x_labels) > max_x_ticks:
        step = math.ceil(len(x_labels) / max_x_ticks)
        tick_positions = list(range(0, len(x_labels), step))
        if tick_positions[-1] != len(x_labels) - 1:
            tick_positions.append(len(x_labels) - 1)
    else:
        tick_positions = list(x_positions)
    ax.set_xticks(tick_positions, [x_labels[i] for i in tick_positions])

    ax.spines[["top", "right"]].set_visible(False)
    ax.spines["left"].set_color(GRAY_300)
    ax.spines["bottom"].set_color(GRAY_300)
    ax.tick_params(colors="#6b7280", labelsize=8)
    # "upper left" 고정 위치는 데이터 모양에 따라 선과 겹친다(예: 첫 해가 가장
    # 높고 우하향하는 ROE 경로 차트에서 범례가 선 위에 그대로 얹힌 문제를 확인).
    # "best"는 matplotlib이 실제 선 위치를 보고 겹치지 않는 위치를 고른다.
    ax.legend(frameon=False, fontsize=8, loc="best")
    ax.grid(axis="y", color=GRAY_300, linewidth=0.6)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def horizontal_bar_chart(
    labels: list[str], values: list[float], *, figsize=(6.2, 1.6), value_fmt: str = "{:.1f}%"
) -> str:
    fig, ax = plt.subplots(figsize=figsize)
    colors = [CHART_PALETTE["primary"], CHART_PALETTE["secondary"], CHART_PALETTE["tertiary"]]
    bars = ax.barh(labels, values, color=[colors[i % len(colors)] for i in range(len(labels))], height=0.5)
    ax.bar_label(bars, labels=[value_fmt.format(v) for v in values], padding=4, fontsize=8, color="#1f2933")
    ax.spines[:].set_visible(False)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=9, colors="#1f2933")
    ax.invert_yaxis()
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def vertical_bar_chart(
    labels: list[str], values: list[float], *, figsize=(6.2, 2.6), value_fmt: str = "{:.0f}", semantic: bool = True
) -> str:
    """semantic=True면 부호에 따라 UP/DOWN 색상을 쓰고, False면 categorical[0]로 통일한다."""
    fig, ax = plt.subplots(figsize=figsize)
    bar_colors = [semantic_color(v) for v in values] if semantic else CHART_PALETTE["primary"]
    bars = ax.bar(labels, values, color=bar_colors, width=0.5)
    # 막대 라벨이 x축 눈금 라벨과 겹치지 않도록 데이터 바깥쪽으로만 여유를 둔다.
    # 0은 항상 축 범위에 포함시킨다 — 그렇지 않으면(예: 모두 양수인 값들) 막대
    # 높이가 크기를 왜곡해 보이는 truncated-axis 문제가 생긴다(실제로 한 번
    # 발생시켜 확인 후 수정함: +9.4%/+11.7% 막대가 축을 9~12로 자르니 실제
    # 비율과 다르게 2배 이상 차이나 보였다).
    data_min, data_max = min(values), max(values)
    y_lo, y_hi = min(0.0, data_min), max(0.0, data_max)
    span = (y_hi - y_lo) or 1.0
    pad = span * 0.18
    if data_min < 0:
        y_lo -= pad
    if data_max > 0:
        y_hi += pad
    ax.set_ylim(y_lo, y_hi)
    ax.bar_label(bars, labels=[value_fmt.format(v) for v in values], padding=4, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines["left"].set_color(GRAY_300)
    ax.spines["bottom"].set_color(GRAY_300)
    ax.tick_params(labelsize=8, colors="#1f2933")
    ax.axhline(0, color=GRAY_300, linewidth=0.8)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def donut_chart(labels: list[str], values: list[float], *, figsize=(4, 4), center_text: str | None = None) -> str:
    fig, ax = plt.subplots(figsize=figsize)
    colors = CHART_PALETTE["categorical"]
    wedges, _ = ax.pie(
        values,
        colors=[colors[i % len(colors)] for i in range(len(values))],
        startangle=90,
        wedgeprops={"width": 0.38, "edgecolor": "white"},
    )
    for wedge, value in zip(wedges, values):
        angle = math.radians((wedge.theta2 + wedge.theta1) / 2)
        x, y = math.cos(angle) * 0.82, math.sin(angle) * 0.82
        ax.text(x, y, f"{value:.1f}%", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    if center_text:
        ax.text(0, 0, center_text, ha="center", va="center", fontsize=11, fontweight="bold", color="#1a2b4c")
    ax.set_aspect("equal")
    fig.tight_layout()
    return _fig_to_data_uri(fig)
