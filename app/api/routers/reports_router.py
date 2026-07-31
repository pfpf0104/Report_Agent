from datetime import date
from enum import Enum

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.computation.fixed_income.duration_controller import build_metroguard_context
from app.computation.quant.ridge_sector_rank import build_callrank_context
from app.computation.valuation.residual_income_model import build_valuation_context
from app.db.base import get_db
from app.rendering.pdf_service import render_report_pdf_async
from app.sync.supabase_sync import sync_report_snapshot

router = APIRouter()


class ReportType(str, Enum):
    callrank = "callrank"
    metroguard = "metroguard"
    valuation = "valuation"


_CONTEXT_BUILDERS = {
    ReportType.callrank: build_callrank_context,
    ReportType.metroguard: build_metroguard_context,
    ReportType.valuation: build_valuation_context,
}


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

    # 서빙 캐시 동기화는 응답을 늦추지 않도록 백그라운드로 실행하고, 설정 안 됐으면
    # sync_report_snapshot 내부에서 조용히 스킵된다. pdf_url은 R2 연동 전까지 None.
    background_tasks.add_task(
        sync_report_snapshot,
        report_type=report_type.value,
        report_date=report_date,
        context=context,
        pdf_url=None,
    )

    filename = f"{report_type.value}_{report_date.isoformat()}.pdf"
    return StreamingResponse(
        pdf_buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
