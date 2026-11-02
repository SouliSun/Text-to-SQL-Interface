import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.hallucination.schema_catalog import SchemaCatalogProvider
from app.llm.generator import SQLGenerator


def run_generator_test():
    print("=" * 60)
    print("TESTING LLM SQL GENERATOR (GROQ + SCHEMA GROUNDING)")
    print("=" * 60)

    provider = SchemaCatalogProvider()
    catalog = provider.get_catalog()
    print(f"Loaded schema with tables: {list(catalog.tables.keys())}\n")

    generator = SQLGenerator()

    test_questions = [
        "How many customers are from the USA?",
        "Show me all completed orders with customer names and total amounts sorted by highest amount first",
        "What is the average order total amount?",
    ]

    for q in test_questions:
        print("-" * 60)
        print(f"Question: \"{q}\"")
        generated_sql = generator.generate_sql(q, catalog)
        print("Generated SQL:\n" + generated_sql)


if __name__ == "__main__":
    run_generator_test()