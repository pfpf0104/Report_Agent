"""WeasyPrint 기반 PDF 렌더링 서비스.

WeasyPrint 렌더링은 CPU-bound 작업이다. FastAPI의 기본 스레드풀에만 맡기면
동시 요청이 몰릴 때 스레드풀이 고갈되어 헬스체크·ingestion 트리거 같은 짧은
I/O 요청까지 지연될 수 있으므로, 전용 ProcessPoolExecutor에서 렌더링하고
그 결과만 비동기로 기다린다.

WeasyPrint는 GTK 계열 네이티브 라이브러리(libgobject-2.0-0 등)에 의존한다.
Windows에 네이티브 GTK가 없으면 `from weasyprint import HTML`이 모듈 임포트
시점에 바로 실패한다 — 로컬 환경에서 실제로 재현됨(health 엔드포인트조차 응답
못 하고 앱 전체가 기동 실패). 그래서 weasyprint import를 이 모듈의 최상단이
아니라 실제로 PDF를 렌더링하는 함수 안으로 미룬다: `/health`, `/ingestion`
라우터는 PDF를 렌더링하지 않으므로 weasyprint가 없어도 정상 기동돼야 한다.
Windows에서 로컬 개발 시에는 WSL2 또는 Docker 사용을 권장한다(README 참고).
"""
from __future__ import annotations

import asyncio
import io
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# PDF 렌더링 전용 프로세스 풀. 코어 수에 맞춰 워커 수를 조정한다.
# mp_context를 spawn으로 강제한다 — 기본값인 fork는 워커가 부모의 열린 파일
# 디스크립터(uvicorn의 리스닝 소켓 포함)를 그대로 물려받아, 부모 프로세스만
# 죽여도 워커가 소켓을 쥔 채 남아 포트가 계속 점유되는 좀비 프로세스를 만든다.
_executor = ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn"))


def shutdown_executor() -> None:
    """FastAPI 종료 이벤트에서 호출해 워커 프로세스를 정리한다.

    wait=True로 블로킹해야 한다 — False로 두면 이 호출이 반환된 직후 인터프리터가
    종료되면서 워커가 종료 시그널을 받지 못한 채 부모 없는 고아 프로세스로 남아
    queue.get()에서 영원히 블록되는 현상이 실제로 재현됐다(py-spy로 확인).
    """
    _executor.shutdown(wait=True, cancel_futures=True)


def _render_pdf_bytes(html_content: str, base_url: str) -> bytes:
    from weasyprint import HTML  # 지연 임포트 — 모듈 docstring 참고

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
