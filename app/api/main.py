from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")

app = FastAPI(
    title="Text-to-SQL Guardrail API",
    description="Natural language to SQL interface with multi-layer AST guardrails and hallucination detection.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
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