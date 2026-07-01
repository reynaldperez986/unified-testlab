import csv
import json
import re
from io import StringIO
from typing import Any, Tuple

import oracledb
import psycopg
import pyodbc

from .models import DatabaseConnection, TestCase


def _load_options(options_json: str) -> dict:
    if not options_json.strip():
        return {}
    try:
        return json.loads(options_json)
    except json.JSONDecodeError:
        return {}


def _connect(db_conn: DatabaseConnection):
    options = _load_options(db_conn.options_json)

    if db_conn.db_type == DatabaseConnection.DbType.ORACLE:
        service = db_conn.service_name or db_conn.database_name
        dsn = f"{db_conn.host}:{db_conn.port}/{service}"
        return oracledb.connect(
            user=db_conn.username,
            password=db_conn.password,
            dsn=dsn,
            **options,
        )

    if db_conn.db_type == DatabaseConnection.DbType.POSTGRES:
        return psycopg.connect(
            dbname=db_conn.database_name,
            user=db_conn.username,
            password=db_conn.password,
            host=db_conn.host,
            port=db_conn.port,
            **options,
        )

    if db_conn.db_type == DatabaseConnection.DbType.SQLSERVER:
        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={db_conn.host},{db_conn.port};"
            f"DATABASE={db_conn.database_name};"
            f"UID={db_conn.username};"
            f"PWD={db_conn.password};"
            "Encrypt=yes;TrustServerCertificate=yes;"
        )
        return pyodbc.connect(conn_str, **options)

    raise ValueError("Unsupported database type")


def _compare(actual: Any, expected: str, operator: str) -> bool:
    if operator == TestCase.Operator.CONTAINS:
        return expected in str(actual)

    try:
        actual_num = float(actual)
        expected_num = float(expected)
        if operator == TestCase.Operator.EQ:
            return actual_num == expected_num
        if operator == TestCase.Operator.GT:
            return actual_num > expected_num
        if operator == TestCase.Operator.GTE:
            return actual_num >= expected_num
        if operator == TestCase.Operator.LT:
            return actual_num < expected_num
        if operator == TestCase.Operator.LTE:
            return actual_num <= expected_num
    except (TypeError, ValueError):
        actual_str = str(actual)
        if operator == TestCase.Operator.EQ:
            return actual_str == expected
        if operator == TestCase.Operator.GT:
            return actual_str > expected
        if operator == TestCase.Operator.GTE:
            return actual_str >= expected
        if operator == TestCase.Operator.LT:
            return actual_str < expected
        if operator == TestCase.Operator.LTE:
            return actual_str <= expected

    return False


def _table_exists_query(db_type: str, table_name: str) -> Tuple[str, tuple]:
    normalized = table_name.strip()

    if db_type == DatabaseConnection.DbType.ORACLE:
        return "SELECT COUNT(*) FROM user_tables WHERE table_name = :1", (normalized.upper(),)

    if db_type == DatabaseConnection.DbType.POSTGRES:
        return "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s", (normalized,)

    if db_type == DatabaseConnection.DbType.SQLSERVER:
        return "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?", (normalized,)

    raise ValueError("Unsupported database type")


def _safe_table_name(table_name: str) -> str:
    value = table_name.strip()
    # Keep identifier handling strict to reduce SQL injection risk in row count tests.
    if not re.fullmatch(r"[A-Za-z0-9_\.]+", value):
        raise ValueError("Invalid table_name. Only letters, numbers, underscore, and dot are allowed.")
    return value


def _row_to_csv_with_headers(column_names: list[str], row_values: tuple[Any, ...]) -> str:
    headers = [name if name else f"col_{idx}" for idx, name in enumerate(column_names, start=1)]
    values = ["" if value is None else str(value) for value in row_values]
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerow(values)
    return out.getvalue().strip("\r\n")


def _normalize_query_for_driver(query: str, db_type: str) -> str:
    """Normalize user-entered SQL text before sending it to DB drivers."""
    sql = (query or "").strip()

    # Drivers (notably Oracle) expect the SQL statement only, without SQL*Plus
    # terminators. Remove trailing ';' or '/' while preserving inner SQL text.
    while sql.endswith(";") or sql.endswith("/"):
        sql = sql[:-1].rstrip()

    return sql


def _extract_form_data_values(raw_form_data: str) -> list[str]:
    """Extract non-empty value entries from JSON form_data rows [{key, value}]."""
    if not (raw_form_data or '').strip():
        return []
    try:
        rows = json.loads(raw_form_data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    values: list[str] = []
    for row in rows or []:
        value = str((row or {}).get('value', '')).strip()
        if value:
            values.append(value)
    return values


def _extract_expected_values(raw_expected: str) -> list[str]:
    raw = (raw_expected or "").strip()
    if not raw:
        return []

    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = [str(item).strip() for item in parsed]
                return [value for value in values if value]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if "\n" in raw or "\r" in raw:
        values = [segment.strip() for segment in raw.splitlines()]
        values = [value for value in values if value]
        if values:
            return values

    return [raw]


def test_database_connection(db_conn: DatabaseConnection) -> tuple[bool, str]:
    conn = None
    cursor = None

    try:
        conn = _connect(db_conn)
        cursor = conn.cursor()

        if db_conn.db_type == DatabaseConnection.DbType.ORACLE:
            cursor.execute("SELECT 1 FROM dual")
        else:
            cursor.execute("SELECT 1")

        cursor.fetchone()
        return True, "Connection test successful."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def execute_test_case(test_case: TestCase) -> tuple[str, str, str]:
    db_conn = test_case.connection
    conn = None
    cursor = None

    try:
        conn = _connect(db_conn)
        cursor = conn.cursor()

        if test_case.test_type == TestCase.TestType.CONNECTION:
            return "PASS", "Connection successful", "connected"

        if test_case.test_type == TestCase.TestType.TABLE_EXISTS:
            if not test_case.table_name:
                return "ERROR", "table_name is required for TABLE_EXISTS", ""
            query, params = _table_exists_query(db_conn.db_type, test_case.table_name)
            cursor.execute(query, params)
            result = cursor.fetchone()
            count = result[0] if result else 0
            exists = count > 0
            return (
                "PASS" if exists else "FAIL",
                f"Table '{test_case.table_name}' exists={exists}",
                str(exists),
            )

        if test_case.test_type == TestCase.TestType.ROW_COUNT:
            if not test_case.table_name:
                return "ERROR", "table_name is required for ROW_COUNT", ""
            table_name = _safe_table_name(test_case.table_name)
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            result = cursor.fetchone()
            actual = result[0] if result else 0

            checks: list[bool] = []
            detail_parts: list[str] = ["matched on: row-count"]

            expected_values = _extract_expected_values(getattr(test_case, "expected_value", ""))
            if expected_values:
                per_expected = [
                    _compare(actual, expected_value_text, test_case.comparison_operator)
                    for expected_value_text in expected_values
                ]
                expected_ok = all(per_expected)
                checks.append(expected_ok)
                expected_preview = " | ".join(expected_values[:5])
                if len(expected_values) > 5:
                    expected_preview += " | ..."
                detail_parts.append(
                    f"expected_value[{len(expected_values)}]({test_case.comparison_operator} {expected_preview})={'OK' if expected_ok else 'FAIL'}"
                )

            form_values = _extract_form_data_values(getattr(test_case, 'form_data', ''))
            if form_values:
                response_text = str(actual)
                missing_values = [value for value in form_values if value not in response_text]
                form_ok = len(missing_values) == 0
                checks.append(form_ok)
                if form_ok:
                    detail_parts.append("form_data_values=OK")
                else:
                    detail_parts.append("form_data_values=FAIL")
                    detail_parts.append("missing=" + ", ".join(missing_values[:8]))

            # If both expected_value and form_data values are empty, treat row-count execution as PASS.
            passed = all(checks) if checks else True
            detail_text = "Row count validation [" + "; ".join(detail_parts) + "]"
            return (
                "PASS" if passed else "FAIL",
                detail_text,
                str(actual),
            )

        if test_case.test_type == TestCase.TestType.QUERY_VALUE:
            if not test_case.query:
                return "ERROR", "query is required for QUERY_VALUE", ""
            normalized_query = _normalize_query_for_driver(test_case.query, db_conn.db_type)
            if not normalized_query:
                return "ERROR", "query is empty after normalization", ""
            cursor.execute(normalized_query)
            column_names = [desc[0] for desc in (cursor.description or [])]
            result = cursor.fetchone()
            if not result:
                compare_actual = ""
                export_actual = ""
                match_scope = "empty-result"
            elif len(result) == 1:
                compare_actual = result[0]
                export_actual = _row_to_csv_with_headers(column_names or ["value"], result)
                match_scope = "single-column"
            else:
                # Preserve all returned columns so contains/equality checks can target full row payloads.
                compare_actual = " | ".join("" if value is None else str(value) for value in result)
                export_actual = _row_to_csv_with_headers(column_names, result)
                match_scope = "full-row"

            checks: list[bool] = []
            detail_parts: list[str] = [f"matched on: {match_scope}"]

            expected_values = _extract_expected_values(getattr(test_case, "expected_value", ""))
            if expected_values:
                per_expected = [
                    _compare(compare_actual, expected_value_text, test_case.comparison_operator)
                    for expected_value_text in expected_values
                ]
                expected_ok = all(per_expected)
                checks.append(expected_ok)
                expected_preview = " | ".join(expected_values[:5])
                if len(expected_values) > 5:
                    expected_preview += " | ..."
                detail_parts.append(
                    f"expected_value[{len(expected_values)}]({test_case.comparison_operator} {expected_preview})={'OK' if expected_ok else 'FAIL'}"
                )

            form_values = _extract_form_data_values(getattr(test_case, 'form_data', ''))
            if form_values:
                # Validate against query response values only (exclude CSV header labels).
                response_text = str(compare_actual or "")
                missing_values = [value for value in form_values if value not in response_text]
                form_ok = len(missing_values) == 0
                checks.append(form_ok)
                if form_ok:
                    detail_parts.append("form_data_values=OK")
                else:
                    detail_parts.append("form_data_values=FAIL")
                    detail_parts.append("missing=" + ", ".join(missing_values[:8]))

            # If both expected_value and form_data values are empty, treat query execution as PASS.
            passed = all(checks) if checks else True
            detail_text = "Query value validation [" + "; ".join(detail_parts) + "]"
            return (
                "PASS" if passed else "FAIL",
                detail_text,
                str(export_actual),
            )

        return "ERROR", "Unsupported test type", ""

    except Exception as exc:  # noqa: BLE001
        return "ERROR", str(exc), ""
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
