from __future__ import annotations

import importlib
from types import ModuleType
from typing import TYPE_CHECKING, Any, Dict, List, Union

from basis.utils.modules import find_all_of_type_in_module
from commonmodel import Schema
from commonmodel.api import find_schema, register_schema
from loguru import logger

if TYPE_CHECKING:
    from basis.core.declarative.environment import ComponentLibraryCfg
    from basis.core.function import Function
    from basis.core.declarative.flow import FlowCfg


DEFAULT_LOCAL_NAMESPACE = "_local"
DEFAULT_NAMESPACE = DEFAULT_LOCAL_NAMESPACE


class DictView(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__ = self


class ComponentLibrary:
    functions: Dict[str, Function]
    schemas: Dict[str, Schema]
    # flows: Dict[str, FlowCfg]
    namespace_precedence: List[str]

    def __init__(
        self,
        namespace_precedence: List[str] = None,
        use_global_schema_lookup: bool = True,
    ):
        self.functions = {}
        self.schemas = {}
        self.flows = {}
        self.namespace_precedence = [DEFAULT_LOCAL_NAMESPACE]
        self.use_global_schema_lookup = use_global_schema_lookup
        if namespace_precedence:
            for k in namespace_precedence:
                self.add_namespace(k)

    @classmethod
    def from_config(cls, cfg: ComponentLibraryCfg) -> ComponentLibrary:
        from basis.core.function_package import load_function_from_source_file

        lib = ComponentLibrary(namespace_precedence=cfg.namespace_precedence)
        for f in cfg.source_file_functions:
            lib.add_function(load_function_from_source_file(f))
        for s in cfg.schemas:
            lib.add_schema(s)
        # for f in cfg.flows:
        #     lib.add_flow(f)
        return lib

    def add_namespace(self, k: str):
        k = k.split(".")[0]
        if k not in self.namespace_precedence:
            self.namespace_precedence.append(k)

    # def add_module(self, module: Union[BasisModule, ModuleType]):
    #     from basis.core.module import BasisModule

    #     if isinstance(module, ModuleType):
    #         sf_modules = find_all_of_type_in_module(module, BasisModule)
    #         if len(sf_modules) == 0:
    #             return self.find_and_add_from_module(module)
    #         else:
    #             module = sf_modules[0]
    #     self.merge(module.library)

    def add_function(self, f: Function):
        self.functions[f.name] = f

    def add_schema(self, schema: Schema):
        self.add_namespace(schema.key)
        self.schemas[schema.key] = schema
        register_schema(schema)

    # def add_flow(self, f: FlowCfg):
    #     self.add_namespace(f.key)
    #     self.flows[f.key] = f

    # def find_and_add_from_module(self, module: ModuleType):
    #     from basis.core.function import Function
    #     from basis.core.declarative.flow import FlowCfg

    #     for fn in find_all_of_type_in_module(module, Function):
    #         self.add_function(fn)
    #     for s in find_all_of_type_in_module(module, Schema):
    #         self.add_schema(s)
    #     for f in find_all_of_type_in_module(module, FlowCfg):
    #         self.add_flow(f)

    # def remove_function(self, function_like: Union[Function, str]):
    #     from basis.core.function import Function

    #     if isinstance(function_like, Function):
    #         function_like = function_like.key
    #     if function_like not in self.functions:
    #         return
    #     del self.functions[function_like]

    def find_function_in_module_path(self, module_path: str) -> Function:
        from basis.core.function_package import find_single_function

        # First see if path is to python module w function inside
        try:
            logger.debug(f"looking for function in possible module {module_path}")
            mod = importlib.import_module(module_path)
            return find_single_function(mod)
        except (ModuleNotFoundError, AssertionError, ValueError):  # TODO: error
            pass
        # Next try as path to package, with module of same name, with function of same name....
        mods = module_path.split(".")
        name = mods[-1]
        try:
            nested_path = module_path + "." + name
            logger.debug(
                f"looking for function in possible (parent) module {nested_path}"
            )
            mod = importlib.import_module(nested_path)
            return find_single_function(mod)
        except (ModuleNotFoundError, ValueError):  # TODO: error
            pass
        # Next try as full path to function
        try:
            mod_path_parent = ".".join(mods[:-1])
            logger.debug(
                f"looking for function in possible (parent) module {mod_path_parent}"
            )
            mod = importlib.import_module(mod_path_parent)
            return getattr(mod, name)
        except (ModuleNotFoundError, ValueError):  # TODO: error
            pass
        # Finally look for the name as local
        return globals()[module_path]

    def get_function(
        self, function_like: Union[Function, str], try_module_lookups=True
    ) -> Function:
        from basis.core.function import Function

        if isinstance(function_like, Function):
            return function_like
        if not isinstance(function_like, str):
            raise TypeError(function_like)
        try:
            return self.functions[function_like]
        except KeyError as e:
            pass
            # import_lib.import
            # if try_module_lookups:
            #     return self.namespace_lookup(self.functions, function_like)
            # raise e
        fn = self.find_function_in_module_path(function_like)
        self.functions[function_like] = fn
        return fn

    def get_schema(
        self, schema_like: Union[Schema, str], try_module_lookups=True
    ) -> Schema:

        if isinstance(schema_like, Schema):
            return schema_like
        if not isinstance(schema_like, str):
            raise TypeError(schema_like)
        try:
            return self.schemas[schema_like]
        except KeyError as e:
            if try_module_lookups:
                try:
                    return self.namespace_lookup(self.schemas, schema_like)
                except KeyError:
                    pass
            if self.use_global_schema_lookup:
                s = find_schema(schema_like)
                if s is not None:
                    return s
            raise e

    # def get_flow(
    #     self, flow_like: Union[FlowCfg, str], try_module_lookups=True
    # ) -> FlowCfg:
    #     from basis.core.declarative.flow import FlowCfg

    #     if isinstance(flow_like, FlowCfg):
    #         return flow_like
    #     if not isinstance(flow_like, str):
    #         raise TypeError(flow_like)
    #     try:
    #         return self.flows[flow_like]
    #     except KeyError as e:
    #         if try_module_lookups:
    #             return self.namespace_lookup(self.flows, flow_like)
    #         raise e

    def namespace_lookup(self, d: Dict[str, Any], k: str) -> Any:
        if "." in k:
            raise KeyError(k)
        for m in self.namespace_precedence:
            try:
                return d[m + "." + k]
            except KeyError:
                pass
        raise KeyError(f"`{k}` not found in modules {self.namespace_precedence}")

    # def all_functions(self) -> List[Function]:
    #     return list(self.functions.values())

    # TODO: doesn't include globals
    # def all_schemas(self) -> List[Schema]:
    #     return list(self.schemas.values())

    def merge(self, other: ComponentLibrary):
        self.functions.update(other.functions)
        self.schemas.update(other.schemas)
        # self.flows.update(other.flows)
        for k in other.namespace_precedence:
            self.add_namespace(k)

    # def get_view(self, d: Dict) -> DictView[str, Any]:
    #     ad: DictView = DictView()
    #     for k, p in d.items():
    #         # ad[k] = p
    #         ad[k.split(".")[-1]] = p  # TODO: module precedence
    #     return ad

    # def get_functions_view(self) -> DictView[str, Function]:
    #     return self.get_view(self.functions)

    # def get_schemas_view(self) -> DictView[str, Schema]:
    #     return self.get_view(self.schemas)


global_library = ComponentLibrary()
