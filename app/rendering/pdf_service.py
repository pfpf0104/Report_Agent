"""WeasyPrint 기반 PDF 렌더링 서비스.

WeasyPrint 렌더링은 CPU-bound 작업이다. FastAPI의 기본 스레드풀에만 맡기면
동시 요청이 몰릴 때 스레드풀이 고갈되어 헬스체크·ingestion 트리거 같은 짧은
I/O 요청까지 지연될 수 있으므로, 전용 ProcessPoolExecutor에서 렌더링하고
그 결과만 비동기로 기다린다.
"""
from __future__ import annotations

import asyncio
import io
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# PDF 렌더링 전용 프로세스 풀. 코어 수에 맞춰 워커 수를 조정한다.
_executor = ProcessPoolExecutor(max_workers=4)


def _render_pdf_bytes(html_content: str, base_url: str) -> bytes:
    return HTML(string=html_content, base_url=base_url).write_pdf()


def render_html(template_name: str, context: dict) -> str:
    template = _env.get_template(template_name)
    return template.render(static_root=STATIC_DIR.as_uri(), **context)


def render_report_pdf(template_name: str, context: dict) -> io.BytesIO:
    """동기 호출용. FastAPI의 sync 엔드포인트(def)에서 쓰면 자동 스레드풀로 넘어간다."""
    html_content = render_html(template_name, context)
    pdf_bytes = _render_pdf_bytes(html_content, str(TEMPLATES_DIR))
    return io.BytesIO(pdf_bytes)


async def render_report_pdf_async(template_name: str, context: dict) -> io.BytesIO:
    """async 엔드포인트용. 전용 ProcessPoolExecutor에서 렌더링해 이벤트 루프를 보호한다."""
    loop = asyncio.get_running_loop()
    html_content = render_html(template_name, context)
    pdf_bytes = await loop.run_in_executor(
        _executor, partial(_render_pdf_bytes, html_content, str(TEMPLATES_DIR)),
    )
    return io.BytesIO(pdf_bytes)
