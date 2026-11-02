import json

with open("evals/eval_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

failed = [c for c in data["cases"] if not c["passed"]]
print(f"Total Failed Cases: {len(failed)}\n")
for c in failed:
    print(f"[{c['id']}] Category: {c['category']}")
    print(f"  Question: {c['question']}")
    print(f"  SQL: {c.get('generated_sql')}")
    print(f"  Errors: {c.get('errors')}")
    print(f"  Warnings: {c.get('warnings')}")
    print("-" * 60)

