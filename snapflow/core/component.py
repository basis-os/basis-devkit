from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Union

from types import ModuleType

from commonmodel import Schema
from dcp.utils.common import AttrDict

if TYPE_CHECKING:
    from snapflow.core.snap import (
        SnapLike,
        _Snap,
    )
    from snapflow.core.module import SnapflowModule


class DictView(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__ = self


class ComponentLibrary:
    snaps: Dict[str, _Snap]
    schemas: Dict[str, Schema]
    module_lookup_names: List[str]

    def __init__(self, namespace_lookup_keys: List[str] = None):
        from snapflow.core.module import DEFAULT_LOCAL_NAMESPACE

        self.snaps = {}
        self.schemas = {}
        self.module_lookup_names = [DEFAULT_LOCAL_NAMESPACE]
        if namespace_lookup_keys:
            for k in namespace_lookup_keys:
                self.add_namespace(k)

    def add_namespace(self, k: str):
        if k not in self.module_lookup_names:
            self.module_lookup_names.append(k)

    def add_module(self, module: Union[SnapflowModule, ModuleType]):
        if isinstance(module, ModuleType):
            module = module.module
        self.merge(module.library)

    def add_snap(self, p: _Snap):
        self.snaps[p.key] = p

    def add_schema(self, schema: Schema):
        self.schemas[schema.key] = schema

    def remove_snap(self, snap_like: Union[_Snap, str]):
        from snapflow.core.snap import _Snap

        if isinstance(snap_like, _Snap):
            snap_like = snap_like.key
        if snap_like not in self.snaps:
            return
        del self.snaps[snap_like]

    def get_snap(self, snap_like: Union[_Snap, str], try_module_lookups=True) -> _Snap:
        from snapflow.core.snap import _Snap

        if isinstance(snap_like, _Snap):
            return snap_like
        if not isinstance(snap_like, str):
            raise TypeError(snap_like)
        try:
            return self.snaps[snap_like]
        except KeyError as e:
            if try_module_lookups:
                return self.namespace_lookup(self.snaps, snap_like)
            raise e

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
                return self.namespace_lookup(self.schemas, schema_like)
            raise e

    def namespace_lookup(self, d: Dict[str, Any], k: str) -> Any:
        if "." in k:
            raise KeyError(k)
        for m in self.module_lookup_names:
            try:
                return d[m + "." + k]
            except KeyError:
                pass
        raise KeyError(f"`{k}` not found in modules {self.module_lookup_names}")

    def all_snaps(self) -> List[_Snap]:
        return list(self.snaps.values())

    def all_schemas(self) -> List[Schema]:
        return list(self.schemas.values())

    def merge(self, other: ComponentLibrary):
        self.snaps.update(other.snaps)
        self.schemas.update(other.schemas)
        for k in other.module_lookup_names:
            self.add_namespace(k)

    def get_view(self, d: Dict) -> DictView[str, Any]:
        ad: DictView = DictView()
        for k, p in d.items():
            # ad[k] = p
            ad[k.split(".")[-1]] = p  # TODO: module precedence
        return ad

    def get_snaps_view(self) -> DictView[str, _Snap]:
        return self.get_view(self.snaps)

    def get_schemas_view(self) -> DictView[str, Schema]:
        return self.get_view(self.schemas)
