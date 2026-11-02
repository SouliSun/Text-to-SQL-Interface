import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from langfuse import observe, get_client

from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.hallucination.semantic_scorer import SemanticAlignmentScorer
from app.llm.generator import SQLGenerator
from app.guardrails.validator import SQLValidator
from app.hallucination.verifier import SchemaHallunicationVerifier
from app.db.executor import SafeSQLExecutor, QueryExecutionResult

logger = logging.getLogger("text2sql.pipeline")


class TextToSQLResponse(BaseModel):
    question: str
    generated_sql: Optional[str] = None
    sanitized_sql: Optional[str] = None
    is_safe: bool = False
    is_hallucinated: bool = False
    heuristic_alignment_score: float = 0.0  
    is_semantically_aligned: bool = False  
    execution_result: Optional[QueryExecutionResult] = None
    attempts: int = 1
    was_healed: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TextToSQLPipeline:
    def __init__(self):
        self.catalog_provider = SchemaCatalogProvider()
        self.generator = SQLGenerator()
        self.validator = SQLValidator()
        self.executor = SafeSQLExecutor()

    @observe(name="text_to_sql_pipeline")
    def query(self, question: str) -> TextToSQLResponse:
        max_attempts = 2
        generated_sql = None
        feedback = None
        catalog = self.catalog_provider.get_catalog()

        val_result = None
        sem_result = None

        for attempt in range(1, max_attempts + 1):
            warnings = []

            try:
                if attempt == 1:
                    generated_sql = self.generator.generate_sql(question, catalog)
                else:
                    logger.info("Self-healing retry (attempt %d) for question '%s'", attempt, question)
                    generated_sql = self.generator.regenerate_with_feedback(question, generated_sql, feedback, catalog)
            except Exception as e:
                logger.error("LLM Generation failed for question '%s': %s", question, str(e), exc_info=True)
                return TextToSQLResponse(
                    question=question,
                    errors=["The query generation service is temporarily unavailable. Please try again later."]
                )

            val_result = self.validator.validate(generated_sql)
            if not val_result.is_valid:
                logger.warning(
                    "Query rejected by AST Guardrails (attempt %d) for question '%s': %s",
                    attempt,
                    question,
                    [e.code for e in val_result.errors]
                )
                is_attack = any(
                    e.code in ["MUTATION_DETECTED", "DANGEROUS_FUNCTION_DETECTED", "MULTI_STATEMENT_QUERY_DETECTED"]
                    for e in val_result.errors
                )
                if is_attack or attempt == max_attempts:
                    return TextToSQLResponse(
                        question=question,
                        generated_sql=generated_sql,
                        is_safe=False,
                        heuristic_alignment_score=0.0,
                        is_semantically_aligned=False,
                        errors=[f"[{e.code}] {e.message}" for e in val_result.errors],
                        warnings=val_result.warnings
                    )
                feedback = f"AST Guardrail Syntax/Structure Error: {val_result.errors[0].message}"
                continue

            warnings.extend(val_result.warnings)

            verifier = SchemaHallunicationVerifier(catalog=catalog)
            hallucination_report = verifier.verify(val_result)

            if hallucination_report.is_hallucinated:
                mismatch_msgs = []
                for m in hallucination_report.schema_mismatches:
                    suggestion = f"(Suggested: '{m.suggested_correction}')" if m.suggested_correction else ""
                    mismatch_msgs.append(f"Hallucinated {m.item_type}: '{m.hallucinated_name}' {suggestion}".strip())
                
                logger.info("Hallucination detected (attempt %d) for question '%s': %s", attempt, question, mismatch_msgs)
                if attempt == max_attempts:
                    return TextToSQLResponse(
                        question=question,
                        generated_sql=generated_sql,
                        sanitized_sql=val_result.sanitized_sql,
                        is_safe=True,
                        is_hallucinated=True,
                        heuristic_alignment_score=0.0,
                        is_semantically_aligned=False,
                        errors=mismatch_msgs,
                        warnings=warnings
                    )
                m0 = hallucination_report.schema_mismatches[0]
                feedback = f"Schema Mismatch in {m0.item_type} '{m0.hallucinated_name}': {m0.suggested_correction or 'Check schema for real tables and columns.'}"
                continue
            
            semantic_scorer = SemanticAlignmentScorer()
            sem_result = semantic_scorer.evaluate(question, val_result.sanitized_sql)

            warnings.extend(sem_result.warnings)
            warnings.extend(sem_result.mismatches)

            if not sem_result.is_aligned:
                logger.warning("Query gated by semantic intent scorer (attempt %d) for question '%s': %s", attempt, question, sem_result.mismatches)
                if attempt == max_attempts:
                    return TextToSQLResponse(
                        question=question,
                        generated_sql=generated_sql,
                        sanitized_sql=val_result.sanitized_sql,
                        is_safe=True,
                        is_hallucinated=False,
                        heuristic_alignment_score=sem_result.heuristic_alignment_score,
                        is_semantically_aligned=False,
                        errors=[f"Semantic Alignment Failure: {m}" for m in sem_result.mismatches],
                        warnings=warnings,
                    )
                feedback = f"Semantic Intent Error: {sem_result.mismatches[0]}"
                continue

            break

        exec_result = self.executor.execute(val_result.sanitized_sql)

        if not exec_result.success:
            err_code = exec_result.error_code or "DATABASE_EXECUTION_ERROR"
            generic_error_map = {
                "READ_ONLY_VIOLATION": "Database transaction aborted: modifying operations are prohibited.",
                "PERMISSION_DENIED": "Database permission denied for the requested query.",
                "QUERY_TIMEOUT": "Database query timed out: execution exceeded the allowed duration.",
                "DATABASE_EXECUTION_ERROR": "A database error occurred while executing the query.",
                "UNEXPECTED_ERROR": "An internal database error occurred during execution.",
            }
            sanitized_message = generic_error_map.get(
                err_code,
                "A database error occurred while executing the query."
            )
            logger.error(
                "Database error for question '%s' [%s]: %s",
                question,
                err_code,
                exec_result.error,
            )
            exec_result.error = sanitized_message

            return TextToSQLResponse(
                question=question,
                generated_sql=generated_sql,
                sanitized_sql=val_result.sanitized_sql,
                is_safe=True,
                is_hallucinated=False,
                heuristic_alignment_score=sem_result.heuristic_alignment_score,
                is_semantically_aligned=True,
                execution_result=exec_result,
                errors=[f"[{err_code}] {sanitized_message}"],
                warnings=warnings,
            )
        try:
            client = get_client()
            client.score_current_trace(
                name="execution_success",
                value=1.0 if exec_result.success else 0.0,
                comment="Layer 4 Execution Status"
            )
            client.score_current_trace(
                name="is_safe",
                value=1.0 if val_result.is_valid else 0.0,
                comment="Layer 1 AST Guardrail Status"
            )
            client.score_current_trace(
                name="is_hallucinated",
                value=0.0 if not hallucination_report.is_hallucinated else 1.0,
                comment="Layer 3A Schema Verifier Status"
            )
            client.score_current_trace(
                name="semantic_alignment",
                value=sem_result.heuristic_alignment_score,
                comment="Layer 3B Heuristic Intent Score"
            )
            client.score_current_trace(
                name="was_healed",
                value=1.0 if attempt > 1 else 0.0,
                comment="Self-Healing Loop Triggered"
            )
        except Exception as e:
            logger.debug("Langfuse scoring skipped: %s", e)

        return TextToSQLResponse(
            question=question,
            generated_sql=generated_sql,
            sanitized_sql=val_result.sanitized_sql,
            is_safe=True,
            is_hallucinated=False,
            heuristic_alignment_score=sem_result.heuristic_alignment_score,
            is_semantically_aligned=True,
            execution_result=exec_result,
            attempts=attempt,
            was_healed=(attempt > 1),
            warnings=warnings,
        )
