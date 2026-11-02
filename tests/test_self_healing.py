import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from app.llm.generator import SQLGenerator
from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.pipeline import TextToSQLPipeline


def test_generator_self_heals_invalid_join_condition():
    """
    Directly tests that regenerate_with_feedback receives the verifier's
    FK error message and corrects an invalid join condition.
    """
    generator = SQLGenerator()
    catalog = SchemaCatalogProvider().get_catalog()

    failed_sql = "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON c.id = o.id;"
    feedback = (
        "Schema Mismatch in INVALID_JOIN_CONDITION 'c.id = o.id': "
        "Columns 'id' and 'id' do not form a foreign key constraint between 'customers' and 'orders'. "
        "Expected foreign key relation: customers.id = orders.customer_id"
    )

    healed_sql = generator.regenerate_with_feedback(
        question="Show customer name and order total for all orders",
        failed_sql=failed_sql,
        feedback=feedback,
        catalog=catalog,
    )

    assert "customer_id" in healed_sql
    assert "c.id = o.customer_id" in healed_sql or "o.customer_id = c.id" in healed_sql
