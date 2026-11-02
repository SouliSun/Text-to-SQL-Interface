from sqlglot import exp

FORBIDDEN_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Create,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Execute,
)

DEFAULT_DISALLOWED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_write_file",
    "pg_ls_dir",
    "pg_stat_file",
    "dblink",
    "dblink_exec",
    "query_to_xml",
    "query_to_xml_and_xmlschema",
    "lo_import",
    "lo_export",
    "copy_from_program",
    "system",
    "exec",
    "eval",
}

DEFAULT_SENSITIVE_TABLES = {
    "pg_authid",
    "pg_shadow",
    "pg_database",
    "pg_user",
}

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
