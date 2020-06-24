from loguru import logger
from typing import List

from sqlalchemy.engine import Engine

from basis.core.data_format import RecordsList
from basis.core.sql.utils import compile_jinja_sql_template
from basis.db.api import DatabaseAPI, conform_columns_for_insert
from basis.utils.common import printd, title_to_snake_case
from basis.utils.data import conform_records_for_insert

try:
    from psycopg2.extras import execute_values
except ImportError:

    def execute_values(*args):
        raise ImportError("Psycopg2 not installed")


def bulk_insert(*args, **kwargs):
    kwargs["update"] = False
    return bulk_upsert(*args, **kwargs)


def bulk_upsert(
    eng: Engine,
    table_name: str,
    records: RecordsList,
    unique_on_column: str = None,
    ignore_duplicates: bool = False,
    update: bool = True,
    columns: List[str] = None,
    adapt_objects_to_json: bool = True,
    convert_columns_to_snake_case: bool = True,
    page_size: int = 5000,
):
    if not records:
        return
    if update and not unique_on_column:
        raise Exception("Must specify unique_on_column when updating")
    columns = conform_columns_for_insert(
        records, columns, convert_columns_to_snake_case
    )
    records = conform_records_for_insert(records, columns, adapt_objects_to_json)
    if update:
        tmpl = "templates/bulk_upsert.sql"
    else:
        tmpl = "templates/bulk_insert.sql"
    jinja_ctx = {
        "table_name": table_name,
        "columns": columns,
        "records": records,
        "unique_on_column": unique_on_column,
        "ignore_duplicates": ignore_duplicates,
    }
    sql = compile_jinja_sql_template(tmpl, jinja_ctx)
    logger.debug("SQL", sql)
    pg_execute_values(eng, sql, records, page_size=page_size)


def pg_execute_values(
    eng: Engine, sql: str, records: RecordsList, page_size: int = 5000
):
    conn = eng.raw_connection()
    try:
        with conn.cursor() as curs:
            execute_values(
                curs, sql, records, template=None, page_size=page_size,
            )
    except Exception as e:
        conn.rollback()
        raise e
    else:
        conn.commit()
    finally:
        conn.close()


class PostgresDatabaseAPI(DatabaseAPI):
    def _bulk_insert(self, table_name: str, records: RecordsList, **kwargs):
        bulk_insert(
            eng=self.get_engine(), table_name=table_name, records=records, **kwargs
        )
