"""
CodeOrbit analysis engine.

Walks a repository, builds a module dependency graph for Python (via `ast`)
and a best-effort graph for JS/TS (via regex on import/require), then
produces:
  - nodes / edges for the architecture graph
  - a layered layout (x/y per node)
  - Mermaid.js flowchart source
  - simple architecture insights
  - a detected technology stack

Import resolution is heuristic (name/path matching), not a full static
resolver -- good enough to visualize real architecture, not a type checker.
"""
import ast
import os
import re
from collections import defaultdict, deque

IGNORED_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", "egg-info",
    ".idea", ".vscode", "site-packages",
}

PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx"}
CODE_EXT = PY_EXT | JS_EXT

ENTRY_NAMES = {"main.py", "app.py", "index.js", "server.js", "index.ts", "app.js"}

# import-name -> (display name, stack tag)
KNOWN_LIBS = {
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI",
    "requests": "Requests", "numpy": "NumPy", "pandas": "Pandas",
    "torch": "PyTorch", "tensorflow": "TensorFlow", "sqlalchemy": "SQLAlchemy",
    "pil": "Pillow", "pillow": "Pillow", "asyncio": "AsyncIO", "json": "JSON",
    "ollama": "Ollama", "openai": "OpenAI", "anthropic": "Anthropic",
    "express": "Express", "react": "React", "axios": "Axios",
    "pytest": "Pytest", "click": "Click", "pydantic": "Pydantic",
}


def _iter_source_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext in CODE_EXT:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                yield full, rel


def _dotted(rel_path):
    """'generated_tools/calculator.py' -> 'generated_tools.calculator'"""
    no_ext = re.sub(r"\.(py|js|jsx|ts|tsx)$", "", rel_path)
    return no_ext.replace("/", ".")


def _py_imports(source):
    """Return list of raw imported-module strings referenced by a Python file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
                for alias in node.names:
                    found.append(f"{node.module}.{alias.name}")
            else:
                for alias in node.names:
                    found.append(alias.name)
    return found


def _js_imports(source):
    """Return JavaScript/TypeScript import and require targets."""
    found = []

    # CommonJS:
    # require("express")
    # require("./routes")
    found += re.findall(
        r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
        source,
    )

    # ES modules:
    # import express from "express"
    # import { router } from "./routes"
    # import * as utils from "./utils"
    # import "./config.js"
    found += re.findall(
        r"""import\s+(?:[\s\S]*?\s+from\s+)?['"]([^'"]+)['"]""",
        source,
    )

    # Remove duplicates while preserving discovery order.
    return list(dict.fromkeys(found))


def _first_docstring_or_comment(source, ext):
    if ext == ".py":
        try:
            tree = ast.parse(source)
            doc = ast.get_docstring(tree)
            if doc:
                return doc.strip().split("\n")[0]
        except SyntaxError:
            pass
    for line in source.splitlines()[:5]:
        line = line.strip()
        if line.startswith(("//", "#", "/**", "*")):
            return line.lstrip("/*# ").strip()
    return None


def _py_symbols(source):
    funcs, classes = [], []
    try:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                funcs.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
    except SyntaxError:
        pass
    return funcs, classes


def analyze_repo(root):
    files = {}       # rel_path -> metadata dict
    dotted_index = {} # dotted module path -> rel_path
    basename_index = defaultdict(list)  # 'calculator' -> [rel_paths]
    external_libs = set()

    for full, rel in _iter_source_files(root):
        ext = os.path.splitext(rel)[1]
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except OSError:
            continue

        base = os.path.splitext(os.path.basename(rel))[0]
        group = os.path.dirname(rel) or ""
        funcs, classes = ([], [])
        if ext == ".py":
            funcs, classes = _py_symbols(source)

        files[rel] = {
            "path": rel,
            "name": os.path.basename(rel),
            "ext": ext,
            "group": group,
            "lang": "python" if ext == ".py" else "javascript",
            "purpose": _first_docstring_or_comment(source, ext) or "",
            "loc": len(source.splitlines()),
            "functions": funcs,
            "classes": classes,
            "raw_imports": (_py_imports(source) if ext == ".py" else _js_imports(source)),
            "source_preview": "\n".join(source.splitlines()[:60]),
        }
        if base != "__init__":
            basename_index[base].append(rel)
        dotted_index[_dotted(rel)] = rel

    # --- resolve edges ---
    edges = set()
    for rel, meta in files.items():
        for raw in meta["raw_imports"]:
            target = _resolve_import(raw, rel, dotted_index, basename_index, files, root)
            if target and target != rel:
                edges.add((rel, target))
            elif raw:
                top = raw.split(".")[0].split("/")[0].lstrip("@").lower()
                if top in KNOWN_LIBS:
                    external_libs.add(top)
                elif not raw.startswith((".", "/")) and ext_lib_guess(raw):
                    external_libs.add(raw.split("/")[0].lower())

    nodes = list(files.keys())

    # --- entry point + layered layout (BFS levels) ---
    entry = _pick_entry(files)
    levels = _layered_levels(nodes, edges, entry)

    # --- degree / highly-connected detection ---
    indeg = defaultdict(int)
    outdeg = defaultdict(int)
    for a, b in edges:
        outdeg[a] += 1
        indeg[b] += 1
    degree = {n: indeg[n] + outdeg[n] for n in nodes}

    # --- groups (folders) ---
    groups = sorted({m["group"] for m in files.values() if m["group"]})

    insights = _build_insights(files, edges, groups, degree)
    stack = _detect_stack(files, external_libs)
    mermaid = _to_mermaid(files, edges, groups)

    stats = {
        "files": len(nodes),
        "modules": len([n for n in nodes if files[n]["name"] != "__init__.py"]),
        "dependencies": len(edges) + len(external_libs),
        "tools": sum(1 for n in nodes if "tool" in files[n]["group"].lower()),
    }

    graph_nodes = []
    for n in nodes:
        m = files[n]
        graph_nodes.append({
            "id": n,
            "name": m["name"],
            "group": m["group"],
            "level": levels.get(n, max(levels.values(), default=0) + 1),
            "purpose": m["purpose"],
            "loc": m["loc"],
            "functions": m["functions"],
            "classes": m["classes"],
            "isEntry": n == entry,
            "degree": degree.get(n, 0),
            "sourcePreview": m["source_preview"],
            "lang": m["lang"],
        })

    graph_edges = [{"source": a, "target": b} for a, b in sorted(edges)]

    return {
        "stats": stats,
        "nodes": graph_nodes,
        "edges": graph_edges,
        "groups": groups,
        "insights": insights,
        "stack": stack,
        "mermaid": mermaid,
        "entry": entry,
        "language": "Python" if any(f["ext"] == ".py" for f in files.values()) else "JavaScript",
    }


def ext_lib_guess(raw):
    return bool(re.match(r"^[a-zA-Z0-9_\-@][a-zA-Z0-9_\-./]*$", raw)) and not raw.startswith(".")


def _resolve_import(raw, from_rel, dotted_index, basename_index, files, root):
    if not raw:
        return None
    ext = files[from_rel]["ext"]
    if ext == ".py":
        candidates = [raw]
        parts = raw.split(".")
        candidates += [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
        for c in candidates:
            if c in dotted_index:
                return dotted_index[c]
        base = parts[-1]
        if base in basename_index and len(basename_index[base]) == 1:
            return basename_index[base][0]
        return None
    else:
        if raw.startswith("."):
            base_dir = os.path.dirname(from_rel)
            target = os.path.normpath(os.path.join(base_dir, raw)).replace(os.sep, "/")
            for ext_try in ("", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts"):
                cand = target + ext_try
                if cand in files:
                    return cand
        return None


def _pick_entry(files):
    for rel, m in files.items():
        if m["name"] in ENTRY_NAMES:
            return rel
    for rel, m in files.items():
        if "__main__" in m["source_preview"]:
            return rel
    return next(iter(files), None)


def _layered_levels(nodes, edges, entry):
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    levels = {}
    if entry:
        levels[entry] = 0
        q = deque([entry])
        while q:
            cur = q.popleft()
            for nxt in adj[cur]:
                if nxt not in levels:
                    levels[nxt] = levels[cur] + 1
                    q.append(nxt)
    next_level = (max(levels.values()) + 1) if levels else 0
    for n in nodes:
        if n not in levels:
            levels[n] = next_level
    return levels


def _build_insights(files, edges, groups, degree):
    insights = []
    n_files = len(files)
    if len(groups) >= 1 and n_files >= 5:
        insights.append({"type": "ok", "text": "Well organized modular structure"})
    else:
        insights.append({"type": "warn", "text": "Most code lives in a single flat directory — consider splitting into modules"})

    avg_degree = (sum(degree.values()) / len(degree)) if degree else 0
    if avg_degree <= 3:
        insights.append({"type": "ok", "text": "Clear separation of concerns"})
    hot = [n for n, d in degree.items() if d >= 4]
    if hot:
        names = ", ".join(files[n]["name"] for n in hot[:3])
        insights.append({"type": "warn", "text": f"{len(hot)} highly connected module(s) ({names}) — consider refactoring"})

    tool_dirs = [g for g in groups if any(k in g.lower() for k in ("tool", "plugin", "util"))]
    if tool_dirs:
        insights.append({"type": "info", "text": "Tool system is extensible and well designed"})

    if not any(i["type"] == "warn" for i in insights):
        insights.append({"type": "info", "text": "No major architectural red flags detected"})
    return insights


def _detect_stack(files, external_libs):
    found = set()
    for m in files.values():
        for raw in m["raw_imports"]:
            top = raw.split(".")[0].split("/")[0].lstrip("@").lower()
            if top in KNOWN_LIBS:
                found.add(KNOWN_LIBS[top])
    for lib in external_libs:
        if lib in KNOWN_LIBS:
            found.add(KNOWN_LIBS[lib])
    langs = {m["lang"] for m in files.values()}
    if "python" in langs:
        found.add("Python")
    if "javascript" in langs:
        found.add("JavaScript")
    return sorted(found)


def _to_mermaid(files, edges, groups):
    def node_id(rel):
        return "n" + re.sub(r"[^a-zA-Z0-9]", "_", rel)

    lines = ["flowchart TD"]
    root_files = [r for r, m in files.items() if not m["group"]]
    for r in root_files:
        lines.append(f'    {node_id(r)}["{files[r]["name"]}"]')

    for g in groups:
        safe_g = re.sub(r"[^a-zA-Z0-9]", "_", g)
        lines.append(f'    subgraph {safe_g} ["{g}/"]')
        for r, m in files.items():
            if m["group"] == g:
                lines.append(f'        {node_id(r)}["{m["name"]}"]')
        lines.append("    end")

    for a, b in sorted(edges):
        lines.append(f"    {node_id(a)} --> {node_id(b)}")

    return "\n".join(lines)
