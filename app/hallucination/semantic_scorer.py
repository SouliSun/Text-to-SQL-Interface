import re
from typing import List
from pydantic import BaseModel, Field
import sqlglot
from sqlglot import exp

class SemanticAlignmentResult(BaseModel):
    is_aligned: bool = Field(..., description="Whether query satisfies minimum heuristic intent criteria")
    heuristic_alignment_score: float = Field(..., ge=0.0, le=1.0, description="Heuristic rule-based alignment score (uncalibrated)")
    intent_signals: List[str] = Field(default_factory=list)
    sql_features: List[str] = Field(default_factory=list)
    mismatches: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

class SemanticAlignmentScorer:
    """
    Heuristic Intent Alignment Evaluator.
    Rule-based comparison between natural language keywords and SQL AST features.
    NOTE: This is an uncalibrated heuristic metric, not a probabilistic confidence score.
    """

    INTENT_PATTERNS = {
        "COUNT": [r"\bhow many\b", r"\bcount\b", r"\bnumber of customers\b", r"\bnumber of orders\b", r"\bnumber of products\b"],
        "SUM": [r"\bsum of\b", r"\btotal revenue\b", r"\btotal amount spent\b", r"\btotal value of inventory\b"],
        "AVERAGE": [r"\baverage\b", r"\bavg\b", r"\bmean\b"],
        "MAX_EXTREMA": [r"\bmost expensive\b", r"\bhighest\b", r"\btop\b", r"\bmaximum\b", r"\bbest\b"],
        "MIN_EXTREMA": [r"\bcheapest\b", r"\blowest\b", r"\bleast\b", r"\bminimum\b", r"\bworst\b"],
        "GROUPING": [r"\bper\b", r"\bgrouped by\b"],
        "FILTER": [
            r"\bfind\b.*\bby\b",
            r"\bsearch\b.*\bby\b",
            r"\bfilter(?:ed)?\s+by\b",
            r"\bwhere\b",
            r"\bwith fewer than\b",
            r"\bwith more than\b",
            r"\bwhose\b",
            r"\bplaced between\b",
        ],
    }

    def extract_intent(self, question: str) -> List[str]:
        q_lower = question.lower()
        signals = []
        for intent_name, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, q_lower):
                    signals.append(intent_name)
                    break
        return signals

    def extract_sql_features(self, sql: str) -> List[str]:
        features = []
        try:
            ast = sqlglot.parse_one(sql, read="postgres")
        except Exception: 
            return features

        for func in ast.find_all(exp.Func):
            func_name = func.key.upper() if func.key else ""
            if isinstance(func, exp.Count) or func_name == "COUNT":
                features.append("COUNT")
            elif isinstance(func, exp.Avg) or func_name == "AVG":
                features.append("AVERAGE")
            elif isinstance(func, exp.Sum) or func_name == "SUM":
                features.append("SUM")
            elif isinstance(func, (exp.Min, exp.Max)) or func_name in ("MIN", "MAX"):
                features.append("AGG_EXTREMA")

        order_node = ast.find(exp.Order)
        if order_node:
            for oc in order_node.expressions:
                if isinstance(oc, exp.Ordered):
                    if oc.args.get("desc"):
                        features.append("ORDER_DESC")
                    else:
                        features.append("ORDER_ASC")

        if ast.find(exp.Group):
            features.append("GROUPING")

        where_node = ast.find(exp.Where)
        if where_node:
            has_false_boolean = any(
                isinstance(lit, exp.Boolean) and (lit.this is False or str(lit.this).lower() == "false")
                for lit in where_node.find_all(exp.Boolean)
            )
            if not has_false_boolean:
                features.append("FILTER")

        return list(set(features))

    def evaluate(self, question: str, sql: str) -> SemanticAlignmentResult:
        intents = self.extract_intent(question)
        features = self.extract_sql_features(sql)

        mismatches: List[str] = []
        warnings: List[str] = []
        score = 1.0

        if "COUNT" in intents and "COUNT" not in features:
            mismatches.append("Question requested a count, but SQL did not use COUNT().")
            score -= 0.35
        if "AVERAGE" in intents and "AVERAGE" not in features:
            mismatches.append("Question requested an average, but SQL did not use AVG().")
            score -= 0.35
        if "SUM" in intents and "SUM" not in features:
            mismatches.append("Question requested a sum/total, but SQL did not use SUM().")
            score -= 0.35
        if "FILTER" in intents and "FILTER" not in features:
            mismatches.append("Question requested filtering by specific criteria ('find ... by ...'), but SQL contains no WHERE filter clause.")
            score -= 0.50

        q_lower = question.lower()
        sql_lower = sql.lower()

        if any(w in q_lower for w in ["born", "birth", "age", "birthday"]) and "created_at" in sql_lower:
            mismatches.append("Question requested birth/age criteria, but query substituted account 'created_at'.")
            score -= 0.50

        if any(w in q_lower for w in ["city", "address", "street", "berlin", "paris", "london"]) and "country" in sql_lower:
            mismatches.append("Question requested city/address criteria, but query substituted 'country'.")
            score -= 0.50
            
        has_min = "ORDER_ASC" in features or "AGG_EXTREMA" in features
        has_max = "ORDER_DESC" in features or "AGG_EXTREMA" in features

        if "MAX_EXTREMA" in intents and not has_max:
            warnings.append("Question requested top/highest extrema, but SQL lacks DESC ordering or MAX().")
            score -= 0.25
        if "MIN_EXTREMA" in intents and not has_min:
            warnings.append("Question requested lowest/cheapest extrema, but SQL lacks ASC ordering or MIN().")
            score -= 0.25

        has_any_aggregate = any(f in features for f in ("COUNT", "AVERAGE", "SUM", "AGG_EXTREMA"))
        if "GROUPING" in intents and has_any_aggregate and "GROUPING" not in features:
            warnings.append("Question requested grouping per entity with aggregation, but SQL is missing GROUP BY.")
            score -= 0.25

        score = max(0.0, min(1.0, round(score, 2)))
        is_aligned = len(mismatches) == 0 and score >= 0.60

        return SemanticAlignmentResult(
            is_aligned=is_aligned,
            heuristic_alignment_score=score,
            intent_signals=intents,
            sql_features=features,
            mismatches=mismatches,
            warnings=warnings,
        )
