import sys
from pathlib import Path
import pytest

# Add project root to sys.path so 'app' can be imported when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.guardrails.validator import SQLValidator


@pytest.fixture(scope="module")
def validator():
    return SQLValidator()


def test_mutation_direct_delete_blocked(validator):
    res = validator.validate("DELETE FROM users WHERE id = 5")
    assert res.is_valid is False
    assert any(e.code == "MUTATION_DETECTED" or "DELETE" in e.message for e in res.errors)


def test_mutation_multi_statement_blocked(validator):
    res = validator.validate("SELECT * FROM users; DROP TABLE orders;")
    assert res.is_valid is False
    assert any(e.code == "MULTI_STATEMENT_QUERY_DETECTED" for e in res.errors)


def test_mutation_stealth_writable_cte_blocked(validator):
    res = validator.validate("WITH d AS (DELETE FROM audit_logs RETURNING *) SELECT count(*) FROM d")
    assert res.is_valid is False
    assert any(e.code == "MUTATION_DETECTED" for e in res.errors)


def test_dangerous_function_pg_sleep_blocked(validator):
    res = validator.validate("SELECT pg_sleep(10)")
    assert res.is_valid is False
    assert any(e.code == "DANGEROUS_FUNCTION_DETECTED" for e in res.errors)


def test_large_limit_clamped(validator):
    res = validator.validate("SELECT * FROM products LIMIT 50000")
    assert res.is_valid is True
    assert "LIMIT 1000" in res.sanitized_sql


def test_missing_limit_injected(validator):
    res = validator.validate("SELECT id, name FROM customers")
    assert res.is_valid is True
    assert "LIMIT 100" in res.sanitized_sql


def test_markdown_fence_unwrapped(validator):
    res = validator.validate("```sql\nSELECT title, price FROM products\n```")
    assert res.is_valid is True
    assert "```" not in res.sanitized_sql


if __name__ == "__main__":
    v = SQLValidator()
    print("Testing mutations directly against SQLValidator...")
    res = v.validate("DELETE FROM users WHERE id = 5")
    print("DELETE blocked?", not res.is_valid)
    res2 = v.validate("SELECT pg_sleep(10)")
    print("pg_sleep blocked?", not res2.is_valid)
