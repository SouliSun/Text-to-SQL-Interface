from typing import Optional
from sqlalchemy import inspect, Engine
from app.db.connection import get_engine
from app.hallucination.models import DatabaseCatalog, TableInfo, ColumnInfo

class SchemaCatalogProvider:
    def __init__(self, engine: Optional[Engine] = None, schema: str = "public"):
        self.engine = engine or get_engine()
        self.schema = schema
        self._cached_catalog: Optional[DatabaseCatalog] = None

    def get_catalog(self, force_refresh: bool = False) -> DatabaseCatalog:
        if self._cached_catalog is not None and not force_refresh:
            return self._cached_catalog

        inspector = inspect(self.engine)
        catalog = DatabaseCatalog()

        table_names = inspector.get_table_names(schema=self.schema)

        for tbl_name in table_names:
            cols_info = {}

            for col in inspector.get_columns(tbl_name, schema=self.schema):
                col_name_lower = col["name"].lower()
                cols_info[col_name_lower]  = ColumnInfo(
                    name=col["name"],
                    data_type=str(col["type"]),
                    is_nullable=col.get("nullable", True),
                    is_primary_key=bool(col.get("primary_key", False))
                )

            fks = []
            for fk in inspector.get_foreign_keys(tbl_name, schema=self.schema):
                fks.append({
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

            catalog.tables[tbl_name.lower()] = TableInfo(
                name=tbl_name,
                columns=cols_info,
                foreign_keys=fks
            )

        self._cached_catalog = catalog
        return self._cached_catalog