from typing import List, Optional

from loguru import logger
from sqlalchemy.engine import Engine

from snapflow.db.api import DatabaseAPI, conform_columns_for_insert

MYSQL_SUPPORTED = False
try:
    import mysqlclient

    MYSQL_SUPPORTED = True
except ImportError:
    pass


class MysqlDatabaseAPI(DatabaseAPI):
    pass
