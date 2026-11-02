import time
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.db.connection import get_engine

logger = logging.getLogger("text2sql.db.executor")


class QueryExecutionResult(BaseModel):
    """The structured result of executing a read query on the database."""
    success: bool
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    error_code: Optional[str] = None


class SafeSQLExecutor:
    """
    Executes validated SQL queries against PostgreSQL with database-level guardrails:
    - Enforces read-only transactions at driver and engine level
    - Limits maximum in-memory result rows
    - Records query latency
    - Traps PostgreSQL security exceptions (read-only violations, timeouts)
    - Logs raw errors internally for operators while returning sanitized errors to callers
    """

    def __init__(self, max_fetch_rows: int = 1000):
        self.max_fetch_rows = max_fetch_rows
        self.engine = get_engine()

    def execute(self, sql: str) -> QueryExecutionResult:
        start_time = time.perf_counter()

        try:
            with self.engine.connect().execution_options(postgresql_readonly=True) as conn:
                cursor_result = conn.execute(text(sql))

                if not cursor_result.returns_rows:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return QueryExecutionResult(
                        success=True,
                        row_count=0,
                        execution_time_ms=round(elapsed, 2),
                    )

                columns = list(cursor_result.keys())
                fetch_rows = cursor_result.fetchmany(self.max_fetch_rows)
                data = [dict(zip(columns, row)) for row in fetch_rows]
                elapsed = (time.perf_counter() - start_time) * 1000

                return QueryExecutionResult(
                    success=True,
                    columns=columns,
                    rows=data,
                    row_count=len(data),
                    execution_time_ms=round(elapsed, 2),
                )

        except (ProgrammingError, DBAPIError) as ex:
            elapsed = (time.perf_counter() - start_time) * 1000
            raw_err = str(ex.orig) if hasattr(ex, "orig") else str(ex)

            logger.error(
                "Database execution failure: %s | Query: %s",
                raw_err,
                sql,
                exc_info=True,
            )

            error_code = "DATABASE_EXECUTION_ERROR"
            safe_msg = "A database execution error occurred while processing the query."

            err_lower = raw_err.lower()
            if "read-only transaction" in err_lower:
                error_code = "READ_ONLY_VIOLATION"
                safe_msg = "Database blocked operation: Transaction is strictly READ-ONLY."
            elif "permission denied" in err_lower:
                error_code = "PERMISSION_DENIED"
                safe_msg = "Database blocked operation: Access to the requested resource is denied."
            elif "canceling statement due to statement timeout" in err_lower or "timeout" in err_lower:
                error_code = "QUERY_TIMEOUT"
                safe_msg = "Database canceled query: Execution exceeded the maximum allowed statement timeout."

            return QueryExecutionResult(
                success=False,
                execution_time_ms=round(elapsed, 2),
                error=safe_msg,
                error_code=error_code,
            )

        except Exception as ex:
            elapsed = (time.perf_counter() - start_time) * 1000
            logger.error("Unexpected executor error: %s | Query: %s", str(ex), sql, exc_info=True)
            return QueryExecutionResult(
                success=False,
                execution_time_ms=round(elapsed, 2),
                error="An unexpected internal error occurred during query execution.",
                error_code="UNEXPECTED_ERROR",
            )
