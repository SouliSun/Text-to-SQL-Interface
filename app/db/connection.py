import os
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from sqlalchemy import create_engine, Engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

logger = logging.getLogger("text2sql.db.connection")

DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """
    Returns a hardened, pooled SQLAlchemy Engine.
    Directly injects statement timeouts, memory caps, and read-only
    transaction parameters via PostgreSQL driver options.
    """
    global _engine
    if _engine is not None:
        return _engine

    if not DATABASE_URL:
        raise ValueError(
            "DATABASE_URL is not set in environment or .env file. "
            "Please provide a valid PostgreSQL connection string."
        )

    _engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": 10,
            # User-level parameters: statement_timeout, read-only mode, and memory sort caps
            "options": (
                "-c statement_timeout=5000ms "
                "-c default_transaction_read_only=on "
                "-c work_mem=4MB"
            ),
        },
        execution_options={
            "postgresql_readonly": True,
        },
    )

    return _engine


def verify_deployment_security() -> Dict[str, Any]:
    """
    Directly queries PostgreSQL catalog and runtime settings to PROVE
    that the connected session and role are genuinely restricted.
    """
    engine = get_engine()
    verification_query = text("""
        SELECT 
            current_user,
            session_user,
            current_database(),
            current_setting('transaction_read_only') AS is_read_only,
            current_setting('statement_timeout') AS statement_timeout,
            current_setting('work_mem') AS work_mem,
            (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser,
            (SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user) AS can_create_db,
            has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_in_public;
    """)

    try:
        with engine.connect().execution_options(postgresql_readonly=True) as conn:
            row = conn.execute(verification_query).mappings().fetchone()
            if not row:
                return {"verified": False, "error": "No configuration row returned."}

            is_super = bool(row["is_superuser"])
            can_create = bool(row["can_create_in_public"])
            can_create_db = bool(row["can_create_db"])
            is_ro = str(row["is_read_only"]).lower() == "on"
            timeout = str(row["statement_timeout"]).lower()
            work_mem = str(row["work_mem"]).lower()

            # Tightened deployment assertions:
            # 1. Non-superuser role with no schema creation and no database creation privileges
            # 2. Dedicated read-only role or transaction read-only enforced
            # 3. Connection-level statement timeout enforced (5s / 5000ms)
            # 4. Connection-level work_mem memory cap enforced (4MB / 4096kB)
            role_restricted = (not is_super) and (not can_create) and (not can_create_db)
            timeout_restricted = "5000" in timeout or "5s" in timeout
            memory_restricted = "4mb" in work_mem or "4096kb" in work_mem or "4096" in work_mem

            passed_all = role_restricted and is_ro and timeout_restricted and memory_restricted

            return {
                "verified": passed_all,
                "current_user": row["current_user"],
                "is_superuser": is_super,
                "can_create_in_public": can_create,
                "can_create_db": can_create_db,
                "transaction_read_only": row["is_read_only"],
                "statement_timeout": row["statement_timeout"],
                "work_mem": row["work_mem"],
            }
    except SQLAlchemyError as ex:
        logger.error("Deployment security verification failed: %s", str(ex), exc_info=True)
        return {
            "verified": False,
            "error": "Failed to connect to PostgreSQL database or verify security settings.",
        }


def test_connection() -> dict:
    return verify_deployment_security()
