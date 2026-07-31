from datetime import date
from enum import Enum

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.computation.fixed_income.duration_controller import build_metroguard_context
from app.computation.quant.ridge_sector_rank import build_callrank_context
from app.computation.valuation.residual_income_model import build_valuation_context
from app.db.base import get_db
from app.rendering.pdf_service import render_report_pdf_async

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
async def stream_report(report_type: ReportType, report_date: date, db: Session = Depends(get_db)):
    builder = _CONTEXT_BUILDERS[report_type]
    context = builder(db=db, as_of=report_date)
    pdf_buf = await render_report_pdf_async(f"{report_type.value}/report.html", context)

    filename = f"{report_type.value}_{report_date.isoformat()}.pdf"
    return StreamingResponse(
        pdf_buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
