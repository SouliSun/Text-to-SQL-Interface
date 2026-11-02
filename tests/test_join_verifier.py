import sys
from pathlib import Path
import pytest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.guardrails.validator import SQLValidator
from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.hallucination.verifier import SchemaHallunicationVerifier


@pytest.fixture(scope="module")
def verifier():
    catalog = SchemaCatalogProvider().get_catalog()
    return SchemaHallunicationVerifier(catalog=catalog)


@pytest.fixture(scope="module")
def validator():
    return SQLValidator()


def test_disconnected_tables(validator, verifier):
    """Multiple tables in FROM without join conditions must be flagged as disconnected."""
    sql = "SELECT c.name, p.name FROM customers c, products p"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is True
    assert any(m.item_type == "DISCONNECTED_QUERY_JOIN_GRAPH" for m in report.schema_mismatches)


def test_valid_multi_hop_join(validator, verifier):
    """4-table relational join along customer -> order -> order_items -> products is valid."""
    sql = """
    SELECT c.name, p.name, oi.quantity
    FROM customers c
    JOIN orders o ON c.id = o.customer_id
    JOIN order_items oi ON o.id = oi.order_id
    JOIN products p ON oi.product_id = p.id
    """
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is False
    assert len(report.schema_mismatches) == 0


def test_wrong_fk_columns(validator, verifier):
    """Joining customers and orders on c.id = o.id is an invalid join condition."""
    sql = "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON c.id = o.id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is True
    assert any(m.item_type == "INVALID_JOIN_CONDITION" for m in report.schema_mismatches)


def test_table_aliases_resolution(validator, verifier):
    """Custom aliases (cust, ord) must resolve to underlying tables and validate."""
    sql = "SELECT cust.name, ord.total_amount FROM customers cust JOIN orders ord ON cust.id = ord.customer_id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is False
    assert len(report.schema_mismatches) == 0


def test_using_syntax_rejects_non_shared_or_invalid_fk(validator, verifier):
    """
    JOIN ... USING requires the column to exist in both tables and represent an FK.
    In our schema, 'order_id' exists on order_items but NOT orders (which uses 'id'),
    so USING (order_id) fails to connect orders.
    """
    sql = "SELECT * FROM orders JOIN order_items USING (order_id)"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is True
    assert any(m.item_type in ("DISCONNECTED_QUERY_JOIN_GRAPH", "INVALID_JOIN_CONDITION", "COLUMN") for m in report.schema_mismatches)


def test_equality_inside_or_not_treated_as_join(validator, verifier):
    """
    An equality predicate inside an OR block ('WHERE c.country = 'USA' OR c.id = o.customer_id')
    does not guarantee an equi-join; the tables must remain disconnected in the join graph.
    """
    sql = "SELECT c.name, o.total_amount FROM customers c, orders o WHERE c.country = 'USA' OR c.id = o.customer_id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is True
    assert any(m.item_type == "DISCONNECTED_QUERY_JOIN_GRAPH" for m in report.schema_mismatches)


def test_invalid_direct_fk_relationship(validator, verifier):
    """Directly joining customers to categories lacks a foreign key in the schema."""
    sql = "SELECT c.name, cat.name FROM customers c JOIN categories cat ON c.id = cat.id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)

    assert report.is_hallucinated is True
    assert any(m.item_type == "INVALID_JOIN_RELATIONSHIP" for m in report.schema_mismatches)
