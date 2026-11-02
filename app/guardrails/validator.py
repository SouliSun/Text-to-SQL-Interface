import re
from typing import List, Set, Optional, Dict
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.guardrails.schema import (
    SQLValidationResult,
    SQLValidationError,
    SQLColumnRef,
)
from app.guardrails.rules import (
    FORBIDDEN_EXPRESSION_TYPES,
    DEFAULT_DISALLOWED_FUNCTIONS,
    DEFAULT_SENSITIVE_TABLES,
    DEFAULT_LIMIT,
    MAX_LIMIT,
)

class SQLValidator:
    def __init__(
        self,
        dialect: str = "postgres",
        read_only: bool = True,
        enforce_single_statement: bool = True,
        default_limit: int = DEFAULT_LIMIT,
        max_limit: int = MAX_LIMIT,
        auto_inject_limit: bool = True,
        disallowed_functions: Optional[Set[str]] = None,
        sensitive_tables: Optional[Set[str]] = None,
    ):
        self.dialect = dialect
        self.read_only = read_only
        self.enforce_single_statement = enforce_single_statement
        self.default_limit = default_limit
        self.max_limit = max_limit
        self.auto_inject_limit = auto_inject_limit
        self.disallowed_functions = (
            {f.lower() for f in disallowed_functions}
            if disallowed_functions is not None
            else DEFAULT_DISALLOWED_FUNCTIONS
        )
        self.sensitive_tables = (
            {t.lower() for t in sensitive_tables}
            if sensitive_tables is not None
            else DEFAULT_SENSITIVE_TABLES
        )

    def clean_raw_sql(self, sql: str) -> str:
        if not sql :
            return ""
        text = sql.strip()
        markdown_match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if markdown_match:
            text = markdown_match.group(1).strip()
        return text

    def validate(self, sql_query: str) -> SQLValidationResult:
        raw_sql = sql_query or ""
        cleaned = self.clean_raw_sql(raw_sql)

        if not cleaned : 
            return SQLValidationResult(
                is_valid = False,
                original_sql=raw_sql,
                errors=[
                    SQLValidationError(
                        code = "EMPTY_QUERY",
                        message = "Query string is empty or contains only whitespaces."
                    )
                ]
            )
        try:
            parsed = sqlglot.parse(cleaned, read= self.dialect)
        except ParseError as pe:
            return SQLValidationResult(
                is_valid=False,
                original_sql=raw_sql,
                errors = [
                    SQLValidationError(
                        code="SYNTAX_ERROR",
                        message = f"SQL syntax parse error: {str(pe)}"
                    )
                ]
            )
        
        statements = [expr for expr in parsed if expr is not None]

        if not statements:
            return SQLValidationResult(
                is_valid=False,
                original_sql = raw_sql,
                errors = [
                    SQLValidationError(
                        code="EMPTY_QUERY",
                        message = "No executable statements found."
                    )
                ]
            )
        
        if self.enforce_single_statement and len(statements) > 1:
            return SQLValidationResult(
                is_valid = False,
                original_sql=raw_sql,
                errors=[
                    SQLValidationError(
                        code = "MULTI_STATEMENT_QUERY_DETECTED",
                        message = f"Multi statement query detected ({len(statements)} found.) Excpeted as a single statement."
                    )
                ]
            )
        ast = statements[0]
        errors : List[SQLValidationError] = []
        warnings : List[str] = []

        query_type = ast.key.upper() if ast.key else "Unknown"
        if self.read_only and not isinstance(ast, (exp.Select, exp.Union)):
            errors.append(
                SQLValidationError(
                    code="DISALLOWED_STATEMENT_TYPE",
                    message= f"Only SELECT / UNION queries are permitted. Got: {query_type}",
                    location=f"Root ({query_type})"
                )
            )

        # Disallow table-less dummy queries (e.g. SELECT ''::text ... WHERE FALSE)
        if isinstance(ast, exp.Select) and not ast.find(exp.From):
            errors.append(
                SQLValidationError(
                    code="MISSING_FROM_CLAUSE",
                    message="Query lacks a FROM clause. Table-less literal or dummy queries are disallowed.",
                    location="Root (SELECT)"
                )
            )

        # Disallow constant false predicates (e.g. WHERE FALSE, WHERE 1 = 0)
        where_node = ast.find(exp.Where)
        if where_node:
            for b in where_node.find_all(exp.Boolean):
                if b.this is False or str(b.this).lower() == "false":
                    errors.append(
                        SQLValidationError(
                            code="DUMMY_QUERY_DETECTED",
                            message="Query contains a constant false predicate ('WHERE FALSE') that produces empty dummy results.",
                            location="WHERE"
                        )
                    )
                    break
            for eq in where_node.find_all(exp.EQ):
                if isinstance(eq.left, exp.Literal) and isinstance(eq.right, exp.Literal):
                    if eq.left.this != eq.right.this:
                        errors.append(
                            SQLValidationError(
                                code="DUMMY_QUERY_DETECTED",
                                message="Query contains a constant false predicate (e.g. '1 = 0') that produces empty dummy results.",
                                location="WHERE"
                            )
                        )
                        break

        for node in ast.walk():
            if isinstance(node, FORBIDDEN_EXPRESSION_TYPES):
                node_type = node.key.upper() if node.key else type(node).__name__.upper()
                errors.append(
                    SQLValidationError(
                        code="MUTATION_DETECTED",
                        message= f"Destructive operation '{node_type}' detected inside query AST.",
                        location = node_type
                    )
                )
        for func_node in ast.find_all(exp.Anonymous, exp.Func):
            func_name = func_node.name.lower() if hasattr(func_node, "name") and func_node.name else ""
            if not func_name and hasattr(func_node, "this"):
                func_name = str(func_node.this).lower()

            if func_name in self.disallowed_functions:
                errors.append(
                    SQLValidationError(
                        code="DANGEROUS_FUNCTION_DETECTED",
                        message=f"Function '{func_name}' is forbidden by security guardrails.",
                        location=func_name
                    )
                )

        cte_names : Set[str] = set()
        for cte in ast.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                cte_names.add(alias.lower())

        physical_tables: Set[str] = set()
        table_aliases: Dict[str, str] = {}
        for tbl in ast.find_all(exp.Table):
            t_name = tbl.name
            if t_name:
                t_lower = t_name.lower()
                if t_lower not in cte_names:
                    physical_tables.add(t_lower)
                    alias = tbl.alias
                    if alias:
                        table_aliases[alias.lower()] = t_lower
                    table_aliases[t_lower] = t_lower
                    if t_lower in self.sensitive_tables:
                        errors.append(
                            SQLValidationError(
                                code = "SENSITIVE_TABLE_ACCESS",
                                message=f"Access to restricted system table '{t_name} is blocked.'",
                                location=t_name
                            )
                        )

        select_aliases: Set[str] = set()
        for alias_node in ast.find_all(exp.Alias):
            a_name = alias_node.alias_or_name
            if a_name:
                select_aliases.add(a_name.lower())

        columns_extracted: List[SQLColumnRef] = []
        seen_cols = set()
        for col in ast.find_all(exp.Column):
            c_name = col.name
            t_prefix = col.table if col.table else None
            if c_name and (t_prefix, c_name) not in seen_cols:
                seen_cols.add((t_prefix, c_name))
                columns_extracted.append(SQLColumnRef(
                    name = c_name,
                    table = t_prefix
                ))

        has_limit = False
        limit_value: Optional[int] = None
        limit_injected = False

        limit_node = ast.find(exp.Limit)
        if limit_node and limit_node.expression:
            has_limit = True
            try:
                raw_val = int(str(limit_node.expression).strip())
                if raw_val > self.max_limit:
                    warnings.append(
                        f"Specified LIMIT {raw_val} exceeded maximum {self.max_limit}. Clamped to {self.max_limit}."
                    )
                    limit_node.set("expression", exp.Literal.number(self.max_limit))
                    limit_value = self.max_limit
                else:
                    limit_value = raw_val
            except (ValueError, TypeError):
                warnings.append("Could not evaluate numeric LIMIT value.")
        elif self.auto_inject_limit and isinstance(ast, (exp.Select, exp.Union)) and not errors:
            ast = ast.limit(self.default_limit)
            has_limit = True
            limit_value = self.default_limit
            limit_injected = True
            warnings.append(f"No Limit specified. Injected default LIMIT {self.default_limit}")

        is_valid = len(errors) == 0
        sanitized_sql : Optional[str] = None

        if is_valid:
            try:
                sanitized_sql = ast.sql(dialect=self.dialect, pretty=True)
            except Exception:
                sanitized_sql = ast.sql(dialect=self.dialect)

        return SQLValidationResult(
            is_valid = is_valid,
            original_sql=raw_sql,
            sanitized_sql = sanitized_sql,
            query_type = query_type,
            tables=physical_tables,
            table_aliases=table_aliases,
            select_aliases=select_aliases,
            columns=columns_extracted,
            cte_names=cte_names,
            has_limit=has_limit,
            limit_value=limit_value,
            limit_injected=limit_injected,
            errors=errors,
            warnings=warnings,
        )
