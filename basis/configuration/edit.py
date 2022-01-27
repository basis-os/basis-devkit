from __future__ import annotations

import functools
import io
import re
from io import StringIO
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, IO, Iterator, Callable
from zipfile import ZipFile, ZipInfo

import ruyaml

from basis.cli.helpers import compress_directory, random_node_id
from basis.cli.services.graph import resolve_graph_path
from basis.configuration.graph import NodeCfg, ExposingCfg, GraphDefinitionCfg
from basis.configuration.path import NodeId
from basis.graph.builder import graph_manifest_from_yaml
from basis.graph.configured_node import GraphManifest

MISSING = object()


class GraphConfigEditor:
    """Edit a graph.yml file, preserving comments

    By default, constructing an instance of this class will raise an exception if the
    yaml file doesn't exist or can't be parsed.

    If you pass `read=False` to the constructor, the file won't be read, and you'll
    start with an empty config.

    You can also pass `path_to_graph_yml=None` if you aren't going to write back to
    disk.
    """

    def __init__(self, path_to_graph_yml: Optional[Path], read: bool = True):
        self._yaml = ruyaml.YAML()
        self._path_to_graph_yml = path_to_graph_yml
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        # read text manually instead of loading the Path directly to normalize line
        # breaks. Ruyaml opens files in binary mode (bypassing universal newline
        # support), then proceeds to behave incorrectly in the presence of \r\n, adding
        # extra line breaks in the output.
        if read:
            with self._path_to_graph_yml.open() as f:
                text = f.read()
            self._cfg = self._yaml.load(text) or {}
            # ruyaml doesn't provide a way to preserve indentation,
            # so pick a value that matches the first list item we see
            if m := re.search(r"^( *)-", text, re.MULTILINE):
                indent = len(m.group(1)) + 2
            else:
                indent = 4
        else:
            self._cfg = {}
            indent = 4

        self._yaml.indent(
            mapping=int(indent / 2), sequence=indent, offset=max(0, indent - 2)
        )

    def write(self):
        """Write the config back to the file"""
        self._yaml.dump(self._cfg, self._path_to_graph_yml)

    def dump(self) -> str:
        """Return the edited config as a yaml string"""
        s = StringIO()
        self._yaml.dump(self._cfg, s)
        return s.getvalue()

    def parse_to_cfg(self) -> GraphDefinitionCfg:
        """Parse the data to a GraphDefinitionCfg without writing it to disk"""
        return GraphDefinitionCfg(**self._cfg)

    def set_name(self, name: str) -> GraphConfigEditor:
        self._cfg["name"] = name
        return self

    def get_name(self) -> Optional[str]:
        return self._cfg.get("name")

    def get_exposing_cfg(self) -> Optional[ExposingCfg]:
        if "exposes" in self._cfg:
            return ExposingCfg(**self._cfg["exposes"])
        return None

    def set_exposing_cfg(self, exposing: Optional[ExposingCfg]) -> GraphConfigEditor:
        if exposing is None:
            del self._cfg["exposes"]
        else:
            self._cfg["exposes"] = exposing.dict(exclude_none=True)
        return self

    def add_node_cfg(self, node: NodeCfg) -> GraphConfigEditor:
        d = node.dict(exclude_none=True)

        for k in ("node_file", "id", "webhook"):
            if (
                k in d
                and d[k]
                and any(it.get(k) == d[k] for it in self._cfg.get("nodes", []))
            ):
                raise ValueError(
                    f"{k} '{d[k]}' already defined in the graph configuration"
                )

        # ruyaml refuses to dump anything that isn't a built-in type, even subclasses of
        # them, so we have to map all the inputs and outputs to strings
        for k in ("inputs", "outputs"):
            p = d.get(k, None)
            if p is None:
                continue
            d[k] = [str(v) for v in p]

        if "nodes" not in self._cfg:
            self._cfg["nodes"] = []
        self._cfg["nodes"].append(d)
        return self

    def remove_node_with_id(
        self, node_id: str, default_id: Callable[[dict], str] = lambda _: {}
    ) -> GraphConfigEditor:
        """Remove node entry with a given id value

        Raise a KeyError if no such nodes exist
        You can pass a default_id function that returns ids for node entries that don't
        have them defined.
        """
        for i, node in enumerate(self._nodes()):
            id = node["id"] if "id" in node else default_id(node)
            if id == node_id:
                del self._cfg["nodes"][i]
                break
        else:
            raise KeyError(node_id)

        return self

    def add_node(
        self,
        node_file: str,
        schedule: str = None,
        inputs: List[str] = None,
        outputs: List[str] = None,
        parameters: Dict[str, Any] = None,
        name: str = None,
        id: Optional[str] = MISSING,
        description: str = None,
    ) -> GraphConfigEditor:
        if id is MISSING:
            id = NodeId.random()
        self.add_node_cfg(
            NodeCfg(
                node_file=node_file,
                schedule=schedule,
                inputs=inputs,
                outputs=outputs,
                parameters=parameters,
                name=name,
                id=str(id) if id else id,
                description=description,
            )
        )
        return self

    def add_webhook(
        self,
        webhook: str,
        name: str = None,
        id: Optional[str] = MISSING,
        description: str = None,
    ) -> GraphConfigEditor:
        if id is MISSING:
            id = NodeId.random()
        self.add_node_cfg(
            NodeCfg(
                webhook=webhook,
                name=name,
                id=str(id) if id else id,
                description=description,
            )
        )
        return self

    def add_missing_node_ids(self) -> GraphConfigEditor:
        """Add a random id to any node entry that doesn't specify one"""
        for node in self._nodes():
            if "id" not in node:
                node["id"] = random_node_id()
        return self

    def _nodes(self) -> Iterator[dict]:
        nodes = self._cfg.get("nodes")
        if not isinstance(nodes, list):
            return

        for node in nodes:
            if not isinstance(node, dict):
                continue
            yield node


class GraphDirectoryEditor:
    def __init__(self, graph_path: Path, overwrite: bool = False):
        """
        :param graph_path: The path to a graph.yml file, or a directory containing one
        :param overwrite: If False, raise an exception in add_node_from_zip if a node
            exists and differs from the extracted content.
        """
        try:
            self.yml_path = resolve_graph_path(graph_path, exists=True)
        except ValueError:
            self.yml_path = resolve_graph_path(graph_path, exists=False)
        self.dir = self.yml_path.parent
        self.overwrite = overwrite
        if self.yml_path.is_file():
            self._cfg = self._editor(self.yml_path)
        else:
            self._cfg = None

    def graph_name(self) -> str:
        """Return the name of the graph"""
        name = None
        if self._cfg:
            name = self._cfg.get_name()
        return name or self.yml_path.parent.name

    def compress_directory(self) -> io.BytesIO:
        """Return an in-memory zip file containing the compressed graph directory"""
        return compress_directory(self.dir)

    def build_manifest(self, allow_errors: bool = False) -> GraphManifest:
        """Build a graph manifest from the graph directory"""
        if self._cfg:
            self._cfg.write()
        return graph_manifest_from_yaml(self.yml_path, allow_errors=allow_errors)

    def add_node_from_file(self, dst_path: Union[Path, str], file: IO[bytes]):
        """Write the content of a file to dst_path

       :param dst_path: Path relative to the output graph directory
       :param file: A file-like object open in read mode
       """
        dst_path = Path(dst_path)
        self._write_file(dst_path, file)
        self._add_cfg_node(dst_path)

    def add_node_from_zip(
        self,
        src_path: Union[Path, str],
        dst_path: Union[Path, str],
        zf: Union[ZipFile, Path, IO[bytes]],
    ) -> GraphDirectoryEditor:
        """Copy the node or subgraph located at src_path in zipfile to dst_path

        :param src_path: Path relative to the root of zipfile
        :param dst_path: Path relative to the output graph directory
        :param zf: A ZipFile open in read mode, or a path to a zip file to open
        """
        src_path = Path(src_path)
        dst_path = Path(dst_path)
        if isinstance(zf, ZipFile):
            self._add(src_path, dst_path, zf)
        else:
            with ZipFile(zf, "r") as f:
                self._add(src_path, dst_path, f)
        return self

    def add_missing_node_ids(self) -> GraphDirectoryEditor:
        """Add a random id to any node entry that doesn't specify one

        This will update all graph.yml files in the directory
        """
        for editor in self._graph_editors():
            editor.add_missing_node_ids()
            editor.write()
        return self

    def _add(self, src_path: Path, dst_path: Path, zf: ZipFile):
        if src_path.name == "graph.yml":

            def dirname(p):
                if len(p.parts) <= 1:
                    return ""
                return _zip_name(p.parent) + "/"

            src_dir = dirname(src_path)
            dst_dir = dirname(dst_path)

            for info in zf.infolist():
                if info.filename.startswith(src_dir) and not info.is_dir():
                    new_name = dst_dir + info.filename[len(src_dir):]
                    self._extract_file(info, Path(new_name), zf)
        else:
            self._extract_file(zf.getinfo(_zip_name(src_path)), dst_path, zf)
        self._add_cfg_node(dst_path)

    def _add_cfg_node(self, dst_path: Path):
        if not self._cfg or str(dst_path) == "graph.yml":
            return
        try:
            self._cfg.add_node(_zip_name(dst_path)).write()
        except ValueError:
            pass  # node already exists, leave it unchanged

    def _extract_file(self, member: ZipInfo, dst_path: Path, zf: ZipFile):
        full_dst_path = self.dir / dst_path
        if full_dst_path.is_dir():
            raise ValueError(
                f"Cannot extract {dst_path}: a directory by that name exists"
            )
        if self.overwrite or not full_dst_path.is_file():
            member.filename = _zip_name(dst_path)
            zf.extract(member, self.dir)
        else:
            with zf.open(member, "r") as f:
                self._write_file(dst_path, f)

    def _write_file(self, dst_path: Path, file: IO[bytes]):
        full_dst_path = self.dir / dst_path
        new_content = io.TextIOWrapper(file).read()

        if not self.overwrite:
            try:
                old_content = full_dst_path.read_text()
            except FileNotFoundError:
                pass
            else:
                if new_content != old_content:
                    raise FileOverwriteError(
                        full_dst_path,
                        f"Cannot extract {dst_path}: would overwrite existing file",
                    )
        full_dst_path.write_text(new_content)

    def _graph_editors(self) -> Iterator[GraphConfigEditor]:
        for p in self.dir.rglob('graph.yml'):
            yield self._editor(p)

    @functools.lru_cache(maxsize=None)
    def _editor(self, yaml_path: Path) -> GraphConfigEditor:
        return GraphConfigEditor(yaml_path)


class FileOverwriteError(Exception):
    def __init__(self, file_path: Path, message: str) -> None:
        super().__init__(message)
        self.file_path = file_path


def _zip_name(p: Path):
    return "/".join(p.parts)
