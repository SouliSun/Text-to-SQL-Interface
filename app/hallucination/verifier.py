import difflib
import logging
from typing import List, Optional, Set, Dict, Tuple
from collections import deque
import sqlglot
from sqlglot import exp

from app.guardrails.schema import SQLValidationResult
from app.hallucination.models import (
    DatabaseCatalog,
    HallucinationReport,
    SchemaMismatch,
)

logger = logging.getLogger("text2sql.verifier")


class SchemaHallunicationVerifier:
    def __init__(self, catalog: DatabaseCatalog):
        self.catalog = catalog
        self.fk_graph, self.fk_column_pairs = self._build_foreign_key_metadata()

    def _build_foreign_key_metadata(self) -> Tuple[Dict[str, Set[str]], Set[Tuple[str, str, str, str]]]:
        """
        Builds:
        1. An undirected relational graph where an edge exists if two tables share an FK.
        2. A set of valid foreign key column tuples (t1, c1, t2, c2) in both directions.
        """
        graph: Dict[str, Set[str]] = {t.lower(): set() for t in self.catalog.tables.keys()}
        fk_column_pairs: Set[Tuple[str, str, str, str]] = set()

        for table_name, table_info in self.catalog.tables.items():
            t1 = table_name.lower()
            for fk in table_info.foreign_keys:
                ref_tbl = fk.get("referred_table")
                if ref_tbl:
                    t2 = ref_tbl.lower()
                    if t2 in self.catalog.tables:
                        graph[t1].add(t2)
                        graph[t2].add(t1)

                    constrained_cols = [c.lower() for c in fk.get("constrained_columns", [])]
                    referred_cols = [c.lower() for c in fk.get("referred_columns", [])]
                    for c_col, r_col in zip(constrained_cols, referred_cols):
                        fk_column_pairs.add((t1, c_col, t2, r_col))
                        fk_column_pairs.add((t2, r_col, t1, c_col))

        return graph, fk_column_pairs

    def _find_relational_path(self, start: str, end: str) -> Optional[List[str]]:
        """
        Finds the shortest relational path connecting two tables in the schema's FK graph.
        Returns the list of table names in order, or None if no valid path exists.
        """
        if start == end:
            return [start]
        visited = {start}
        queue = deque([[start]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in self.fk_graph.get(node, set()):
                if neighbor == end:
                    return path + [end]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    def _resolve_table_for_col(
        self,
        col_node: exp.Column,
        alias_to_table: Dict[str, str],
        tables_in_query: Set[str],
    ) -> Optional[str]:
        """Resolves the owning table name for a column AST node."""
        if col_node.table:
            prefix = col_node.table.lower()
            return alias_to_table.get(prefix, prefix)
        # If unqualified, check if only one referenced table contains it
        col_name = col_node.name.lower()
        candidates = [
            t for t in tables_in_query
            if self.catalog.column_exists(t, col_name)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _verify_query_join_connectivity(
        self,
        ast: exp.Expression,
        real_tables_in_query: Set[str],
        table_aliases: Dict[str, str],
    ) -> Tuple[List[SchemaMismatch], Dict[str, Set[str]]]:
        """
        Builds the query's actual join graph from explicit JOIN (ON/USING) and
        conjunctive WHERE foreign-key equalities, then verifies that:
        1. All referenced tables belong to a single connected component in the query.
        2. Every join condition connects tables that share a direct FK using valid constrained columns.
        """
        mismatches: List[SchemaMismatch] = []
        if len(real_tables_in_query) < 2:
            return mismatches, {}

        alias_to_table = {k.lower(): v.lower() for k, v in table_aliases.items()}

        # Build query join graph: table -> set of tables it is directly joined with in the SQL
        query_join_graph: Dict[str, Set[str]] = {t: set() for t in real_tables_in_query}
        join_conditions_checked: Set[Tuple[str, str, str, str]] = set()

        # 1. Process explicit JOIN clauses
        for join in ast.find_all(exp.Join):
            join_table_node = join.this
            if not isinstance(join_table_node, exp.Table):
                continue

            joined_tbl_raw = join_table_node.name.lower()
            joined_table = alias_to_table.get(joined_tbl_raw, joined_tbl_raw)

            # Check ON conditions
            on_clause = join.args.get("on")
            if on_clause:
                for eq in on_clause.find_all(exp.EQ):
                    left_col = eq.left
                    right_col = eq.right
                    if isinstance(left_col, exp.Column) and isinstance(right_col, exp.Column):
                        tbl1 = self._resolve_table_for_col(left_col, alias_to_table, real_tables_in_query)
                        tbl2 = self._resolve_table_for_col(right_col, alias_to_table, real_tables_in_query)

                        if tbl1 and tbl2 and tbl1 != tbl2 and tbl1 in real_tables_in_query and tbl2 in real_tables_in_query:
                            query_join_graph[tbl1].add(tbl2)
                            query_join_graph[tbl2].add(tbl1)

                            c1 = left_col.name.lower()
                            c2 = right_col.name.lower()
                            pair_key = (min(tbl1, tbl2), c1, max(tbl1, tbl2), c2)

                            if pair_key not in join_conditions_checked:
                                join_conditions_checked.add(pair_key)

                                # Verify direct FK relationship in schema
                                if tbl2 not in self.fk_graph.get(tbl1, set()):
                                    path = self._find_relational_path(tbl1, tbl2)
                                    bridge_hint = f" You must bridge through: {' -> '.join(path)}." if path else " No relational link exists."
                                    mismatches.append(
                                        SchemaMismatch(
                                            item_type="INVALID_JOIN_RELATIONSHIP",
                                            hallucinated_name=f"{tbl1}.{c1} = {tbl2}.{c2}",
                                            suggested_correction=f"No direct foreign key connects '{tbl1}' to '{tbl2}'.{bridge_hint}",
                                            context=f"JOIN {joined_table}",
                                        )
                                    )
                                # Verify constrained columns match foreign key definition
                                elif (tbl1, c1, tbl2, c2) not in self.fk_column_pairs:
                                    expected_pairs = [
                                        f"{a}.{b} = {c}.{d}"
                                        for (a, b, c, d) in self.fk_column_pairs
                                        if a == tbl1 and c == tbl2
                                    ]
                                    expected_str = f" Expected foreign key relation: {', '.join(expected_pairs)}" if expected_pairs else ""
                                    mismatches.append(
                                        SchemaMismatch(
                                            item_type="INVALID_JOIN_CONDITION",
                                            hallucinated_name=f"{tbl1}.{c1} = {tbl2}.{c2}",
                                            suggested_correction=f"Columns '{c1}' and '{c2}' do not form a foreign key constraint between '{tbl1}' and '{tbl2}'.{expected_str}",
                                            context=f"JOIN {joined_table}",
                                        )
                                    )

            # Check USING conditions
            using_clause = join.args.get("using")
            if using_clause:
                using_items = using_clause if isinstance(using_clause, list) else [using_clause]
                for item in using_items:
                    col_name = (item.name if hasattr(item, "name") else str(item)).lower()
                    if not self.catalog.column_exists(joined_table, col_name):
                        mismatches.append(
                            SchemaMismatch(
                                item_type="COLUMN",
                                hallucinated_name=col_name,
                                suggested_correction=None,
                                context=f"JOIN {joined_table} USING",
                            )
                        )
                    else:
                        for other_tbl in real_tables_in_query:
                            if other_tbl != joined_table and self.catalog.column_exists(other_tbl, col_name):
                                query_join_graph[joined_table].add(other_tbl)
                                query_join_graph[other_tbl].add(joined_table)

        # 2. Process WHERE equality clauses (implicit foreign-key joins)
        for where in ast.find_all(exp.Where):
            for eq in where.find_all(exp.EQ):
                # Ignore equalities inside OR blocks
                if eq.find_ancestor(exp.Or) is not None:
                    continue

                left_col = eq.left
                right_col = eq.right
                if isinstance(left_col, exp.Column) and isinstance(right_col, exp.Column):
                    tbl1 = self._resolve_table_for_col(left_col, alias_to_table, real_tables_in_query)
                    tbl2 = self._resolve_table_for_col(right_col, alias_to_table, real_tables_in_query)

                    if tbl1 and tbl2 and tbl1 != tbl2 and tbl1 in real_tables_in_query and tbl2 in real_tables_in_query:
                        c1 = left_col.name.lower()
                        c2 = right_col.name.lower()
                        # Only treat WHERE equality as a join edge if it connects on an authentic FK constraint
                        if (tbl1, c1, tbl2, c2) in self.fk_column_pairs:
                            query_join_graph[tbl1].add(tbl2)
                            query_join_graph[tbl2].add(tbl1)

        # 3. Check that the query's actual join graph forms a single connected component
        start_node = next(iter(real_tables_in_query))
        visited = {start_node}
        queue = deque([start_node])
        while queue:
            curr = queue.popleft()
            for neighbor in query_join_graph.get(curr, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        disconnected = real_tables_in_query - visited
        if disconnected:
            mismatches.append(
                SchemaMismatch(
                    item_type="DISCONNECTED_QUERY_JOIN_GRAPH",
                    hallucinated_name=f"Disconnected table(s): {', '.join(sorted(disconnected))}",
                    suggested_correction=(
                        f"The query references multiple tables ({', '.join(sorted(real_tables_in_query))}), "
                        f"but table(s) '{', '.join(sorted(disconnected))}' are not joined to the rest of the query. "
                        "Add an explicit foreign-key JOIN ... ON condition to prevent accidental Cartesian cross-joins."
                    ),
                    context="Query Join Graph Connectivity",
                )
            )

        return mismatches, query_join_graph

    def _find_closest_match(self, word: str, candidates: List[str], cutoff: float = 0.5) -> Optional[str]:
        matches = difflib.get_close_matches(word.lower(), [c.lower() for c in candidates], n=1, cutoff=cutoff)
        return matches[0] if matches else None

    def verify(self, validation_result: SQLValidationResult) -> HallucinationReport:
        mismatches: List[SchemaMismatch] = []
        warnings: List[str] = []

        all_real_tables = list(self.catalog.tables.keys())

        # 1. Verify Table Existence
        real_tables_in_query: Set[str] = set()
        for table in validation_result.tables:
            table_lower = table.lower()
            if not self.catalog.table_exists(table_lower):
                suggested = self._find_closest_match(table_lower, all_real_tables)
                mismatches.append(
                    SchemaMismatch(
                        item_type="TABLE",
                        hallucinated_name=table,
                        suggested_correction=suggested,
                    )
                )
            else:
                real_tables_in_query.add(table_lower)

        # 2. Parse AST for join and projection verification
        parsed_ast = None
        if validation_result.sanitized_sql:
            try:
                parsed_ast = sqlglot.parse_one(validation_result.sanitized_sql, read="postgres")
            except Exception as ex:
                logger.error("Failed to parse SQL AST for verification: %s", str(ex), exc_info=True)
                mismatches.append(
                    SchemaMismatch(
                        item_type="JOIN_VERIFICATION_PARSE_ERROR",
                        hallucinated_name=validation_result.sanitized_sql[:60] + "...",
                        suggested_correction="Ensure query syntax is valid PostgreSQL ANSI SQL.",
                        context=f"Parser Error: {str(ex)}",
                    )
                )

        # 3. Check for mocked NULL columns (e.g. NULL::INTEGER AS age)
        if parsed_ast:
            for select_node in parsed_ast.find_all(exp.Select):
                for expr in select_node.expressions:
                    alias = expr.alias
                    if alias:
                        is_mock_null = False
                        is_mock_literal = False
                        target = expr.this
                        
                        if isinstance(target, exp.Null) or (isinstance(target, (exp.Cast, exp.TryCast)) and isinstance(target.this, exp.Null)):
                            is_mock_null = True
                        elif isinstance(target, exp.Literal) or (isinstance(target, (exp.Cast, exp.TryCast)) and isinstance(target.this, exp.Literal)):
                            is_mock_literal = True

                        if is_mock_null or is_mock_literal:
                            alias_lower = alias.lower()
                            if not any(self.catalog.column_exists(t, alias_lower) for t in real_tables_in_query):
                                item_type = "MOCKED_NULL_COLUMN" if is_mock_null else "MOCKED_LITERAL_COLUMN"
                                mismatches.append(
                                    SchemaMismatch(
                                        item_type=item_type,
                                        hallucinated_name=alias,
                                        suggested_correction=f"Column '{alias}' does not exist in schema. Projecting fake placeholder values is prohibited.",
                                        context="SELECT projection",
                                    )
                                )

        # 4. Query Join Graph Connectivity & FK Integrity
        if len(real_tables_in_query) >= 2 and parsed_ast:
            join_errors, _ = self._verify_query_join_connectivity(
                parsed_ast,
                real_tables_in_query,
                validation_result.table_aliases,
            )
            mismatches.extend(join_errors)

        referenced_real_tables = [
            self.catalog.tables[t]
            for t in real_tables_in_query
        ]

        # 3. Column Source Mapping (For Ambiguity Detection)
        column_to_tables: Dict[str, List[str]] = {}
        for tbl in referenced_real_tables:
            for col in tbl.columns.keys():
                column_to_tables.setdefault(col, []).append(tbl.name)

        valid_aliases = getattr(validation_result, "select_aliases", set())

        # 4. Verify Columns (Qualified & Unqualified)
        for col_ref in validation_result.columns:
            col_name_lower = col_ref.name.lower()

            if col_name_lower == "*":
                continue

            if col_name_lower in valid_aliases:
                continue

            # Case A: Explicitly Qualified Column (e.g. `c.name` or `customers.id`)
            if col_ref.table:
                raw_prefix = col_ref.table.lower()
                resolved_table = validation_result.table_aliases.get(raw_prefix, raw_prefix)

                if self.catalog.table_exists(resolved_table):
                    if not self.catalog.column_exists(resolved_table, col_name_lower):
                        tbl_cols = list(self.catalog.tables[resolved_table].columns.keys())
                        suggested = self._find_closest_match(col_name_lower, tbl_cols)
                        mismatches.append(
                            SchemaMismatch(
                                item_type="COLUMN",
                                hallucinated_name=col_ref.name,
                                suggested_correction=suggested,
                                context=resolved_table,
                            )
                        )
                elif resolved_table not in validation_result.tables:
                    mismatches.append(
                        SchemaMismatch(
                            item_type="COLUMN",
                            hallucinated_name=col_ref.name,
                            suggested_correction=None,
                            context=col_ref.table,
                        )
                    )

            # Case B: Unqualified Column (e.g. `SELECT id`)
            else:
                owning_tables = column_to_tables.get(col_name_lower, [])
                if len(owning_tables) > 1:
                    mismatches.append(
                        SchemaMismatch(
                            item_type="AMBIGUOUS_COLUMN",
                            hallucinated_name=col_ref.name,
                            suggested_correction=f"Disambiguate column with table alias (e.g. '{owning_tables[0]}.{col_ref.name}')",
                            context=f"Collides across tables: {', '.join(owning_tables)}",
                        )
                    )
                elif len(owning_tables) == 0:
                    all_cols = self.catalog.get_all_columns_names()
                    suggested = self._find_closest_match(col_name_lower, list(all_cols))
                    mismatches.append(
                        SchemaMismatch(
                            item_type="COLUMN",
                            hallucinated_name=col_ref.name,
                            suggested_correction=suggested,
                        )
                    )

        is_hallucinated = len(mismatches) > 0
        if is_hallucinated:
            confidence_score = 0.0
            confidence_breakdown = {
                "schema_integrity": 0.0,
                "table_hallucinations": sum(1 for m in mismatches if m.item_type == "TABLE"),
                "column_hallucinations": sum(1 for m in mismatches if m.item_type == "COLUMN"),
                "ambiguous_columns": sum(1 for m in mismatches if m.item_type == "AMBIGUOUS_COLUMN"),
                "invalid_join_relationships": sum(1 for m in mismatches if m.item_type in ("INVALID_JOIN_RELATIONSHIP", "INVALID_JOIN_CONDITION", "DISCONNECTED_QUERY_JOIN_GRAPH")),
            }
        else:
            confidence_score = 1.0
            confidence_breakdown = {
                "schema_integrity": 1.0,
            }

        return HallucinationReport(
            is_hallucinated=is_hallucinated,
            confidence_score=confidence_score,
            schema_mismatches=mismatches,
            semantic_warnings=warnings,
            confidence_breakdown=confidence_breakdown,
        )
