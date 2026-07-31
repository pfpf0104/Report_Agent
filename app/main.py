import logging

from fastapi import Depends, FastAPI

from app.api.routers import health_router, ingestion_router, reports_router
from app.core.config import settings
from app.core.security import verify_api_key
from app.rendering.pdf_service import shutdown_executor

logger = logging.getLogger("app")

app = FastAPI(title="Financial Report Automation Pipeline")

if settings.api_key == "changeme":
    logger.warning(
        "REPORT_AGENT_API_KEY가 기본값(changeme)입니다. "
        "Cloudflare Tunnel로 외부에 노출하기 전에 .env에서 반드시 변경하세요."
    )

app.include_router(health_router.router, prefix="/health", tags=["health"])
app.include_router(
    ingestion_router.router,
    prefix="/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    reports_router.router,
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(verify_api_key)],
)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    shutdown_executor()
