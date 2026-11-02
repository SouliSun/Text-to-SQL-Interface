# Text-to-SQL Interface

Text-to-SQL Interface is a Python service that turns natural-language questions into PostgreSQL read queries. It grounds generation in the connected database schema, validates the generated SQL, checks tables and joins, and applies database-side read-only and resource limits before execution.

The project is an evaluation prototype. Its heuristic intent checks and benchmark results should not be treated as a security certification or a guarantee that generated answers are correct.

## Features

- Natural-language-to-SQL generation using Groq's OpenAI-compatible API.
- Live schema and foreign-key introspection through SQLAlchemy.
- SQL parsing and validation with SQLGlot, including read-query restrictions, multi-statement checks, dangerous function checks, and row-limit enforcement.
- Schema, column, and join verification, with heuristic checks for question intent.
- A bounded correction attempt for some validation failures; corrected SQL passes through validation again.
- PostgreSQL connection settings for read-only transactions, a 5-second statement timeout, and a 4 MB `work_mem` cap.
- FastAPI endpoints for querying, health checks, schema inspection, and liveness.
- Optional Langfuse tracing and scoring.

## How a query is handled

1. The service introspects the connected database schema and foreign keys.
2. The schema and question are sent to the configured language model.
3. SQLGlot parses the response and the validator rejects disallowed query structures and applies a row limit.
4. The schema verifier checks referenced tables, columns, and join relationships. A heuristic scorer checks selected intent signals.
5. Some validation failures may trigger one correction attempt. The corrected query is validated again.
6. Accepted SQL is executed through SQLAlchemy, and the API returns a structured response.

The database account and connection settings are important safeguards, but the application should still be deployed behind appropriate network and API controls. The in-memory rate limiter is intended for a single process; use a shared rate-limit store and an API gateway for multi-worker or public deployments.

## Requirements

- Python 3.12 or later
- PostgreSQL database credentials with only the access the application needs
- Groq API key
- Optional Langfuse account and keys for tracing

## Setup

Clone the repository and install dependencies in a virtual environment:

```bash
git clone <your-repository-url>
cd Text-SQL
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install the project dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your credentials. Do not commit `.env` or real credentials.

```env
DATABASE_URL=postgresql+psycopg://readonly_user:password@host:5432/database?sslmode=require
GROQ_API_KEY=your_groq_api_key
API_ACCESS_TOKEN=your_long_random_api_token
FRONTEND_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

# Optional Langfuse tracing
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# Optional; defaults to openai/gpt-oss-120b
GROQ_MODEL=openai/gpt-oss-120b
```

Use a dedicated PostgreSQL role with the minimum required privileges. The connection config requests read-only transactions and applies statement and memory limits; verify the actual role grants and settings in your own deployment.

## Run the API

From the repository root:

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs).

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/query` | Bearer token | Generate, validate, and execute a query |
| `GET` | `/api/v1/health` | Bearer token | Check database connection security settings |
| `GET` | `/api/v1/schema` | Bearer token | Return introspected table and column names |
| `GET` | `/api/v1/live` | None | Minimal liveness response |

Example request:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/query \
  -H "Authorization: Bearer YOUR_API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"Show the five most expensive products"}'
```

The query endpoint accepts questions up to 2,000 characters. API authentication must be configured with `API_ACCESS_TOKEN`; browser origins are controlled by the comma-separated `FRONTEND_ORIGINS` setting.

## Python usage

```python
from app.pipeline import TextToSQLPipeline

pipeline = TextToSQLPipeline()
response = pipeline.query("Show completed orders with customer names")

print("Generated SQL:", response.generated_sql)
print("Accepted by SQL validation:", response.is_safe)
print("Schema issue detected:", response.is_hallucinated)
print("Correction attempted:", response.was_healed)

if response.execution_result and response.execution_result.success:
    for row in response.execution_result.rows:
        print(row)
else:
    print(response.errors)
```

## Evaluation

The repository includes a 150-case dataset at [`data/evals_dataset.json`](data/evals_dataset.json), an evaluation harness at [`evals/evaluation.py`](evals/evaluation.py), and a saved run at [`evals/eval_results.json`](evals/eval_results.json). The README previously reported these results for that saved run:

- 87 of 90 legitimate analytical queries executed successfully (96.7%).
- 141 of 150 cases were classified as contained (94.0%) under the project's evaluation taxonomy.
- 2 cases were classified as unexpected execution, including benign synonym mappings.
- Adversarial cases frequently ended in model refusal or safe rewrite, rather than a downstream guardrail block.

These are results from a particular model, prompt, schema, and dataset run. They are not a general security guarantee. Review the evaluation taxonomy and raw results before drawing conclusions; rerun the harness against your own configuration before relying on these numbers.

## Repository layout

```text
app/
  api/             FastAPI routes, authentication, and middleware
  db/              PostgreSQL connection and query execution
  guardrails/      SQL parsing, validation, and query limits
  hallucination/   Schema catalog, join checks, and intent heuristics
  llm/             Groq-backed SQL generation
  pipeline.py      End-to-end query pipeline
data/              Evaluation dataset
evals/             Evaluation harness and saved results
tests/             Unit and integration tests
requirements.txt   Python dependencies
```

## Security and deployment notes

- Use a dedicated, least-privilege PostgreSQL role. Do not connect with an owner or superuser account.
- Keep `.env` and all credentials out of version control. Rotate any credential that has been exposed.
- The configured read-only transaction, statement timeout, and `work_mem` settings are defense-in-depth measures. Confirm permissions and runtime settings against your database provider.
- The in-memory request limiter does not coordinate across processes or machines. Add a shared limiter and edge protection before multi-worker or public deployment.
- The semantic scorer is heuristic and may miss intent mismatches or reject valid questions.
- Model output can be wrong. Validate results and do not expose sensitive database contents to users without appropriate authorization.
- Langfuse tracing may send prompts, model outputs, and usage metadata to your configured Langfuse service. Review your data handling requirements before enabling it.

## Development

Run the test suite with:

```bash
python -m pytest tests -v
```

Some tests require a configured database or external API credentials. Consult the individual tests and environment before running integration tests.

## License

No license file is currently included. Add a license before treating this repository as open source or inviting reuse.
