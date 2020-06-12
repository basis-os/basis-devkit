from collections import Generator, Iterable
from typing import List

from sqlalchemy.engine import ResultProxy, RowProxy

from basis.core.data_format import RecordsList


def result_proxy_to_records_list(
    result_proxy: ResultProxy, rows: List[RowProxy] = None
) -> RecordsList:
    if not rows:
        rows = result_proxy
    return [{k: v for k, v in zip(result_proxy.keys(), row)} for row in result_proxy]


def db_result_batcher(result_proxy: ResultProxy, batch_size: int = 1000) -> Generator:
    while True:
        rows = result_proxy.fetchmany(batch_size)
        yield result_proxy_to_records_list(result_proxy, rows)
        if len(rows) < batch_size:
            return
