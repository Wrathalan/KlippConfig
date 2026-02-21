from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath


class ConfigGraphService:
    _INCLUDE_PATTERN = re.compile(
        r"^\s*\[include\s+([^\]]+)\]\s*(?:[#;].*)?$",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = path.replace("\\", "/").strip()
        if not raw:
            return ""
        normalized = posixpath.normpath(raw)
        if raw.startswith("/") and not normalized.startswith("/"):
            return f"/{normalized}"
        return normalized

    @staticmethod
    def _expand_relative_path(base_file: str, include_target: str) -> str:
        include_value = include_target.strip().strip("'\"")
        if not include_value:
            return ""
        include_value = include_value.replace("\\", "/")
        if include_value.startswith("/"):
            return ConfigGraphService._normalize_path(include_value)
        base_dir = posixpath.dirname(base_file) if base_file else "."
        return ConfigGraphService._normalize_path(posixpath.join(base_dir, include_value))

    @staticmethod
    def _is_glob_pattern(path: str) -> bool:
        return any(ch in path for ch in ("*", "?", "["))

    def resolve_includes(self, file_path: str, content: str) -> list[str]:
        normalized_file = self._normalize_path(file_path)
        includes: list[str] = []
        for raw_line in (content or "").splitlines():
            match = self._INCLUDE_PATTERN.match(raw_line)
            if not match:
                continue
            resolved = self._expand_relative_path(normalized_file, match.group(1))
            if resolved:
                includes.append(resolved)
        return includes

    def build_graph(self, files: dict[str, str], root_file: str) -> dict[str, list[str]]:
        normalized_files: dict[str, str] = {
            self._normalize_path(path): content for path, content in files.items()
        }
        normalized_root = self._normalize_path(root_file)
        all_file_paths = sorted(normalized_files.keys())
        graph: dict[str, list[str]] = {}
        visited: set[str] = set()

        def visit(node: str) -> None:
            if not node or node in visited:
                return
            visited.add(node)
            content = normalized_files.get(node, "")
            resolved_targets = self.resolve_includes(node, content)
            edges: list[str] = []
            for target in resolved_targets:
                if self._is_glob_pattern(target):
                    matches = sorted(
                        candidate
                        for candidate in all_file_paths
                        if PurePosixPath(candidate).match(target)
                    )
                    if matches:
                        edges.extend(matches)
                    else:
                        edges.append(target)
                else:
                    edges.append(target)
            deduped_edges = list(dict.fromkeys(edges))
            graph[node] = deduped_edges
            for child in deduped_edges:
                if child in normalized_files:
                    visit(child)

        visit(normalized_root)
        if normalized_root not in graph:
            graph[normalized_root] = []
        return graph

    def flatten_graph(self, graph: dict[str, list[str]], root_file: str) -> list[str]:
        root = self._normalize_path(root_file)
        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if not node or node in visited:
                return
            if node in visiting:
                return
            visiting.add(node)
            order.append(node)
            for child in graph.get(node, []):
                dfs(child)
            visiting.remove(node)
            visited.add(node)

        dfs(root)
        if root and root not in order:
            order.insert(0, root)
        return order
