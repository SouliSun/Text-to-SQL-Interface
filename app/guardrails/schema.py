from pydantic import BaseModel, Field
from typing import List, Set, Optional, Dict

class SQLValidationError(BaseModel):
    code: str = Field(..., description="Machine-readable code, e.g. 'MUTATION_DETECTED', 'MULTI_STATEMENT'")
    message: str = Field(..., description="Human-readable explanation of why the query failed")
    location: Optional[str] = Field(None, description="AST node or snippet causing the failure")

class SQLColumnRef(BaseModel):
    name: str
    table: Optional[str] = None

class SQLValidationResult(BaseModel):
    is_valid: bool
    original_sql: str
    sanitized_sql: Optional[str] = None
    query_type: Optional[str] = None

    tables: Set[str] = Field(default_factory=set)
    table_aliases: Dict[str, str] = Field(default_factory=dict)
    select_aliases: Set[str] = Field(default_factory=set)
    columns: List[SQLColumnRef] = Field(default_factory=list)
    cte_names: Set[str] = Field(default_factory=set)

    has_limit: bool = False
    limit_value: Optional[int] = None
    limit_injected: bool = False

    errors: List[SQLValidationError] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
