"""
CodeOrbit analysis engine.

Walks a repository, builds a module dependency graph for Python
and JavaScript/TypeScript, then produces:

- nodes / edges for the architecture graph
- layered levels for architecture visualization
- Mermaid.js flowchart source
- architecture insights
- detected technology stack

Import resolution is heuristic and intended for architecture
visualization rather than full static analysis.
"""

import ast
import os
import re
from collections import defaultdict, deque


# ============================================================
# Configuration
# ============================================================

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    "egg-info",
    ".idea",
    ".vscode",
    "site-packages",
}

PY_EXT = {
    ".py",
}

JS_EXT = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}

CODE_EXT = PY_EXT | JS_EXT

ENTRY_NAMES = {
    "main.py",
    "app.py",
    "index.js",
    "server.js",
    "index.ts",
    "app.js",
}


KNOWN_LIBS = {
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "requests": "Requests",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "sqlalchemy": "SQLAlchemy",
    "pil": "Pillow",
    "pillow": "Pillow",
    "asyncio": "AsyncIO",
    "json": "JSON",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "express": "Express",
    "react": "React",
    "axios": "Axios",
    "pytest": "Pytest",
    "click": "Click",
    "pydantic": "Pydantic",
}


# ============================================================
# File discovery
# ============================================================

def _iter_source_files(root):
    """
    Yield:

        full_path, relative_path

    for supported source files.
    """

    for dirpath, dirnames, filenames in os.walk(root):

        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not d.startswith(".")
        ]

        for filename in filenames:

            ext = os.path.splitext(filename)[1]

            if ext not in CODE_EXT:
                continue

            full = os.path.join(
                dirpath,
                filename,
            )

            rel = os.path.relpath(
                full,
                root,
            ).replace(
                os.sep,
                "/",
            )

            yield full, rel


# ============================================================
# Module helpers
# ============================================================

def _dotted(rel_path):
    """
    Convert:

        generated_tools/calculator.py

    into:

        generated_tools.calculator
    """

    no_ext = re.sub(
        r"\.(py|js|jsx|ts|tsx)$",
        "",
        rel_path,
    )

    return no_ext.replace(
        "/",
        ".",
    )


# ============================================================
# Python import extraction
# ============================================================

def _py_imports(source):
    """
    Return Python module imports.

    Important:

        from app import app

    represents an import from the module/package:

        app

    It should NOT additionally generate:

        app.app

    Doing that can cause a nested project such as:

        sample_repo/app/

    to accidentally resolve `app.app` against an unrelated:

        backend/app.py
    """

    try:
        tree = ast.parse(source)

    except SyntaxError:
        return []

    found = []

    for node in ast.walk(tree):

        # ----------------------------------------------------
        # import foo
        # import foo.bar
        # ----------------------------------------------------

        if isinstance(
            node,
            ast.Import,
        ):

            for alias in node.names:

                found.append(
                    alias.name
                )

        # ----------------------------------------------------
        # from foo import bar
        # from foo.bar import baz
        # ----------------------------------------------------

        elif isinstance(
            node,
            ast.ImportFrom,
        ):

            if node.module:

                # Only record the actual module.
                #
                # from app import app
                #        ^^^
                #
                # produces:
                #
                # app
                #
                # rather than:
                #
                # app
                # app.app

                found.append(
                    node.module
                )

            else:

                # Handles:
                #
                # from . import foo
                #
                # Relative imports require the importing
                # file's location to resolve correctly.

                for alias in node.names:

                    found.append(
                        alias.name
                    )

    return list(
        dict.fromkeys(found)
    )


# ============================================================
# JavaScript / TypeScript import extraction
# ============================================================

def _js_imports(source):
    """
    Return JavaScript/TypeScript import and require targets.
    """

    found = []

    # CommonJS:
    #
    # require("express")
    # require("./routes")

    found += re.findall(
        r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
        source,
    )

    # ES modules:
    #
    # import express from "express"
    # import { router } from "./routes"
    # import * as utils from "./utils"
    # import "./config.js"

    found += re.findall(
        r"""import\s+(?:[\s\S]*?\s+from\s+)?['"]([^'"]+)['"]""",
        source,
    )

    return list(
        dict.fromkeys(found)
    )


# ============================================================
# Source metadata
# ============================================================

def _first_docstring_or_comment(
    source,
    ext,
):
    if ext == ".py":

        try:

            tree = ast.parse(
                source
            )

            doc = ast.get_docstring(
                tree
            )

            if doc:
                return (
                    doc.strip()
                    .split("\n")[0]
                )

        except SyntaxError:
            pass

    for line in source.splitlines()[:5]:

        line = line.strip()

        if line.startswith(
            (
                "//",
                "#",
                "/**",
                "*",
            )
        ):

            return line.lstrip(
                "/*# "
            ).strip()

    return None


def _py_symbols(source):
    funcs = []
    classes = []

    try:

        tree = ast.parse(
            source
        )

        for node in tree.body:

            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):

                funcs.append(
                    node.name
                )

            elif isinstance(
                node,
                ast.ClassDef,
            ):

                classes.append(
                    node.name
                )

    except SyntaxError:
        pass

    return funcs, classes


# ============================================================
# Python local resolution
# ============================================================

def _py_local_candidates(
    raw,
    from_rel,
    files,
):
    """
    Generate local Python module candidates.

    For example, if:

        from_rel =
        backend/sample_repo/app/routes.py

    and:

        raw = app

    this checks the importing file's directory first and
    then walks upward through parent directories.

    The nearest matching module wins.
    """

    if not raw:
        return

    module_path = raw.replace(
        ".",
        "/",
    )

    current_dir = os.path.dirname(
        from_rel
    )

    ancestors = []

    while True:

        ancestors.append(
            current_dir
        )

        if not current_dir:
            break

        parent = os.path.dirname(
            current_dir
        )

        if parent == current_dir:
            break

        current_dir = parent

    seen = set()

    for base_dir in ancestors:

        if base_dir:

            prefix = os.path.join(
                base_dir,
                module_path,
            ).replace(
                os.sep,
                "/",
            )

        else:

            prefix = module_path

        candidates = [
            prefix + ".py",
            prefix + "/__init__.py",
        ]

        for candidate in candidates:

            if (
                candidate in files
                and candidate not in seen
            ):

                seen.add(
                    candidate
                )

                yield candidate


# ============================================================
# Python import resolution
# ============================================================

def _resolve_python_import(
    raw,
    from_rel,
    dotted_index,
    basename_index,
    files,
):
    """
    Resolve a Python import.

    Resolution order:

    1. Nearest local project path
    2. Exact dotted module path
    3. Shorter dotted module paths
    4. Unique basename fallback
    """

    if not raw:
        return None

    # --------------------------------------------------------
    # 1. Local project-aware resolution
    # --------------------------------------------------------

    for candidate in _py_local_candidates(
        raw,
        from_rel,
        files,
    ):

        return candidate

    # --------------------------------------------------------
    # 2. Exact / shorter dotted resolution
    # --------------------------------------------------------

    parts = raw.split(
        "."
    )

    candidates = [
        raw
    ]

    candidates += [
        ".".join(
            parts[:i]
        )
        for i in range(
            len(parts),
            0,
            -1,
        )
    ]

    seen = set()

    for candidate in candidates:

        if candidate in seen:
            continue

        seen.add(
            candidate
        )

        if candidate in dotted_index:

            return dotted_index[
                candidate
            ]

    # --------------------------------------------------------
    # 3. Unique basename fallback
    # --------------------------------------------------------

    base = parts[-1]

    matches = basename_index.get(
        base,
        [],
    )

    if len(matches) == 1:

        return matches[0]

    return None


# ============================================================
# General import resolution
# ============================================================

def _resolve_import(
    raw,
    from_rel,
    dotted_index,
    basename_index,
    files,
    root,
):
    """
    Resolve Python and JavaScript/TypeScript imports.
    """

    if not raw:
        return None

    ext = files[
        from_rel
    ]["ext"]

    # --------------------------------------------------------
    # Python
    # --------------------------------------------------------

    if ext == ".py":

        return _resolve_python_import(
            raw,
            from_rel,
            dotted_index,
            basename_index,
            files,
        )

    # --------------------------------------------------------
    # JavaScript / TypeScript
    # --------------------------------------------------------

    if raw.startswith("."):

        base_dir = os.path.dirname(
            from_rel
        )

        target = os.path.normpath(
            os.path.join(
                base_dir,
                raw,
            )
        ).replace(
            os.sep,
            "/",
        )

        for ext_try in (
            "",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            "/index.js",
            "/index.jsx",
            "/index.ts",
            "/index.tsx",
        ):

            candidate = (
                target
                + ext_try
            )

            if candidate in files:

                return candidate

    return None


# ============================================================
# External library detection
# ============================================================

def ext_lib_guess(raw):
    return (
        bool(
            re.match(
                r"^[a-zA-Z0-9_\-@][a-zA-Z0-9_\-./]*$",
                raw,
            )
        )
        and not raw.startswith(".")
    )


# ============================================================
# Repository analysis
# ============================================================

def analyze_repo(root):

    files = {}

    dotted_index = {}

    basename_index = defaultdict(
        list
    )

    external_libs = set()

    # --------------------------------------------------------
    # Read source files
    # --------------------------------------------------------

    for full, rel in _iter_source_files(
        root
    ):

        ext = os.path.splitext(
            rel
        )[1]

        try:

            with open(
                full,
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as f:

                source = f.read()

        except OSError:

            continue

        base = os.path.splitext(
            os.path.basename(rel)
        )[0]

        group = os.path.dirname(
            rel
        ) or ""

        funcs = []
        classes = []

        if ext == ".py":

            funcs, classes = _py_symbols(
                source
            )

        files[rel] = {

            "path": rel,

            "name": os.path.basename(
                rel
            ),

            "ext": ext,

            "group": group,

            "lang": (
                "python"
                if ext == ".py"
                else "javascript"
            ),

            "purpose": (
                _first_docstring_or_comment(
                    source,
                    ext,
                )
                or ""
            ),

            "loc": len(
                source.splitlines()
            ),

            "functions": funcs,

            "classes": classes,

            "raw_imports": (
                _py_imports(source)
                if ext == ".py"
                else _js_imports(source)
            ),

            "source_preview": "\n".join(
                source.splitlines()[:60]
            ),
        }

        # ----------------------------------------------------
        # Build module indexes
        # ----------------------------------------------------

        if base != "__init__":

            basename_index[
                base
            ].append(
                rel
            )

            dotted_index[
                _dotted(rel)
            ] = rel

        else:

            # app/__init__.py
            #
            # is imported as:
            #
            # app

            package_name = (
                os.path.dirname(
                    rel
                ).replace(
                    "/",
                    ".",
                )
            )

            if package_name:

                dotted_index[
                    package_name
                ] = rel

    # --------------------------------------------------------
    # Resolve dependency edges
    # --------------------------------------------------------

    edges = set()

    for rel, meta in files.items():

        for raw in meta[
            "raw_imports"
        ]:

            target = _resolve_import(
                raw,
                rel,
                dotted_index,
                basename_index,
                files,
                root,
            )

            if (
                target
                and target != rel
            ):

                edges.add(
                    (
                        rel,
                        target,
                    )
                )

            elif raw:

                top = (
                    raw
                    .split(".")[0]
                    .split("/")[0]
                    .lstrip("@")
                    .lower()
                )

                if top in KNOWN_LIBS:

                    external_libs.add(
                        top
                    )

                elif (
                    not raw.startswith(
                        (
                            ".",
                            "/",
                        )
                    )
                    and ext_lib_guess(
                        raw
                    )
                ):

                    external_libs.add(
                        raw
                        .split("/")[0]
                        .lower()
                    )

    nodes = list(
        files.keys()
    )

    # --------------------------------------------------------
    # Entry point and architecture levels
    # --------------------------------------------------------

    entry = _pick_entry(
        files
    )

    levels = _layered_levels(
        nodes,
        edges,
        entry,
    )

    # --------------------------------------------------------
    # Degree information
    # --------------------------------------------------------

    indeg = defaultdict(
        int
    )

    outdeg = defaultdict(
        int
    )

    for source, target in edges:

        outdeg[
            source
        ] += 1

        indeg[
            target
        ] += 1

    degree = {
        n:
        indeg[n]
        + outdeg[n]
        for n in nodes
    }

    # --------------------------------------------------------
    # Groups
    # --------------------------------------------------------

    groups = sorted(
        {
            metadata["group"]
            for metadata
            in files.values()
            if metadata["group"]
        }
    )

    # --------------------------------------------------------
    # Insights
    # --------------------------------------------------------

    insights = _build_insights(
        files,
        edges,
        groups,
        degree,
    )

    # --------------------------------------------------------
    # Technology stack
    # --------------------------------------------------------

    stack = _detect_stack(
        files,
        external_libs,
    )

    # --------------------------------------------------------
    # Mermaid
    # --------------------------------------------------------

    mermaid = _to_mermaid(
        files,
        edges,
        groups,
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    stats = {

        "files": len(
            nodes
        ),

        "modules": len(
            [
                n
                for n in nodes
                if files[n]["name"]
                != "__init__.py"
            ]
        ),

        "dependencies": (
            len(edges)
            + len(external_libs)
        ),

        "tools": sum(
            1
            for n in nodes
            if "tool"
            in files[n]["group"].lower()
        ),
    }

    # --------------------------------------------------------
    # Graph nodes
    # --------------------------------------------------------

    graph_nodes = []

    for n in nodes:

        metadata = files[n]

        graph_nodes.append({

            "id": n,

            "name": metadata[
                "name"
            ],

            "group": metadata[
                "group"
            ],

            "level": levels.get(
                n,
                max(
                    levels.values(),
                    default=0,
                ) + 1,
            ),

            "purpose": metadata[
                "purpose"
            ],

            "loc": metadata[
                "loc"
            ],

            "functions": metadata[
                "functions"
            ],

            "classes": metadata[
                "classes"
            ],

            "isEntry": (
                n == entry
            ),

            "degree": degree.get(
                n,
                0,
            ),

            "sourcePreview": metadata[
                "source_preview"
            ],

            "lang": metadata[
                "lang"
            ],
        })

    # --------------------------------------------------------
    # Graph edges
    # --------------------------------------------------------

    graph_edges = [

        {
            "source": source,
            "target": target,
        }

        for source, target
        in sorted(edges)

    ]

    return {

        "stats": stats,

        "nodes": graph_nodes,

        "edges": graph_edges,

        "groups": groups,

        "insights": insights,

        "stack": stack,

        "mermaid": mermaid,

        "entry": entry,

        "language": (
            "Python"
            if any(
                f["ext"] == ".py"
                for f in files.values()
            )
            else "JavaScript"
        ),
    }


# ============================================================
# Entry point selection
# ============================================================

def _pick_entry(files):

    # Prefer known entry-point filenames.

    for rel, metadata in files.items():

        if metadata[
            "name"
        ] in ENTRY_NAMES:

            return rel

    # Then look for __main__.

    for rel, metadata in files.items():

        if "__main__" in metadata[
            "source_preview"
        ]:

            return rel

    # Finally use the first file.

    return next(
        iter(files),
        None,
    )


# ============================================================
# Layer calculation
# ============================================================

def _layered_levels(
    nodes,
    edges,
    entry,
):
    """
    Assign architecture levels using BFS.

    Level 0:
        Entry point

    Level 1:
        Direct dependencies

    Level 2:
        Dependencies of level 1

    etc.
    """

    adjacency = defaultdict(
        list
    )

    for source, target in edges:

        adjacency[
            source
        ].append(
            target
        )

    levels = {}

    if entry:

        levels[
            entry
        ] = 0

        queue = deque(
            [entry]
        )

        while queue:

            current = queue.popleft()

            for nxt in adjacency[
                current
            ]:

                if nxt not in levels:

                    levels[
                        nxt
                    ] = (
                        levels[current]
                        + 1
                    )

                    queue.append(
                        nxt
                    )

    next_level = (
        max(
            levels.values()
        ) + 1
        if levels
        else 0
    )

    for node in nodes:

        if node not in levels:

            levels[
                node
            ] = next_level

    return levels


# ============================================================
# Architecture insights
# ============================================================

def _build_insights(
    files,
    edges,
    groups,
    degree,
):
    insights = []

    file_count = len(
        files
    )

    # --------------------------------------------------------
    # Modularity
    # --------------------------------------------------------

    if (
        len(groups) >= 1
        and file_count >= 5
    ):

        insights.append({
            "type": "ok",
            "text": (
                "Well organized modular structure"
            ),
        })

    else:

        insights.append({
            "type": "warn",
            "text": (
                "Most code lives in a single flat "
                "directory. Consider splitting into modules"
            ),
        })

    # --------------------------------------------------------
    # Average connectivity
    # --------------------------------------------------------

    average_degree = (
        sum(
            degree.values()
        )
        / len(degree)
        if degree
        else 0
    )

    if average_degree <= 3:

        insights.append({
            "type": "ok",
            "text": (
                "Clear separation of concerns"
            ),
        })

    # --------------------------------------------------------
    # Highly connected modules
    # --------------------------------------------------------

    highly_connected = [
        node
        for node, value
        in degree.items()
        if value >= 4
    ]

    if highly_connected:

        names = ", ".join(
            files[node]["name"]
            for node
            in highly_connected[:3]
        )

        insights.append({
            "type": "warn",
            "text": (
                f"{len(highly_connected)} "
                f"highly connected module(s) "
                f"({names}). Consider refactoring"
            ),
        })

    # --------------------------------------------------------
    # Tool system
    # --------------------------------------------------------

    tool_dirs = [

        group

        for group
        in groups

        if any(
            keyword
            in group.lower()

            for keyword
            in (
                "tool",
                "plugin",
                "util",
            )
        )
    ]

    if tool_dirs:

        insights.append({
            "type": "info",
            "text": (
                "Tool system is extensible and well designed"
            ),
        })

    # --------------------------------------------------------
    # General result
    # --------------------------------------------------------

    if not any(
        insight["type"] == "warn"
        for insight in insights
    ):

        insights.append({
            "type": "info",
            "text": (
                "No major architectural red flags detected"
            ),
        })

    return insights


# ============================================================
# Technology stack
# ============================================================

def _detect_stack(
    files,
    external_libs,
):
    found = set()

    for metadata in files.values():

        for raw in metadata[
            "raw_imports"
        ]:

            top = (
                raw
                .split(".")[0]
                .split("/")[0]
                .lstrip("@")
                .lower()
            )

            if top in KNOWN_LIBS:

                found.add(
                    KNOWN_LIBS[top]
                )

    for lib in external_libs:

        if lib in KNOWN_LIBS:

            found.add(
                KNOWN_LIBS[lib]
            )

    languages = {
        metadata["lang"]
        for metadata
        in files.values()
    }

    if "python" in languages:

        found.add(
            "Python"
        )

    if "javascript" in languages:

        found.add(
            "JavaScript"
        )

    return sorted(
        found
    )


# ============================================================
# Mermaid generation
# ============================================================

def _to_mermaid(
    files,
    edges,
    groups,
):

    def node_id(rel):

        return (
            "n"
            + re.sub(
                r"[^a-zA-Z0-9]",
                "_",
                rel,
            )
        )

    lines = [
        "flowchart TD"
    ]

    # --------------------------------------------------------
    # Root files
    # --------------------------------------------------------

    root_files = [

        rel

        for rel, metadata
        in files.items()

        if not metadata["group"]
    ]

    for rel in root_files:

        lines.append(
            f'    {node_id(rel)}'
            f'["{files[rel]["name"]}"]'
        )

    # --------------------------------------------------------
    # Folder groups
    # --------------------------------------------------------

    for group in groups:

        safe_group = re.sub(
            r"[^a-zA-Z0-9]",
            "_",
            group,
        )

        lines.append(
            f'    subgraph '
            f'{safe_group} '
            f'["{group}/"]'
        )

        for rel, metadata in files.items():

            if metadata[
                "group"
            ] == group:

                lines.append(
                    f'        '
                    f'{node_id(rel)}'
                    f'["{metadata["name"]}"]'
                )

        lines.append(
            "    end"
        )

    # --------------------------------------------------------
    # Edges
    # --------------------------------------------------------

    for source, target in sorted(
        edges
    ):

        lines.append(
            f"    "
            f"{node_id(source)}"
            f" --> "
            f"{node_id(target)}"
        )

    return "\n".join(
        lines
    )