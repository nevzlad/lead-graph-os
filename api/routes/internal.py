from fastapi import APIRouter, Depends, HTTPException, Query

from api.bigdata.analyzer import BigDataAnalyzer
from api.deps import verify_internal_token
from config import settings

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_internal_token)],
)


@router.get("/analytics")
async def get_analytics(tenant_id: str = Query(..., min_length=36, max_length=36)):
    analyzer = BigDataAnalyzer()
    data = await analyzer.aggregate_tenant_metrics(tenant_id)
    return {"mode": settings.MODE, "analytics": data}


@router.post("/export")
async def export_data(
    tenant_id: str = Query(..., min_length=36, max_length=36),
    format: str = "json",
):
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="Unsupported format")
    analyzer = BigDataAnalyzer()
    path = await analyzer.export_dataset(tenant_id, format)
    return {"status": "exported", "tenant_id": tenant_id, "path": path}
