from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from io import TextIOBase
from typing import (
    TYPE_CHECKING,
    Any,
    Generator,
    Iterable,
    List,
    Optional,
    Sequence,
    TextIO,
    Type,
    Union,
)

from dags.core.component import ComponentLibrary
from dags.core.typing.object_schema import (
    ObjectSchema,
    ObjectSchemaLike,
    schema_from_yaml,
)
from dags.utils.common import AttrDict

if TYPE_CHECKING:
    from dags.core.pipe import (
        PipeLike,
        Pipe,
        make_pipe,
    )
    from dags.testing.pipes import PipeTest

DEFAULT_LOCAL_MODULE_NAME = "_local_"


class DagsModule:
    name: str
    py_module_path: Optional[str]
    py_module_name: Optional[str]
    library: ComponentLibrary
    test_cases: List[PipeTest]
    dependencies: List[DagsModule]

    def __init__(
        self,
        name: str,
        py_module_path: Optional[str] = None,
        py_module_name: Optional[str] = None,
        schemas: Optional[Sequence[ObjectSchemaLike]] = None,
        pipes: Optional[Sequence[Union[PipeLike, str]]] = None,
        tests: Optional[Sequence[PipeTest]] = None,
        dependencies: List[
            DagsModule
        ] = None,  # TODO: support str references to external deps (will need repo hooks...)
    ):

        self.name = name
        if py_module_path:
            py_module_path = os.path.dirname(py_module_path)
        self.py_module_path = py_module_path
        self.py_module_name = py_module_name
        self.library = ComponentLibrary(module_lookup_keys=[self.name])
        self.test_cases = []
        self.dependencies = []
        for schema in schemas or []:
            self.add_schema(schema)
        for fn in pipes or []:
            self.add_pipe(fn)
        for t in tests or []:
            self.add_test(t)
        for d in dependencies or []:
            self.add_dependency(d)
        # for t in tests or []:
        #     self.add_test_case(t)

    # TODO: implement dir for usability
    # def __dir__(self):
    #     return list(self.members().keys())

    @contextmanager
    def open_module_file(self, fp: str) -> Generator[TextIO, None, None]:
        if not self.py_module_path:
            raise Exception(f"Module path not set, cannot read {fp}")
        typedef_path = os.path.join(self.py_module_path, fp)
        with open(typedef_path) as f:
            yield f

    def get_schema(self, schema_like: ObjectSchemaLike) -> ObjectSchema:
        if isinstance(schema_like, ObjectSchema):
            return schema_like
        return self.library.get_schema(schema_like)

    def get_pipe(self, pipe_like: Union[Pipe, str]) -> Pipe:
        from dags.core.pipe import Pipe

        if isinstance(pipe_like, Pipe):
            return pipe_like
        return self.library.get_pipe(pipe_like)

    def export(self):
        if self.py_module_name is None:
            raise Exception("Cannot export module, no module_name set")
        sys.modules[self.py_module_name] = self  # type: ignore  # sys.module_lookup_names wants a modulefinder.Module type and it's not gonna get it

    @property
    def schemas(self) -> AttrDict[str, ObjectSchema]:
        return self.library.get_schemas_view()

    @property
    def pipes(self) -> AttrDict[str, Pipe]:
        return self.library.get_pipes_view()

    def validate_key(self, obj: Any):
        pass  # TODO: make the name whatever you want? idk
        # if not obj.name.startswith(self.name + "."):
        #     raise ValueError("Must prefix component name with module name")

    def add_schema(self, schema_like: ObjectSchemaLike) -> ObjectSchema:
        schema = self.process_schema(schema_like)
        self.validate_key(schema)
        self.library.add_schema(schema)
        return schema

    def process_schema(self, schema_like: ObjectSchemaLike) -> ObjectSchema:
        if isinstance(schema_like, ObjectSchema):
            schema = schema_like
        elif isinstance(schema_like, str):
            with self.open_module_file(schema_like) as f:
                yml = f.read()
                schema = schema_from_yaml(yml, module_name=self.name)
        else:
            raise TypeError(schema_like)
        return schema

    def add_pipe(self, pipe_like: Union[PipeLike, str]) -> Pipe:
        p = self.process_pipe(pipe_like)
        self.validate_key(p)
        self.library.add_pipe(p)
        return p

    def process_pipe(self, pipe_like: Union[PipeLike, str]) -> Pipe:
        from dags.core.pipe import (
            Pipe,
            make_pipe,
            Pipe,
        )
        from dags.core.sql.pipe import sql_pipe

        if isinstance(pipe_like, Pipe):
            pipe = pipe_like
        else:
            if callable(pipe_like):
                pipe = make_pipe(pipe_like)
            elif isinstance(pipe_like, str) and pipe_like.endswith(".sql"):
                if not self.py_module_path:
                    raise Exception(
                        f"Module path not set, cannot read sql definition {pipe_like}"
                    )
                sql_file_path = os.path.join(self.py_module_path, pipe_like)
                with open(sql_file_path) as f:
                    sql = f.read()
                file_name = os.path.basename(pipe_like)[:-4]
                pipe = sql_pipe(
                    name=file_name, module=self.name, sql=sql
                )  # TODO: versions, runtimes, etc for sql (someway to specify in a .sql file)
            else:
                raise TypeError(pipe_like)
        return pipe

    def add_test(self, test_case: PipeTest):
        test_case.module = self
        self.test_cases.append(test_case)

    def run_tests(self):
        print(f"Running tests for module {self.name}")
        for test in self.test_cases:
            print(f"======= {test.pipe} =======")
            try:
                test.run(initial_modules=[self] + self.dependencies)
            except Exception as e:
                print(e)
                raise e

    def add_dependency(self, m: DagsModule):
        # if isinstance(m, DagsModule):
        #     m = m.name
        self.dependencies.append(m)

    # def add_test_case(self, test_case_like: PipeTestCaseLike):
    #     test_case = self.process_test_case(test_case_like)
    #     self.test_cases.extend(test_case)
    #
    # def process_test_case(
    #     self, test_case_like: PipeTestCaseLike
    # ) -> List[PipeTestCase]:
    #     from dags.testing.pipes import (
    #         PipeTestCaseLike,
    #         PipeTestCase,
    #         test_cases_from_yaml,
    #     )
    #
    #     if isinstance(test_case_like, PipeTestCase):
    #         test_cases = [test_case_like]
    #     elif isinstance(test_case_like, str):
    #         yml = self.read_module_file(test_case_like)
    #         test_cases = test_cases_from_yaml(yml, self)
    #     else:
    #         raise TypeError(test_case_like)
    #     return test_cases

    # def get_indexable_components(self) -> Iterable[IndexableComponent]:
    #     dti = self.schema_indexer()
    #     si = self.provider_indexer()
    #     dfi = self.pipe_indexer()
    #     for schema in self.schemas.all():
    #         for ic in dti.get_indexable_components(schema, self):
    #             yield ic
    #     for s in self.providers.all():
    #         for ic in si.get_indexable_components(s, self):
    #             yield ic
    #     for df in self.pipes.all():
    #         for ic in dfi.get_indexable_components(df, self):
    #             yield ic


DEFAULT_LOCAL_MODULE = DagsModule(DEFAULT_LOCAL_MODULE_NAME)