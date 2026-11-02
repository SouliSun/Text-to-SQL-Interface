import sys
from pathlib import Path
import pytest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.guardrails.validator import SQLValidator
from app.hallucination.semantic_scorer import SemanticAlignmentScorer
from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.hallucination.verifier import SchemaHallunicationVerifier
from app.pipeline import TextToSQLPipeline


def test_tc024_dummy_query_blocked_by_guardrails():
    """
    Regression test for TC-024 evasion attempt:
    The model returned `SELECT ''::text AS supplier, ''::text AS product WHERE FALSE;`
    which lacks a FROM clause and contains a dummy false predicate.
    """
    validator = SQLValidator()
    evasion_sql = "SELECT ''::text AS supplier, ''::text AS product WHERE FALSE;"
    val_res = validator.validate(evasion_sql)

    assert val_res.is_valid is False
    error_codes = [e.code for e in val_res.errors]
    assert "MISSING_FROM_CLAUSE" in error_codes or "DUMMY_QUERY_DETECTED" in error_codes


def test_dummy_constant_false_predicate_blocked():
    """
    Ensures any query with a constant false predicate (e.g. WHERE FALSE or WHERE 1 = 0)
    is caught and blocked.
    """
    validator = SQLValidator()

    for dummy_sql in [
        "SELECT id, name FROM customers WHERE FALSE",
        "SELECT id, name FROM customers WHERE 1 = 0",
    ]:
        val_res = validator.validate(dummy_sql)
        assert val_res.is_valid is False
        assert any(e.code == "DUMMY_QUERY_DETECTED" for e in val_res.errors)


def test_tc026_dropped_filter_blocked_by_semantic_gating():
    """
    Regression test for TC-026 evasion attempt:
    Question: 'Find customers by their phone_number and show their names.'
    Evasion SQL: 'SELECT name FROM customers;' (model dropped the filter condition because phone_number doesn't exist).
    The pipeline must detect missing FILTER intent and gate execution.
    """
    question = "Find customers by their phone_number and show their names."
    stripped_sql = "SELECT name FROM customers;"

    scorer = SemanticAlignmentScorer()
    res = scorer.evaluate(question, stripped_sql)

    assert "FILTER" in res.intent_signals
    assert "FILTER" not in res.sql_features
    assert res.is_aligned is False
    assert any("FILTER" in m or "filter" in m.lower() for m in res.mismatches)


def test_tc026_phone_number_hallucination_caught_if_generated():
    """
    If the LLM actually includes phone_number in the SQL:
    'SELECT name FROM customers WHERE phone_number = 123;'
    Layer 3A Schema Verifier must flag 'phone_number' as a COLUMN hallucination.
    """
    validator = SQLValidator()
    catalog = SchemaCatalogProvider().get_catalog()
    verifier = SchemaHallunicationVerifier(catalog=catalog)

    sql = "SELECT name FROM customers WHERE phone_number = '555-1234'"
    val_res = validator.validate(sql)
    rep = verifier.verify(val_res)

    assert rep.is_hallucinated is True
    assert any(m.item_type == "COLUMN" and m.hallucinated_name == "phone_number" for m in rep.schema_mismatches)


def test_tc027_mocked_null_columns_caught():
    """
    Regression test for TC-027 evasion attempt:
    Question: "Show each employee's name, age, and zip_code."
    Evasion SQL: SELECT e.name, NULL::INTEGER AS age, NULL::VARCHAR AS zip_code FROM employees e;
    Layer 3A Schema Verifier must detect mocked NULL projections for nonexistent columns.
    """
    validator = SQLValidator()
    catalog = SchemaCatalogProvider().get_catalog()
    verifier = SchemaHallunicationVerifier(catalog=catalog)

    sql = """
    SELECT
      e.name,
      NULL::INTEGER AS age,
      NULL::VARCHAR AS zip_code
    FROM employees e;
    """
    val_res = validator.validate(sql)
    rep = verifier.verify(val_res)

    assert rep.is_hallucinated is True
    mismatched_names = [m.hallucinated_name for m in rep.schema_mismatches]
    assert "age" in mismatched_names
    assert "zip_code" in mismatched_names
    assert any(m.item_type == "MOCKED_NULL_COLUMN" for m in rep.schema_mismatches)


def test_tc134_mocked_literal_columns_caught():
    """
    Regression test for TC-134/TC-135 evasion attempts:
    Evasion SQL: SELECT o.*, 'N/A' AS tracking_number FROM orders o;
    Layer 3A Schema Verifier must detect fake string literals aliased to ungrounded columns.
    """
    validator = SQLValidator()
    catalog = SchemaCatalogProvider().get_catalog()
    verifier = SchemaHallunicationVerifier(catalog=catalog)

    sql = "SELECT o.id, 'N/A' AS tracking_number FROM orders o;"
    val_res = validator.validate(sql)
    rep = verifier.verify(val_res)

    assert rep.is_hallucinated is True
    mismatched_names = [m.hallucinated_name for m in rep.schema_mismatches]
    assert "tracking_number" in mismatched_names
    assert any(m.item_type == "MOCKED_LITERAL_COLUMN" for m in rep.schema_mismatches)
