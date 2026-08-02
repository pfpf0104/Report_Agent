import io
from datetime import date
from enum import Enum

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.computation.fixed_income.duration_controller import build_metroguard_context
from app.computation.quant.ridge_sector_rank import build_callrank_context
from app.computation.regime.dashboard_context import build_macro_regime_context
from app.computation.valuation.residual_income_model import build_valuation_context
from app.db.base import get_db
from app.rendering.pdf_service import render_report_pdf_async
from app.sync.r2_sync import upload_report_pdf
from app.sync.supabase_sync import sync_report_snapshot

router = APIRouter()


class ReportType(str, Enum):
    callrank = "callrank"
    metroguard = "metroguard"
    valuation = "valuation"
    macro_regime = "macro_regime"


_CONTEXT_BUILDERS = {
    ReportType.callrank: build_callrank_context,
    ReportType.metroguard: build_metroguard_context,
    ReportType.valuation: build_valuation_context,
    ReportType.macro_regime: build_macro_regime_context,
}


def _sync_after_render(report_type: str, report_date: date, context: dict, pdf_bytes: bytes) -> None:
    """R2 업로드 → Supabase snapshot 동기화. 둘 다 설정 안 됐으면 각자 조용히 스킵된다."""
    pdf_url = upload_report_pdf(report_type, report_date, pdf_bytes)
    sync_report_snapshot(report_type=report_type, report_date=report_date, context=context, pdf_url=pdf_url)


@router.get("/{report_type}/{report_date}.pdf")
async def stream_report(
    report_type: ReportType,
    report_date: date,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    builder = _CONTEXT_BUILDERS[report_type]
    context = builder(db=db, as_of=report_date)
    pdf_buf = await render_report_pdf_async(f"{report_type.value}/report.html", context)
    pdf_bytes = pdf_buf.getvalue()

    # 서빙 캐시 동기화는 응답을 늦추지 않도록 백그라운드로 실행한다.
    background_tasks.add_task(_sync_after_render, report_type.value, report_date, context, pdf_bytes)

    filename = f"{report_type.value}_{report_date.isoformat()}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
