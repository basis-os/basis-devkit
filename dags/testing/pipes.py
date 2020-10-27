from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Generator, List, Optional, Tuple, Union

from pandas import DataFrame
from sqlalchemy.orm import close_all_sessions  # type: ignore

from dags.core.environment import Environment
from dags.core.graph import Graph
from dags.core.pipe import PipeLike, ensure_pipe
from dags.core.streams import InputBlocks
from dags.core.typing.inference import (
    conform_dataframe_to_schema,
    infer_schema_from_records_list,
)
from dags.core.typing.object_schema import ObjectSchemaLike
from dags.db.api import dispose_all
from dags.utils.common import cf, printd, rand_str
from dags.utils.data import read_csv, read_json
from dags.utils.pandas import (
    assert_dataframes_are_almost_equal,
    records_list_to_dataframe,
)
from loguru import logger

if TYPE_CHECKING:
    from dags.core.module import DagsModule


@dataclass(frozen=True)
class TestDataBlock:
    schema_like: ObjectSchemaLike
    data_frame: DataFrame
    data_raw: Optional[str] = None


class PipeTestCase:
    def __init__(
        self,
        name: str,
        pipe: Union[PipeLike, str],
        test_datas: List[Dict[str, TestDataBlock]],
        ignored_fields: List[str] = None,
        pipe_config: Dict = None,
    ):
        self.name = name
        self.pipe = pipe
        self.pipe_config = pipe_config
        self.test_datas = test_datas
        self.ignored_fields = ignored_fields or []

    def as_input_blocks(self, env: Environment) -> InputBlocks:
        raise


class PipeTest:
    def __init__(
        self,
        pipe: Union[PipeLike, str],
        tests: List[Dict],
        pipe_config: Dict = None,
        module: DagsModule = None,
    ):
        self.pipe = pipe
        self.module = module
        self._raw_tests = tests
        self.pipe_config = pipe_config

    def get_tests(self) -> List[PipeTestCase]:
        return self.process_raw_tests(self._raw_tests)

    def process_raw_tests(self, test_cases: List[Dict]) -> List[PipeTestCase]:
        cases: List[PipeTestCase] = []
        for case in test_cases:
            test_name = case["name"]
            if isinstance(case["test_data"], list):
                test_datas = case["test_data"]
            else:
                test_datas = [case["test_data"]]
            processed_test_datas: List[Dict[str, TestDataBlock]] = []
            for test_data in test_datas:
                test_data_blocks = {}
                for input_name, data_block_like in test_data.items():
                    data = data_block_like.get("data")
                    schema = data_block_like.get("schema")
                    if data:
                        df = self.process_raw_test_data_into_dataframe(data)
                    else:
                        df = None
                    test_data_blocks[input_name] = TestDataBlock(
                        schema_like=schema, data_frame=df, data_raw=data
                    )
                processed_test_datas.append(test_data_blocks)
            dfcase = PipeTestCase(
                name=test_name,
                pipe=self.pipe,
                test_datas=processed_test_datas,
                ignored_fields=case.get("ignored_fields", []),
            )
            cases.append(dfcase)
        return cases

    def process_raw_test_data_into_dataframe(self, test_data: str) -> DataFrame:
        if test_data.endswith(".csv"):
            if not self.module:
                raise
            with self.module.open_module_file(test_data) as f:
                raw_records = read_csv(f.readlines())
        elif test_data.endswith(".json"):
            if not self.module:
                raise
            with self.module.open_module_file(test_data) as f:
                raw_records = [read_json(line) for line in f]
        else:
            # Raw str csv
            lines = [ln.strip() for ln in test_data.split("\n") if ln.strip()]
            assert lines, "Empty test data"
            raw_records = read_csv(lines)
        auto_schema = infer_schema_from_records_list(raw_records)
        df = records_list_to_dataframe(raw_records, auto_schema)
        return df

    @contextmanager
    def test_env(self, **kwargs: Any) -> Generator[Environment, None, None]:
        # TODO: need more hooks here (adding runtimes and storages, for instance)
        from dags.core.environment import Environment
        from dags.db.api import create_db, drop_db
        from dags.modules import core

        # TODO: Can we use sqlite?
        # TODO: check for pg support at least
        db_name = f"__test_{rand_str(10).lower()}"
        conn_url = "postgres://postgres@localhost:5432/postgres"
        db_url = f"postgres://postgres@localhost:5432/{db_name}"
        # conn_url = f"sqlite:///" + db_name + ".db"
        # try:
        #     drop_db(conn_url, db_name)
        # except Exception as e:
        #     pass
        create_db(conn_url, db_name)
        initial_modules = [core] + kwargs.pop("initial_modules", [])

        env = Environment(
            db_name, metadata_storage=db_url, initial_modules=initial_modules, **kwargs
        )
        env.add_storage(db_url)
        try:
            yield env
        finally:
            close_all_sessions()
            dispose_all()
            drop_db(conn_url, db_name)

    def run(self, **env_args: Any):
        # TODO: clean this pipe up
        for case in self.get_tests():
            print(f"{case.name}:")
            with self.test_env(**env_args) as env:
                fn = ensure_pipe(env, self.pipe)
                dfi = fn.get_interface(env)
                assert dfi is not None
                for i, test_data in enumerate(case.test_datas):
                    g = Graph(env)
                    try:
                        inputs = {}
                        for input in dfi.inputs:
                            assert input.name is not None
                            test_df = test_data[input.name].data_frame
                            test_schema = test_data[input.name].schema_like
                            n = g.add_node(
                                f"_test_source_node_{input.name}_{i}",
                                "core.extract_dataframe",
                                config={"dataframe": test_df, "schema": test_schema},
                            )
                            inputs[input.name] = n
                        test_node = g.add_node(
                            "_test_node", fn, config=case.pipe_config, inputs=inputs
                        )
                        # test_node._set_declared_inputs(
                        #     inputs
                        # )  # Force, for testing. Normally want node to be immutable
                        output = env.produce(g, test_node, to_exhaustion=False)
                        if "output" in test_data:
                            assert (
                                output is not None
                            ), "Output is None, expected DataBlock"
                            output_df = output.as_dataframe()
                            # output_df.to_csv("out.csv")
                            expected_df = test_data["output"].data_frame
                            expected_schema = env.get_schema(
                                test_data["output"].schema_like
                            )
                            # TODO: conform cleanup
                            conform_dataframe_to_schema(expected_df, expected_schema)
                            conform_dataframe_to_schema(output_df, expected_schema)
                            logger.debug("Output", output_df)
                            logger.debug("Expected", expected_df)
                            assert_dataframes_are_almost_equal(
                                output_df,
                                expected_df,
                                expected_schema,
                                ignored_columns=case.ignored_fields,
                            )
                        else:
                            assert output is None, f"Unexpected output {output}"
                        print(cf.success("Ok"))
                    except Exception as e:
                        print(cf.error("Fail:"), str(e))
                        raise e


PipeTestCaseLike = Union[PipeTestCase, str]


# def test_cases_from_yaml(yml: str, module: DagsModule) -> List[PipeTestCase]:
#     d = strictyaml.load(yml).data
#     fn = d.get("pipe")
#     fn = module.get_pipe(fn)
#     tests = d.get("tests")
#     cases = []
#     for test_name, test_inputs in tests.items():
#         test_data = {}
#         for input_name, data in test_inputs.items():
#             test_data[input_name] = pd.read_csv(StringIO(data.strip()))
#         PipeTestCase(
#             name=test_name, pipe=fn, test_data=test_data,
#         )
#     return cases