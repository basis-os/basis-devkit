from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING, Dict, TypeVar

import json
import pydantic
import yaml


class PydanticBase(pydantic.BaseModel):
    class Config:
        extra = "forbid"
        use_enum_values = True
        allow_population_by_field_name = True


class FrozenPydanticBase(PydanticBase):
    class Config:
        frozen = True


class ImproperlyConfigured(Exception):
    pass


F = TypeVar("F", bound=FrozenPydanticBase)


def update(o: F, **kwargs) -> F:
    d = o.dict()
    d.update(**kwargs)
    return type(o)(**d)


def load_yaml(yml: str) -> Dict:

    try:
        from yaml import CLoader as Loader, CDumper as Dumper
    except ImportError:
        from yaml import Loader, Dumper
    if "\n" not in yml and (yml.endswith(".yml") or yml.endswith(".yaml")):
        with open(yml) as f:
            yml = f.read()
    return yaml.load(yml, Loader=Loader)


def dump_yaml(d: Dict) -> str:
    try:
        from yaml import CLoader as Loader, CDumper as Dumper
    except ImportError:
        from yaml import Loader, Dumper
    io = StringIO()
    yaml.dump(d, io, Dumper=Dumper)
    return io.getvalue()


def dump_json(obj: PydanticBase) -> str:
    return json.dumps(obj.dict(exclude_unset=True, exclude_none=True))

