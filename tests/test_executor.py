import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.executor import SafeSQLExecutor


@pytest.fixture(scope="module")
def executor():
    return SafeSQLExecutor(max_fetch_rows=100)


def test_executor_valid_read(executor):
    res = executor.execute("SELECT id, name, email FROM customers ORDER BY id LIMIT 5;")
    assert res.success is True
    assert res.row_count >= 1
    assert "name" in res.columns


def test_executor_db_blocks_update(executor):
    """Verifies that the PostgreSQL engine rejects modifying statements."""
    res = executor.execute("UPDATE customers SET country = 'Bypassed' WHERE id = 1;")
    assert res.success is False
    assert res.error_code in ("READ_ONLY_VIOLATION", "PERMISSION_DENIED", "DATABASE_EXECUTION_ERROR")


def test_executor_db_blocks_insert(executor):
    """Verifies that the PostgreSQL engine rejects direct insert statements."""
    res = executor.execute("INSERT INTO customers (name, email) VALUES ('Hacker', 'hacked@hack.com');")
    assert res.success is False
    assert res.error_code in ("READ_ONLY_VIOLATION", "PERMISSION_DENIED", "DATABASE_EXECUTION_ERROR")


def test_executor_statement_timeout(executor):
    """Verifies that socket statement_timeout=5000ms kills long queries."""
    res = executor.execute("SELECT pg_sleep(6);")
    assert res.success is False
    assert res.error_code == "QUERY_TIMEOUT"


def run_executor_tests():
    executor = SafeSQLExecutor(max_fetch_rows=100)

    print("=" * 60)
    print("TESTING SAFE SQL EXECUTOR (DATABASE-LEVEL PROTECTION)")
    print("=" * 60)

    print("\n[Test 1] Valid Read Query (SELECT from customers):")
    res1 = executor.execute("SELECT id, name, email, country FROM customers ORDER BY id LIMIT 5;")
    print(f"Success: {res1.success}")
    print(f"Rows returned: {res1.row_count}")

    print("\n[Test 2] Defense-in-Depth: Attempting UPDATE directly against DB:")
    res3 = executor.execute("UPDATE customers SET country = 'Bypassed' WHERE id = 1;")
    print(f"Success: {res3.success} (Should be False!)")
    print(f"Error Code: {res3.error_code}")

    print("\n[Test 3] Defense-in-Depth: Attempting INSERT directly against DB:")
    res4 = executor.execute("INSERT INTO customers (name, email) VALUES ('Hacker', 'hacked@hack.com');")
    print(f"Success: {res4.success} (Should be False!)")
    print(f"Error Code: {res4.error_code}")

    print("\n[Test 4] Statement Timeout Protection (pg_sleep 6s):")
    res5 = executor.execute("SELECT pg_sleep(6);")
    print(f"Success: {res5.success} (Should be False due to 5s statement_timeout!)")
    print(f"Error Code: {res5.error_code}")


if __name__ == "__main__":
    run_executor_tests()
