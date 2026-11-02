import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.hallucination.schema_catalog import SchemaCatalogProvider

def test_live_catalog():
    print("Fetching live database schema from Supabase...")
    provider = SchemaCatalogProvider()
    catalog = provider.get_catalog()

    print(f"\n[SUCCESS] Found {len(catalog.tables)} tables in database:")
    for table_name, table_info in catalog.tables.items():
        print(f"\n[Table] '{table_name}'")
        print("   Columns:")
        for col_name, col_info in table_info.columns.items():
            pk = " (PK)" if col_info.is_primary_key else ""
            print(f"     - {col_name}: {col_info.data_type}{pk}")
        if table_info.foreign_keys:
            print("   Foreign Keys:")
            for fk in table_info.foreign_keys:
                print(f"     - {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")

if __name__ == "__main__":
    test_live_catalog()
