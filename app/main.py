from fastapi import FastAPI

from app.api.routers import health_router, ingestion_router, reports_router

app = FastAPI(title="Financial Report Automation Pipeline")

app.include_router(health_router.router, prefix="/health", tags=["health"])
app.include_router(ingestion_router.router, prefix="/ingestion", tags=["ingestion"])
app.include_router(reports_router.router, prefix="/reports", tags=["reports"])
