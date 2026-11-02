import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import TextToSQLPipeline


def run_pipeline_demo():
    print("=" * 70)
    print("TEXT-TO-SQL WITH GUARDRAILS & HALLUCINATION DETECTION (LIVE DEMO)")
    print("=" * 70)

    pipeline = TextToSQLPipeline()

    questions = [
        # 1. Aggregation / Count question
        "How many customers are in the USA?",

        # 2. Relational join with ordering
        "Show me all completed orders with the customer name and total amount, sorted by highest amount first",

        # 3. Calculation question
        "What is the average order total amount?",

        # 4. Hallucination test (asking for a table that does NOT exist in Supabase!)
        "Show me all user reviews and their star ratings",
    ]

    for q in questions:
        print("\n" + "-" * 70)
        print(f"User Question: \"{q}\"")
        resp = pipeline.query(q)

        print(f"Generated SQL: {resp.generated_sql}")
        print(f"Is Safe: {resp.is_safe} | Is Hallucinated: {resp.is_hallucinated}")
        print(f"Heuristic Alignment Score: {resp.heuristic_alignment_score}")

        if resp.errors:
            print(f"Errors Triggered: {resp.errors}")

        if resp.warnings:
            print(f"Warnings / Alignment Notes: {resp.warnings}")

        if resp.execution_result and resp.execution_result.success:
            print(f"Query Execution: SUCCESS ({resp.execution_result.execution_time_ms} ms)")
            print(f"Data Returned ({resp.execution_result.row_count} rows):")
            for row in resp.execution_result.rows:
                print(f"  {row}")


if __name__ == "__main__":
    run_pipeline_demo()