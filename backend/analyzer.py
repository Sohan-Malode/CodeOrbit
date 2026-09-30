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
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
}

CODE_EXT = PY_EXT | JS_EXT

# Import specifier extension -> extensions it may refer to on disk.
TS_EXT_SWAPS = {
    ".js": ((".js", ".ts"), (".js", ".tsx"), (".js", ".jsx")),
    ".jsx": ((".jsx", ".tsx"),),
    ".mjs": ((".mjs", ".mts"),),
    ".cjs": ((".cjs", ".cts"),),
}

# Tool/build configuration is loaded by tooling, never imported by the app,
# so it must not be reported as unused or dead.
CONFIG_FILE_RE = re.compile(
    r"(^|/)("
    r"[\w.-]*\.config\.(js|cjs|mjs|ts|cts|mts)"
    r"|\.?[\w-]*rc\.(js|cjs|mjs|ts)"
    r"|setupTests?\.(js|ts)"
    r"|jest\.setup\.(js|ts)"
    r"|conftest\.py"
    r"|setup\.py"
    r"|manage\.py"
    r"|wsgi\.py|asgi\.py"
    r")$"
)

# Files in these directories are run directly (CI, build steps, CLIs), so
# "nothing imports it" does not mean "dead".
TOOLING_DIR_NAMES = {"scripts", "script", "bin", "tools", "migrations"}

ENTRY_NAMES = {
    "main.py",
    "app.py",
    "index.js",
    "server.js",
    "index.ts",
    "app.js",
    # Vite / CRA / Next-style frontend bootstrap files
    "main.ts",
    "main.tsx",
    "main.js",
    "main.jsx",
    "index.tsx",
    "index.jsx",
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


def _py_submodule_imports(source):
    """
    Return dotted candidates for names imported with ``from pkg import name``.

    ``from shop.core import billing`` depends on ``shop/core/billing.py`` when
    ``billing`` is a submodule, not just on ``shop/core/__init__.py``. Missing
    this hides real circular dependencies. Candidates are resolved
    conservatively (exact paths only), so ``from app import app`` never
    resolves to an unrelated ``app.py`` elsewhere in the repository.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        for alias in node.names:
            if alias.name == "*":
                continue

            found.append(
                f"{node.module}.{alias.name}"
                if node.module
                else alias.name
            )

    return list(dict.fromkeys(found))


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

    # Re-exports (barrel files):
    #
    # export { x } from "./x.js"
    # export * from "./y.js"
    # export type { T } from "./types.js"

    found += re.findall(
        r"""\bexport\s+(?:type\s+)?(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s*from\s*['"]([^'"]+)['"]""",
        source,
    )

    # Dynamic imports:
    #
    # await import("./plugin.js")

    found += re.findall(
        r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""",
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
    workspace_packages=None,
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

    # Workspace package imports such as @vitest/utils are internal project
    # dependencies in a monorepo, not external libraries. Resolve them to the
    # package source entry before treating them as third-party imports.
    if workspace_packages:
        package_match = None
        for package_name in sorted(workspace_packages, key=len, reverse=True):
            if raw == package_name or raw.startswith(package_name + "/"):
                package_match = package_name
                break

        if package_match:
            package_entry = workspace_packages[package_match]

            if raw == package_match:
                return package_entry

            subpath = raw[len(package_match):].lstrip("/")
            package_dir = os.path.dirname(package_entry)
            # Walk upward until the package name's directory is reached.
            package_root = package_dir
            while package_root and package_root.count("/") > 0 and os.path.basename(package_root) not in {"src", "lib", "dist"}:
                package_root = os.path.dirname(package_root)
            if os.path.basename(package_dir) in {"src", "lib", "dist"}:
                package_root = os.path.dirname(package_dir)
            else:
                package_root = package_dir

            target_base = os.path.normpath(os.path.join(package_root, subpath)).replace(os.sep, "/")
            for candidate in (
                target_base,
                target_base + ".ts",
                target_base + ".tsx",
                target_base + ".js",
                target_base + ".jsx",
                target_base + "/index.ts",
                target_base + "/index.tsx",
                target_base + "/index.js",
                target_base + "/index.jsx",
            ):
                if candidate in files:
                    return candidate

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

        # TypeScript ESM projects import "./x.js" while the file on disk is
        # "x.ts" / "x.tsx" (and "./x.mjs" -> "x.mts", "./x.cjs" -> "x.cts").
        # Try the literal path first, then the extension-swapped variants.
        bases = [target]

        stem, literal_ext = os.path.splitext(target)

        for written, on_disk in TS_EXT_SWAPS.get(literal_ext, ()):
            bases.append(stem + on_disk)

        suffixes = (
            "",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            ".mts",
            ".cts",
            "/index.js",
            "/index.jsx",
            "/index.ts",
            "/index.tsx",
        )

        for base in bases:

            for ext_try in suffixes:

                candidate = base + ext_try

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
# Architecture issue detection
# ============================================================

def _is_test_file(rel):
    """Return True for files that are normally test-only entry points."""
    normalized = rel.lower().replace("\\", "/")
    parts = normalized.split("/")
    name = parts[-1]
    if any(
        part in {"tests", "test", "__tests__", "__mocks__", "fixtures", "__fixtures__", "e2e", "cypress"}
        for part in parts[:-1]
    ):
        return True

    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or bool(
            re.search(
                r"\.(test|spec|e2e)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)$",
                name,
            )
        )
    )


def _find_strongly_connected_components(nodes, edges):
    """Find strongly connected components using Tarjan's algorithm."""
    adjacency = defaultdict(list)

    for source, target in edges:
        adjacency[source].append(target)

    index = 0
    stack = []
    on_stack = set()
    indices = {}
    lowlink = {}
    components = []

    def visit(node):
        nonlocal index

        indices[node] = index
        lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in adjacency[node]:
            if target not in indices:
                visit(target)
                lowlink[node] = min(
                    lowlink[node],
                    lowlink[target],
                )
            elif target in on_stack:
                lowlink[node] = min(
                    lowlink[node],
                    indices[target],
                )

        if lowlink[node] == indices[node]:
            component = []

            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)

                if member == node:
                    break

            components.append(sorted(component))

    for node in nodes:
        if node not in indices:
            visit(node)

    return components


def _detect_architecture_issues(nodes, edges, entries):
    """
    Detect architecture problems that matter during legacy onboarding.

    A circular dependency is a strongly connected component containing
    multiple modules, or a self-referencing module.

    A potentially unused module has no incoming project dependency and is
    not the detected entry point or an obvious test module.

    A potentially dead module is not reachable from the detected entry point.
    Test modules are excluded because test suites intentionally sit outside
    the runtime dependency graph.
    """
    adjacency = defaultdict(list)
    indegree = defaultdict(int)

    for source, target in edges:
        adjacency[source].append(target)
        indegree[target] += 1

    components = _find_strongly_connected_components(
        nodes,
        edges,
    )

    circular_components = []
    circular_nodes = set()

    edge_set = set(edges)

    for component in components:
        is_self_cycle = (
            len(component) == 1
            and (component[0], component[0]) in edge_set
        )

        if len(component) > 1 or is_self_cycle:
            circular_components.append(component)
            circular_nodes.update(component)

    if isinstance(entries, str):
        entries = [entries] if entries else []

    entry_set = set(entries or [])
    reachable = set(entry_set)
    queue = deque(entry_set)

    while queue:
        current = queue.popleft()

        for target in adjacency[current]:
            if target not in reachable:
                reachable.add(target)
                queue.append(target)

    def eligible(rel):
        return (
            rel not in entry_set
            and os.path.basename(rel) != "__init__.py"
            and not _is_test_file(rel)
            and not CONFIG_FILE_RE.search(rel)
            and not (set(rel.split("/")[:-1]) & TOOLING_DIR_NAMES)
        )

    unused_nodes = sorted(
        node
        for node in nodes
        if eligible(node)
        and indegree[node] == 0
    )

    dead_nodes = sorted(
        node
        for node in nodes
        if eligible(node)
        and node not in reachable
    )

    circular_components.sort(
        key=lambda component: component[0]
        if component
        else ""
    )

    return {
        "circularComponents": circular_components,
        "circularNodes": sorted(circular_nodes),
        "unusedNodes": unused_nodes,
        "deadNodes": dead_nodes,
        "reachableNodes": sorted(reachable),
    }


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

            "submodule_imports": (
                _py_submodule_imports(source)
                if ext == ".py"
                else []
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

    workspace_packages = _workspace_package_index(root, files)

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
                workspace_packages,
            )

            if target:

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

    # from pkg import submodule  ->  edge to pkg/submodule.py (exact match only)
    for rel, meta in files.items():

        for dotted in meta.get("submodule_imports", []):

            target = None

            for candidate in _py_local_candidates(
                dotted,
                rel,
                files,
            ):

                if not candidate.endswith("/__init__.py"):
                    target = candidate

                break

            if target is None:
                indexed = dotted_index.get(dotted)

                if indexed and not indexed.endswith("/__init__.py"):
                    target = indexed

            if target and target != rel:
                edges.add((rel, target))

    nodes = list(
        files.keys()
    )

    # --------------------------------------------------------
    # Entry point and architecture levels
    # --------------------------------------------------------

    entry_points = _pick_entries(
        root,
        files,
    )

    entry = entry_points[0] if entry_points else None

    levels = _layered_levels(
        nodes,
        edges,
        entry_points,
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
    # Architecture issue detection
    # --------------------------------------------------------

    architecture_issues = _detect_architecture_issues(
        nodes,
        edges,
        entry_points,
    )

    circular_nodes = set(
        architecture_issues["circularNodes"]
    )

    unused_nodes = set(
        architecture_issues["unusedNodes"]
    )

    dead_nodes = set(
        architecture_issues["deadNodes"]
    )

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
        architecture_issues,
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
        architecture_issues,
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

        "circular": len(
            architecture_issues["circularComponents"]
        ),

        "unused": len(
            architecture_issues["unusedNodes"]
        ),

        "dead": len(
            architecture_issues["deadNodes"]
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

            "isEntry": n in set(entry_points),

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

            "isCircular": n in circular_nodes,

            "isUnused": n in unused_nodes,

            "isDead": n in dead_nodes,

            "issueLabels": [
                label
                for label, active
                in (
                    ("circular", n in circular_nodes),
                    ("unused", n in unused_nodes),
                    ("dead", n in dead_nodes),
                )
                if active
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

        "issues": architecture_issues,

        "stack": stack,

        "mermaid": mermaid,

        "entry": entry,

        "entryPoints": entry_points,

        "isMonorepo": len(entry_points) > 1,

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

def _manifest_entry_targets(root, files):
    """Return source modules exposed by package manifests.

    Monorepos often have many legitimate entry points. A single global
    entry point makes every unrelated package look dead. We inspect
    package.json files and map common Node package fields back to source
    modules when possible.
    """
    entries = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.startswith(".")
        ]

        if "package.json" not in filenames:
            continue

        manifest_path = os.path.join(dirpath, "package.json")

        try:
            import json
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue

        package_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        package_dir = "" if package_dir == "." else package_dir

        candidates = []

        for key in ("main", "module", "browser", "types", "typings"):
            value = manifest.get(key)
            if isinstance(value, str):
                candidates.append(value)

        exports = manifest.get("exports")
        if isinstance(exports, str):
            candidates.append(exports)
        elif isinstance(exports, dict):
            def collect_export(value):
                if isinstance(value, str):
                    candidates.append(value)
                elif isinstance(value, dict):
                    for nested in value.values():
                        collect_export(nested)
                elif isinstance(value, list):
                    for nested in value:
                        collect_export(nested)
            collect_export(exports)

        bin_field = manifest.get("bin")
        if isinstance(bin_field, str):
            candidates.append(bin_field)
        elif isinstance(bin_field, dict):
            candidates.extend(v for v in bin_field.values() if isinstance(v, str))

        # Files run by package.json scripts, e.g. "node scripts/build.mjs"
        # or "tsx tools/seed.ts", are entry points even though nothing
        # imports them.
        script_map = manifest.get("scripts")

        if isinstance(script_map, dict):
            for command in script_map.values():
                if not isinstance(command, str):
                    continue

                for match in re.findall(
                    r"(?:^|[\s=\"'])(\.{0,2}/?[\w@./-]+\.(?:mjs|cjs|js|mts|cts|ts|py))\b",
                    command,
                ):
                    script_path = os.path.normpath(
                        os.path.join(package_dir, match)
                    ).replace(os.sep, "/")

                    if script_path in files:
                        entries.add(script_path)

        for candidate in candidates:
            if not candidate or candidate.startswith(".") is False and not candidate.startswith("/"):
                # Package fields such as "./dist/index.js" are the useful
                # local form. Bare values are not source paths.
                if not candidate.startswith("."):
                    continue

            candidate = candidate.split("#", 1)[0].split("?", 1)[0]
            candidate = candidate.lstrip("./")
            candidate_path = os.path.normpath(
                os.path.join(package_dir, candidate)
            ).replace(os.sep, "/")

            possibilities = [candidate_path]
            base, ext = os.path.splitext(candidate_path)
            if ext in {".js", ".jsx", ".ts", ".tsx", ".py"}:
                possibilities.append(base + ".js")
                possibilities.append(base + ".jsx")
                possibilities.append(base + ".ts")
                possibilities.append(base + ".tsx")
            else:
                for suffix in (".js", ".jsx", ".ts", ".tsx", ".py"):
                    possibilities.append(candidate_path + suffix)
                for suffix in ("/index.js", "/index.jsx", "/index.ts", "/index.tsx", "/__init__.py"):
                    possibilities.append(candidate_path + suffix)

            if "/dist/" in candidate_path:
                source_candidate = candidate_path.replace("/dist/", "/src/", 1)
                possibilities.extend([
                    source_candidate,
                    source_candidate.rsplit(".", 1)[0] + ".ts",
                    source_candidate.rsplit(".", 1)[0] + ".tsx",
                    source_candidate.rsplit(".", 1)[0] + ".js",
                    source_candidate.rsplit(".", 1)[0] + ".jsx",
                ])

            for possibility in possibilities:
                if possibility in files:
                    entries.add(possibility)
                    break

    return entries


def _workspace_package_index(root, files):
    """Map workspace package names to their source entry modules."""
    index = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.startswith(".")
        ]

        if "package.json" not in filenames:
            continue

        manifest_path = os.path.join(dirpath, "package.json")
        try:
            import json
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue

        package_name = manifest.get("name")
        if not isinstance(package_name, str) or not package_name:
            continue

        package_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        package_dir = "" if package_dir == "." else package_dir

        candidates = []
        for key in ("source", "main", "module", "browser", "types", "typings"):
            value = manifest.get(key)
            if isinstance(value, str):
                candidates.append(value)

        exports = manifest.get("exports")
        if isinstance(exports, str):
            candidates.append(exports)
        elif isinstance(exports, dict):
            def collect(value):
                if isinstance(value, str):
                    candidates.append(value)
                elif isinstance(value, dict):
                    for nested in value.values():
                        collect(nested)
                elif isinstance(value, list):
                    for nested in value:
                        collect(nested)
            collect(exports)

        # Prefer source-oriented paths first.
        candidates.extend([
            "./src/index.ts",
            "./src/index.tsx",
            "./src/index.js",
            "./src/index.jsx",
            "./index.ts",
            "./index.js",
        ])

        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.startswith("."):
                continue

            clean = candidate.split("#", 1)[0].split("?", 1)[0].lstrip("./")
            candidate_path = os.path.normpath(
                os.path.join(package_dir, clean)
            ).replace(os.sep, "/")

            possibilities = [candidate_path]
            base, ext = os.path.splitext(candidate_path)
            if ext in {".js", ".jsx", ".ts", ".tsx", ".d.ts"}:
                possibilities.extend([
                    base + ".ts", base + ".tsx", base + ".js", base + ".jsx"
                ])
            else:
                possibilities.extend([
                    candidate_path + suffix
                    for suffix in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")
                ])

            # Common build output convention: package.json points at dist,
            # while the repository source lives under src.
            if "/dist/" in candidate_path:
                possibilities.extend([
                    candidate_path.replace("/dist/", "/src/", 1),
                    candidate_path.replace("/dist/", "/src/", 1).rsplit(".", 1)[0] + ".ts",
                    candidate_path.replace("/dist/", "/src/", 1).rsplit(".", 1)[0] + ".tsx",
                ])

            for possibility in possibilities:
                if possibility in files:
                    index[package_name] = possibility
                    break

            if package_name in index:
                break

    return index


def _html_script_targets(root, files):
    """Return project scripts loaded by <script src="..."> in HTML pages.

    A plain frontend (index.html + script.js) has no import statements, so the
    script would look unused and dead even though the page loads it.
    """
    entries = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            if not filename.lower().endswith((".html", ".htm")):
                continue

            try:
                with open(
                    os.path.join(dirpath, filename),
                    "r",
                    encoding="utf-8",
                    errors="ignore",
                ) as handle:
                    html = handle.read()
            except OSError:
                continue

            page_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            page_dir = "" if page_dir == "." else page_dir

            for src in re.findall(
                r"""<script\b[^>]*?\bsrc\s*=\s*['"]([^'"]+)['"]""",
                html,
                flags=re.IGNORECASE,
            ):
                src = src.split("?", 1)[0].split("#", 1)[0]

                if not src or src.startswith(("http://", "https://", "//", "data:")):
                    continue

                base = "" if src.startswith("/") else page_dir
                target = os.path.normpath(
                    os.path.join(base, src.lstrip("/"))
                ).replace(os.sep, "/")

                candidates = [target]
                stem, ext = os.path.splitext(target)

                for _, on_disk in TS_EXT_SWAPS.get(ext, ()):
                    candidates.append(stem + on_disk)

                for candidate in candidates:
                    if candidate in files:
                        entries.add(candidate)
                        break

    return entries


def _pick_entries(root, files):
    """Choose one or many legitimate entry points.

    For a monorepo, package manifests and package-local conventional entry
    names are used together. For a normal single project the historical
    single-entry behavior is retained.
    """
    manifest_entries = _manifest_entry_targets(root, files)
    html_entries = _html_script_targets(root, files)

    package_dirs = set()
    for rel in files:
        parts = rel.split("/")
        if len(parts) > 1:
            package_dirs.add(parts[0])
            if parts[0] == "packages" and len(parts) > 2:
                package_dirs.add("/".join(parts[:2]))

    has_monorepo_layout = (
        os.path.isdir(os.path.join(root, "packages"))
        or len(package_dirs) >= 3
        or len(manifest_entries) > 1
    )

    entries = set(manifest_entries)

    def has_package_manifest(rel):
        current = os.path.dirname(rel)
        while True:
            manifest = os.path.join(root, current, "package.json")
            if os.path.isfile(manifest):
                return True
            if not current:
                return False
            parent = os.path.dirname(current)
            if parent == current:
                return False
            current = parent

    for rel, metadata in files.items():
        if metadata["name"] not in ENTRY_NAMES:
            continue

        if has_monorepo_layout:
            if has_package_manifest(rel):
                entries.add(rel)
        elif not entries:
            entries.add(rel)

    # A test module with an `if __name__ == "__main__"` guard is not a runtime
    # entry point; treating it as one would mark code that only tests import
    # as reachable and hide dead modules.
    for rel, metadata in files.items():
        if "__main__" in metadata["source_preview"] and not _is_test_file(rel):
            entries.add(rel)

    # Pages load their scripts directly. Added after the conventional-name
    # logic above so a frontend bundle never suppresses main.py / app.py.
    entries |= html_entries

    # Test files and fixtures are never legitimate runtime entry points, even
    # when they are named index.ts or sit next to a package.json.
    non_test_entries = {e for e in entries if not _is_test_file(e)}

    if non_test_entries:
        return sorted(non_test_entries)

    if entries:
        return sorted(entries)

    first = next(iter(files), None)
    return [first] if first else []


def _pick_entry(root, files):
    """Backward-compatible primary entry used by the existing UI/API."""
    entries = _pick_entries(root, files)
    return entries[0] if entries else None


# ============================================================
# Layer calculation
# ============================================================

def _layered_levels(
    nodes,
    edges,
    entries,
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

    if isinstance(entries, str):
        entries = [entries] if entries else []

    entries = [entry for entry in (entries or []) if entry]

    for entry in entries:
        levels[entry] = 0

    if entries:
        queue = deque(entries)

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
    architecture_issues=None,
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
    # Legacy architecture issue detection
    # --------------------------------------------------------

    architecture_issues = architecture_issues or {}

    circular_components = architecture_issues.get(
        "circularComponents",
        [],
    )

    unused_nodes = architecture_issues.get(
        "unusedNodes",
        [],
    )

    dead_nodes = architecture_issues.get(
        "deadNodes",
        [],
    )

    if circular_components:
        examples = []

        for component in circular_components[:2]:
            examples.append(
                " → ".join(
                    os.path.basename(node)
                    for node in component
                )
            )

        insights.append({
            "type": "warn",
            "text": (
                f"{len(circular_components)} circular dependency "
                f"group(s) detected. "
                f"Example: {'; '.join(examples)}"
            ),
        })

    if unused_nodes:
        names = ", ".join(
            os.path.basename(node)
            for node in unused_nodes[:3]
        )

        insights.append({
            "type": "warn",
            "text": (
                f"{len(unused_nodes)} potentially unused module(s) "
                f"have no incoming project dependency ({names})."
            ),
        })

    if dead_nodes:
        names = ", ".join(
            os.path.basename(node)
            for node in dead_nodes[:3]
        )

        insights.append({
            "type": "warn",
            "text": (
                f"{len(dead_nodes)} potentially dead module(s) "
                f"are unreachable from the detected entry point ({names})."
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
    architecture_issues=None,
):

    architecture_issues = architecture_issues or {}

    circular_components = architecture_issues.get(
        "circularComponents",
        [],
    )
    circular_nodes = set(
        architecture_issues.get("circularNodes", [])
    )
    unused_nodes = set(
        architecture_issues.get("unusedNodes", [])
    )
    dead_nodes = set(
        architecture_issues.get("deadNodes", [])
    )

    # Sanitizing paths is lossy ("my-app/x.py" and "my_app/x.py" both become
    # "my_app_x_py"), so ids are de-duplicated to keep every module a
    # distinct Mermaid node.
    node_ids = {}
    used_ids = set()

    def node_id(rel):
        if rel in node_ids:
            return node_ids[rel]

        base = "n" + re.sub(r"[^a-zA-Z0-9]", "_", rel)
        candidate = base
        counter = 2

        while candidate in used_ids:
            candidate = f"{base}_{counter}"
            counter += 1

        used_ids.add(candidate)
        node_ids[rel] = candidate

        return candidate

    def esc(text):
        return (
            str(text)
            .replace("&", "#amp;")
            .replace('"', "#quot;")
            .replace("<", "#lt;")
            .replace(">", "#gt;")
        )

    circular_edges = set()

    for component in circular_components:
        members = set(component)

        for source, target in edges:
            if source in members and target in members:
                circular_edges.add((source, target))

    def issue_class(rel):
        active = tuple(
            key
            for key, present in (
                ("circular", rel in circular_nodes),
                ("unused", rel in unused_nodes),
                ("dead", rel in dead_nodes),
            )
            if present
        )

        return {
            ("circular",): "circular",
            ("unused",): "unused",
            ("dead",): "dead",
            ("circular", "unused"): "circularUnused",
            ("circular", "dead"): "circularDead",
            ("unused", "dead"): "unusedDead",
            ("circular", "unused", "dead"): "criticalIssue",
        }.get(active)

    def node_label(rel):
        name = files[rel]["name"]
        labels = []

        if rel in circular_nodes:
            labels.append("CYCLE")
        if rel in unused_nodes:
            labels.append("UNUSED")
        if rel in dead_nodes:
            labels.append("DEAD")

        if labels:
            return esc(f'[{" / ".join(labels)}] {name}')

        return esc(name)

    lines = [
        "flowchart TD",
        "    %% CodeOrbit architecture issue markers",
    ]

    # --------------------------------------------------------
    # Root files
    # --------------------------------------------------------

    root_files = [
        rel
        for rel, metadata in files.items()
        if not metadata["group"]
    ]

    for rel in root_files:
        lines.append(
            f'    {node_id(rel)}'
            f'["{node_label(rel)}"]'
        )

    # --------------------------------------------------------
    # Folder tree (nested subgraphs mirror the real directory tree)
    # --------------------------------------------------------

    folder_tree = {}

    for group in groups:
        cursor = folder_tree

        for part in group.split("/"):
            cursor = cursor.setdefault(part, {})

    files_by_group = defaultdict(list)

    for rel, metadata in files.items():
        if metadata["group"]:
            files_by_group[metadata["group"]].append(rel)

    # "end", "graph", "subgraph" ... are reserved in Mermaid, so subgraph ids
    # always carry a prefix and a de-duplicating counter.
    used_group_ids = set()

    def group_id(path):
        base = "g_" + re.sub(r"[^a-zA-Z0-9]", "_", path)
        candidate = base
        counter = 2

        while candidate in used_group_ids:
            candidate = f"{base}_{counter}"
            counter += 1

        used_group_ids.add(candidate)

        return candidate

    def emit_folder(name, children, path, depth):
        indent = "    " * depth

        lines.append(
            f'{indent}subgraph {group_id(path)} '
            f'["{esc(name)}/"]'
        )

        for rel in files_by_group.get(path, []):
            lines.append(
                f'{indent}    {node_id(rel)}'
                f'["{node_label(rel)}"]'
            )

        for child in sorted(children):
            emit_folder(
                child,
                children[child],
                f"{path}/{child}",
                depth + 1,
            )

        lines.append(f"{indent}end")

    for top in sorted(folder_tree):
        emit_folder(top, folder_tree[top], top, 1)

    # --------------------------------------------------------
    # Edges
    # --------------------------------------------------------

    for source, target in sorted(edges):
        connector = "-.->" if (source, target) in circular_edges else "-->"

        lines.append(
            f"    {node_id(source)}"
            f" {connector} "
            f"{node_id(target)}"
        )

    # --------------------------------------------------------
    # Explicit architecture issue legend
    # --------------------------------------------------------

    if circular_nodes or unused_nodes or dead_nodes:
        lines.extend([
            "",
            '    subgraph codeorbit_legend ["CodeOrbit Architecture Issues"]',
            '        legend_cycle["CIRCULAR DEPENDENCY"]:::circular',
            '        legend_unused["POTENTIALLY UNUSED"]:::unused',
            '        legend_dead["POTENTIALLY DEAD"]:::dead',
            "    end",
        ])

    # Class definitions remain inside the generated Mermaid source so the
    # copied/exported diagram preserves the warning styling.
    lines.extend([
        "",
        "    classDef circular fill:#3b1f2b,stroke:#ef4444,stroke-width:3px,color:#fecaca;",
        "    classDef unused fill:#3a2b14,stroke:#f59e0b,stroke-width:3px,color:#fde68a;",
        "    classDef dead fill:#2b2738,stroke:#a78bfa,stroke-width:3px,color:#ddd6fe;",
        "    classDef circularUnused fill:#3a2430,stroke:#f97316,stroke-width:3px,color:#fed7aa;",
        "    classDef circularDead fill:#352335,stroke:#e879f9,stroke-width:3px,color:#f5d0fe;",
        "    classDef unusedDead fill:#332a19,stroke:#f59e0b,stroke-width:3px,color:#fde68a;",
        "    classDef criticalIssue fill:#3f1d2e,stroke:#ef4444,stroke-width:4px,color:#fee2e2;",
    ])

    for rel in sorted(files):
        class_name = issue_class(rel)

        if class_name:
            lines.append(
                f"    class {node_id(rel)} {class_name};"
            )

    return "\n".join(lines)
