import sys
from pathlib import Path
import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.guardrails.validator import SQLValidator
from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.hallucination.verifier import SchemaHallunicationVerifier


@pytest.fixture(scope="module")
def verifier():
    catalog_provider = SchemaCatalogProvider()
    catalog = catalog_provider.get_catalog()
    return SchemaHallunicationVerifier(catalog=catalog)


@pytest.fixture(scope="module")
def validator():
    return SQLValidator()


def test_clean_query(validator, verifier):
    val_res = validator.validate("SELECT id, name, email FROM customers WHERE country = 'USA'")
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is False
    assert len(report.schema_mismatches) == 0


def test_hallucinated_table(validator, verifier):
    val_res = validator.validate("SELECT id, name FROM clients")
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is True
    assert any(m.item_type == "TABLE" and m.hallucinated_name == "clients" for m in report.schema_mismatches)


def test_hallucinated_column(validator, verifier):
    val_res = validator.validate("SELECT id, name, phone_number FROM customers")
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is True
    assert any(m.item_type == "COLUMN" and m.hallucinated_name == "phone_number" for m in report.schema_mismatches)


def test_valid_join_fk_path(validator, verifier):
    sql = "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON c.id = o.customer_id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is False


def test_disconnected_query_join(validator, verifier):
    # Multiple tables with no join clauses (accidental Cartesian product)
    sql = "SELECT c.name, p.name FROM customers c, products p"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is True
    assert any(m.item_type == "DISCONNECTED_QUERY_JOIN_GRAPH" for m in report.schema_mismatches)


def test_invalid_join_condition(validator, verifier):
    # Tables have an FK, but the join equates wrong columns (c.id = o.id)
    sql = "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON c.id = o.id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is True
    assert any(m.item_type == "INVALID_JOIN_CONDITION" for m in report.schema_mismatches)


def test_invalid_join_relationship(validator, verifier):
    # Tables do not share a direct FK in the database schema
    sql = "SELECT c.name, cat.name FROM customers c JOIN categories cat ON c.id = cat.id"
    val_res = validator.validate(sql)
    assert val_res.is_valid is True
    report = verifier.verify(val_res)
    assert report.is_hallucinated is True
    assert any(m.item_type == "INVALID_JOIN_RELATIONSHIP" for m in report.schema_mismatches)


def run_hallucination_tests():
    print("=" * 65)
    print("TESTING LAYER 3: SCHEMA HALLUCINATION DETECTOR")
    print("=" * 65)

    validator = SQLValidator()
    catalog_provider = SchemaCatalogProvider()
    catalog = catalog_provider.get_catalog()
    verifier = SchemaHallunicationVerifier(catalog=catalog)

    print(f"Live Catalog Loaded: {list(catalog.tables.keys())}\n")

    test_scenarios = [
        ("Clean Query (No Hallucinations)", "SELECT id, name, email FROM customers WHERE country = 'USA'"),
        ("Hallucinated Table ('clients')", "SELECT id, name FROM clients"),
        ("Hallucinated Column ('phone_number')", "SELECT id, name, phone_number FROM customers"),
        ("Disconnected Query Join", "SELECT c.name, p.name FROM customers c, products p"),
        ("Invalid Join Condition (c.id = o.id)", "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON c.id = o.id"),
        ("Invalid Direct Join (customers JOIN categories)", "SELECT c.name, cat.name FROM customers c JOIN categories cat ON c.id = cat.id"),
    ]

    for label, query in test_scenarios:
        print("-" * 65)
        print(f"Scenario: {label}")
        print(f"SQL: {query}")

        val_res = validator.validate(query)
        if not val_res.is_valid:
            print("[FAIL] Layer 1 rejected query:")
            for err in val_res.errors:
                print(f"  [{err.code}] {err.message}")
            continue

        report = verifier.verify(val_res)
        print(f"Is Hallucinated? {'[BLOCKED] YES' if report.is_hallucinated else '[PASS] NO (SAFE TO RUN)'}")
        print(f"Confidence Score: {report.confidence_score}")

        if report.schema_mismatches:
            print("Detected Mismatches:")
            for m in report.schema_mismatches:
                suggestion = f" -> Suggested: '{m.suggested_correction}'" if m.suggested_correction else " (No suggestion)"
                ctx = f" on '{m.context}'" if m.context else ""
                print(f"  - [{m.item_type}] '{m.hallucinated_name}'{ctx}{suggestion}")


if __name__ == "__main__":
    run_hallucination_tests()
