import os
import time
import logging
from typing import Optional
from dotenv import load_dotenv
from langfuse.openai import OpenAI

from app.hallucination.models import DatabaseCatalog
from app.hallucination.schema_catalog import SchemaCatalogProvider

load_dotenv()
logger = logging.getLogger("text2sql.llm.generator")


class SQLGenerator:
    def __init__(self, model_name: Optional[str] = None):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not set in environment or .env file")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        self.model_name = model_name or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    def _build_schema_context(self, catalog: DatabaseCatalog) -> str:
        schema_lines = []
        for table_name, table_info in catalog.tables.items():
            cols = [f"{col.name} ({col.data_type})" for col in table_info.columns.values()]
            schema_lines.append(f"Table '{table_name}': {', '.join(cols)}")
            for fk in table_info.foreign_keys:
                schema_lines.append(f"Foreign Key: {table_name}.{fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")
        return "\n".join(schema_lines)

    def generate_sql(self, question: str, catalog: DatabaseCatalog) -> str:
        schema_context = self._build_schema_context(catalog)

        system_prompt = f"""You are an expert PostgreSQL database engineer.
Your task is to translate plain English questions into clean, accurate, read-only SQL queries.
Database Schema:
{schema_context}
Rules:
1. ONLY return a SELECT query. Never use INSERT, UPDATE, DELETE, DROP, or ALTER.
2. Only use the tables and columns explicitly listed in the schema above. Do NOT invent tables or columns.
3. STRICT SCHEMA GROUNDING:
   - If the user asks for entities, tables, or attributes NOT present in the schema (such as invoices, reviews, suppliers, birth dates, phone numbers, addresses, or cities):
     * NEVER substitute unrelated columns (e.g., NEVER use created_at for birth date, NEVER use country for city, NEVER substitute orders for invoices).
     * NEVER invent dummy placeholder literals (e.g., 'N/A', 'Acme Corp', or NULL).
   - If a question cannot be answered strictly using existing schema columns, output ONLY:
     REFUSAL: The database schema does not contain the requested entity or attribute.
4. Use PostgreSQL dialect syntax.
5. Output ONLY the raw SQL query or REFUSAL. Do not include markdown fences, explanations, or chatter.
"""

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question}
                    ],
                    temperature=0.0,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                err_str = str(e).lower()
                if ("rate limit" in err_str or "429" in err_str) and attempt < max_retries:
                    wait_sec = 4 * (attempt + 1)
                    logger.warning("Groq API rate limit hit. Backing off for %ds (attempt %d/%d)...", wait_sec, attempt + 1, max_retries)
                    time.sleep(wait_sec)
                else:
                    raise

    def regenerate_with_feedback(self, question: str, failed_sql: str, feedback: str, catalog: DatabaseCatalog) -> str:
        schema_context = self._build_schema_context(catalog)

        system_prompt = f"""You are an expert PostgreSQL database engineer.
Your previous SQL query for the user's question failed validation.
Database Schema:
{schema_context}
Rules:
1. ONLY return a SELECT query.
2. Fix the specific error mentioned in the feedback.
3. Output ONLY the raw corrected SQL query or REFUSAL. No explanations or markdown.
"""
        user_prompt = f"""Question: "{question}"
Failed SQL:
{failed_sql}
Validation Feedback / Error:
{feedback}
Please provide the corrected SQL query:"""
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                err_str = str(e).lower()
                if ("rate limit" in err_str or "429" in err_str) and attempt < max_retries:
                    wait_sec = 4 * (attempt + 1)
                    logger.warning("Groq API rate limit hit in regeneration. Backing off for %ds (attempt %d/%d)...", wait_sec, attempt + 1, max_retries)
                    time.sleep(wait_sec)
                else:
                    raise