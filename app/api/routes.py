from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any
from app.pipeline import TextToSQLPipeline, TextToSQLResponse
from app.db.connection import verify_deployment_security
from app.hallucination.schema_catalog import SchemaCatalogProvider
from .auth import current_user

router = APIRouter()
pipeline = TextToSQLPipeline()

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, description="Natural language question to translate into SQL")

@router.get("/health", tags=["Health"])
def health_check() -> Dict[str, Any]:
    security_status = verify_deployment_security()
    if not security_status.get("verified"):
        raise HTTPException(status_code=500, detail="Deployment security verification failed.")
    return {"status": "healthy", "security": security_status}

@router.post("/query", response_model=TextToSQLResponse, tags=["Query"])
def run_query(request: QueryRequest, user: dict = Depends(current_user)) -> TextToSQLResponse:
    response = pipeline.query(request.question)
    return response

@router.get("/schema", tags=["Schema"])
def get_schema() -> Dict[str, Any]:
    catalog = SchemaCatalogProvider().get_catalog()
    return {
        "tables": {
            name: [col.name for col in table.columns.values()]
            for name, table in catalog.tables.items()
        }
    }

