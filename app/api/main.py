from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from fastapi.staticfiles import StaticFiles
from pathlib import Path
app = FastAPI(
    title="Text-to-SQL Guardrail API",
    description="Natural language to SQL interface with multi-layer AST guardrails and hallucination detection.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).resolve().parents[1] / "frontend",
        html=True,
    ),
    name="frontend",
)