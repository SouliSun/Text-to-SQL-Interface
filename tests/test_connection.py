import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.connection import verify_deployment_security


def test_database_connection():
    status = verify_deployment_security()
    assert status.get("verified") is True
    assert status.get("current_user") == "text2sql_readonly"
    assert status.get("is_superuser") is False
    assert status.get("can_create_in_public") is False
    assert status.get("can_create_db") is False
    assert status.get("transaction_read_only") == "on"
    assert "5s" in str(status.get("statement_timeout")) or "5000" in str(status.get("statement_timeout"))
    assert "4MB" in str(status.get("work_mem")) or "4096" in str(status.get("work_mem"))


if __name__ == "__main__":
    print("Testing connection to Supabase...")
    sec = verify_deployment_security()
    if sec.get("verified"):
        print("[SUCCESS] Connected to PostgreSQL with verified security!")
        print(f"  User: {sec.get('current_user')}")
        print(f"  Superuser: {sec.get('is_superuser')}")
        print(f"  Read-Only Mode: {sec.get('transaction_read_only')}")
        print(f"  Statement Timeout: {sec.get('statement_timeout')}")
    else:
        print("[FAILED] Security verification failed:")
        print(f"  Details: {sec}")
