from typing import List, Optional, Dict, Set, Any
from pydantic import BaseModel, Field

class SchemaMismatch(BaseModel):
    item_type: str
    hallucinated_name: str    
    suggested_correction: Optional[str] = None
    context: Optional[str] = None

class ColumnInfo(BaseModel):
    name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False

class TableInfo(BaseModel):
    name: str
    columns: Dict[str, ColumnInfo] = Field(default_factory=dict)
    foreign_keys: List[Dict[str, Any]] = Field(default_factory=list)

class DatabaseCatalog(BaseModel):
    tables: Dict[str, TableInfo] = Field(default_factory=dict)

    def table_exists(self, table_name: str) -> bool:
        return table_name.lower() in self.tables

    def column_exists(self, table_name: str, column_name: str) -> bool:
        tbl = self.tables.get(table_name.lower())
        if not tbl:
            return False
        return column_name.lower() in tbl.columns

    def get_all_columns_names(self) -> Set[str]:
        all_cols = set()
        for tbl in self.tables.values():
            all_cols.update(tbl.columns.keys())
        return all_cols
    
class HallucinationReport(BaseModel):
    is_hallucinated: bool
    confidence_score: float

    schema_mismatches: List[SchemaMismatch]  = Field(default_factory=list)
    semantic_warnings: List[str] = Field(default_factory=list)
    confidence_breakdown: Dict[str, float] = Field(default_factory=dict)