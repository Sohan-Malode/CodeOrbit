"""
CodeOrbit backend.

Run:
    python -m pip install -r requirements.txt
    python app.py

Then open:
    http://localhost:5000
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from analyzer import analyze_repo


# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The frontend files are directly inside the parent folder:
#
# GPT1/
# ├── index.html
# ├── script.js
# ├── styles.css
# └── backend/

FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

SAMPLE_REPO = os.path.join(BASE_DIR, "sample_repo")


# ---------------------------------------------------------
# Sample repository name detection
# ---------------------------------------------------------

def _parse_github_url(github_url):
    """Validate a GitHub repository URL and return (owner, repo)."""
    try:
        parsed = urlparse(github_url.strip())
    except Exception:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    if parsed.netloc.lower() != "github.com":
        return None

    parts = [p for p in parsed.path.strip("/").split("/") if p]

    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]

    if repo.endswith(".git"):
        repo = repo[:-4]

    if not owner or not repo:
        return None

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return None

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return None

    return owner, repo


def _github_default_branch(owner, repo):
    """Get the default branch for a public GitHub repository."""
    api_url = (
        f"https://api.github.com/repos/"
        f"{quote(owner)}/{quote(repo)}"
    )

    req = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeOrbit"
        }
    )

    with urlopen(req, timeout=15) as response:
        data = json.loads(
            response.read().decode("utf-8")
        )

    branch = data.get("default_branch")

    if not branch:
        raise ValueError(
            "GitHub did not provide a default branch for that repository."
        )

    return branch


def _download_github_repo(github_url):
    """
    Download a public GitHub repository as a ZIP archive
    and extract it.

    Returns:
        (temporary_directory, repository_root, project_name)
    """
    parsed = _parse_github_url(github_url)

    if not parsed:
        raise ValueError(
            "Please enter a valid public GitHub repository URL, "
            "for example: https://github.com/user/repository"
        )

    owner, repo = parsed

    try:
        branch = _github_default_branch(owner, repo)

    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError(
                "That GitHub repository was not found or is not public."
            )

        raise ValueError(
            f"GitHub returned HTTP {exc.code} "
            "while looking up the repository."
        )

    except URLError:
        raise ValueError(
            "Could not reach GitHub. "
            "Check your internet connection and try again."
        )

    except TimeoutError:
        raise ValueError(
            "GitHub took too long to respond. "
            "Please try again."
        )

    download_url = (
        f"https://github.com/{quote(owner)}/{quote(repo)}"
        f"/archive/refs/heads/{quote(branch, safe='')}.zip"
    )

    upload_dir = tempfile.mkdtemp(
        prefix="codeorbit_github_"
    )

    zip_path = os.path.join(
        upload_dir,
        "repo.zip"
    )

    try:
        req = Request(
            download_url,
            headers={
                "User-Agent": "CodeOrbit"
            }
        )

        with (
            urlopen(req, timeout=60) as response,
            open(zip_path, "wb") as output
        ):
            shutil.copyfileobj(
                response,
                output
            )

        with zipfile.ZipFile(zip_path) as zf:
            base_path = os.path.realpath(
                upload_dir
            )

            # Validate archive paths before extraction.
            for member in zf.infolist():
                target = os.path.realpath(
                    os.path.join(
                        upload_dir,
                        member.filename
                    )
                )

                if not (
                    target == base_path
                    or target.startswith(
                        base_path + os.sep
                    )
                ):
                    raise ValueError(
                        "GitHub archive contains "
                        "an unsafe file path."
                    )

            zf.extractall(upload_dir)

    except HTTPError as exc:
        shutil.rmtree(
            upload_dir,
            ignore_errors=True
        )

        if exc.code == 404:
            raise ValueError(
                "The repository archive could not be downloaded. "
                "The repository may be empty or unavailable."
            )

        raise ValueError(
            f"GitHub returned HTTP {exc.code} "
            "while downloading the repository."
        )

    except URLError:
        shutil.rmtree(
            upload_dir,
            ignore_errors=True
        )

        raise ValueError(
            "Could not download the repository from GitHub."
        )

    except zipfile.BadZipFile:
        shutil.rmtree(
            upload_dir,
            ignore_errors=True
        )

        raise ValueError(
            "GitHub returned an invalid repository archive."
        )

    except Exception:
        shutil.rmtree(
            upload_dir,
            ignore_errors=True
        )

        raise

    try:
        os.remove(zip_path)
    except OSError:
        pass

    entries = [
        entry
        for entry in os.listdir(upload_dir)
        if not entry.startswith("__MACOSX")
    ]

    if (
        len(entries) == 1
        and os.path.isdir(
            os.path.join(
                upload_dir,
                entries[0]
            )
        )
    ):
        root = os.path.join(
            upload_dir,
            entries[0]
        )

    else:
        root = upload_dir

    return upload_dir, root, repo


def get_sample_project_name(root):
    """
    Try to determine the project name from the repository.

    Priority:
        1. pyproject.toml
        2. package.json
        3. README.md first heading
        4. setup.py
        5. Generic fallback
    """

    # -----------------------------------------------------
    # 1. pyproject.toml
    # -----------------------------------------------------

    pyproject_path = os.path.join(
        root,
        "pyproject.toml"
    )

    if os.path.isfile(pyproject_path):
        try:
            with open(
                pyproject_path,
                "r",
                encoding="utf-8"
            ) as f:
                content = f.read()

            # Handles:
            # name = "My Project"
            match = re.search(
                r'^\s*name\s*=\s*["\']([^"\']+)["\']',
                content,
                re.MULTILINE
            )

            if match:
                return match.group(1).strip()

        except Exception:
            pass

    # -----------------------------------------------------
    # 2. package.json
    # -----------------------------------------------------

    package_json_path = os.path.join(
        root,
        "package.json"
    )

    if os.path.isfile(package_json_path):
        try:
            with open(
                package_json_path,
                "r",
                encoding="utf-8"
            ) as f:
                package_data = json.load(f)

            name = package_data.get("name")

            if name:
                return str(name).strip()

        except Exception:
            pass

    # -----------------------------------------------------
    # 3. README.md
    # -----------------------------------------------------

    readme_path = os.path.join(
        root,
        "README.md"
    )

    if os.path.isfile(readme_path):
        try:
            with open(
                readme_path,
                "r",
                encoding="utf-8"
            ) as f:
                for line in f:
                    line = line.strip()

                    # Look for a Markdown heading:
                    # # My Project
                    # ## My Project
                    match = re.match(
                        r"^#{1,6}\s+(.+?)\s*$",
                        line
                    )

                    if match:
                        name = match.group(1).strip()

                        if name:
                            return name

        except Exception:
            pass

    # -----------------------------------------------------
    # 4. setup.py
    # -----------------------------------------------------

    setup_path = os.path.join(
        root,
        "setup.py"
    )

    if os.path.isfile(setup_path):
        try:
            with open(
                setup_path,
                "r",
                encoding="utf-8"
            ) as f:
                content = f.read()

            match = re.search(
                r'name\s*=\s*["\']([^"\']+)["\']',
                content
            )

            if match:
                return match.group(1).strip()

        except Exception:
            pass

    # -----------------------------------------------------
    # 5. Fallback
    # -----------------------------------------------------

    return "Sample Repository"


# ---------------------------------------------------------
# Flask
# ---------------------------------------------------------

app = Flask(
    __name__,
    static_folder=None
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------
#
# The frontend is hosted on GitHub Pages while the Flask
# backend is hosted on Render. Because these are different
# origins, the browser requires CORS permission.
#
# Only the CodeOrbit GitHub Pages origin is allowed.
# ---------------------------------------------------------

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://sohan-malode.github.io"
            ]
        }
    }
)


# Holds the most recent analysis so /api/explain can
# look up file details without the client re-uploading
# the whole repo.
#
# Fine for a single-user hackathon demo.

LAST_ANALYSIS = {
    "root": None,
    "result": None,
}


# ---------------------------------------------------------
# Frontend
# ---------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(
        FRONTEND_DIR,
        "index.html"
    )


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(
        FRONTEND_DIR,
        filename
    )


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.route(
    "/api/analyze",
    methods=["POST"]
)
def api_analyze():
    upload_dir = None
    project_name = "repository"
    github_url = None

    # -----------------------------------------------------
    # ZIP upload
    # -----------------------------------------------------

    if (
        request.content_type
        and "multipart/form-data"
        in request.content_type
    ):
        file = request.files.get("repo")

        if (
            not file
            or not file.filename.endswith(".zip")
        ):
            return jsonify({
                "error":
                    "Please upload a .zip file "
                    "of your repository."
            }), 400

        upload_dir = tempfile.mkdtemp(
            prefix="codeorbit_"
        )

        zip_path = os.path.join(
            upload_dir,
            "repo.zip"
        )

        file.save(zip_path)

        project_name = os.path.splitext(
            os.path.basename(
                file.filename
            )
        )[0]

        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(
                    upload_dir
                )

        except zipfile.BadZipFile:
            shutil.rmtree(
                upload_dir,
                ignore_errors=True
            )

            return jsonify({
                "error":
                    "That file isn't a valid "
                    ".zip archive."
            }), 400

        os.remove(zip_path)

        # If the ZIP contains one top-level folder,
        # analyze inside that folder.
        entries = [
            e
            for e in os.listdir(upload_dir)
            if not e.startswith("__MACOSX")
        ]

        if (
            len(entries) == 1
            and os.path.isdir(
                os.path.join(
                    upload_dir,
                    entries[0]
                )
            )
        ):
            root = os.path.join(
                upload_dir,
                entries[0]
            )

            project_name = entries[0]

        else:
            root = upload_dir

    # -----------------------------------------------------
    # GitHub repository
    # -----------------------------------------------------

    elif (
        request.is_json
        and request.get_json(silent=True)
    ):
        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        github_url = data.get(
            "github_url"
        )

        if github_url:
            try:
                (
                    upload_dir,
                    root,
                    project_name
                ) = _download_github_repo(
                    github_url
                )

            except ValueError as exc:
                return jsonify({
                    "error": str(exc)
                }), 400

            except Exception as exc:
                return jsonify({
                    "error":
                        "Could not download the "
                        f"GitHub repository: {exc}"
                }), 500

            detected_name = (
                get_sample_project_name(root)
            )

            if (
                detected_name
                != "Sample Repository"
            ):
                project_name = detected_name

        else:
            # -----------------------------------------------------
            # Sample repository
            # -----------------------------------------------------

            root = SAMPLE_REPO

            project_name = (
                get_sample_project_name(
                    root
                )
            )

    # -----------------------------------------------------
    # Sample repository
    # -----------------------------------------------------

    else:
        root = SAMPLE_REPO

        project_name = (
            get_sample_project_name(
                root
            )
        )

    # -----------------------------------------------------
    # Analyze
    # -----------------------------------------------------

    try:
        result = analyze_repo(
            root
        )

    except Exception as exc:
        return jsonify({
            "error":
                f"Analysis failed: {exc}"
        }), 500

    finally:
        if upload_dir:
            # Keep temporary files around for /api/explain
            # and /api/source during this demo session.
            LAST_ANALYSIS["root"] = (
                upload_dir
            )

        else:
            LAST_ANALYSIS["root"] = (
                SAMPLE_REPO
            )

    result["projectName"] = (
        project_name
    )

    LAST_ANALYSIS["result"] = (
        result
    )

    return jsonify(
        result
    )


# ---------------------------------------------------------
# Explain
# ---------------------------------------------------------

@app.route(
    "/api/explain",
    methods=["POST"]
)
def api_explain():
    """
    Explain a file.

    Uses a local Ollama server if available.
    Otherwise falls back to a heuristic summary
    built from the parsed AST.
    """

    data = (
        request.get_json(
            force=True
        )
        or {}
    )

    file_id = data.get(
        "file"
    )

    result = (
        LAST_ANALYSIS["result"]
    )

    # The normal path uses the analysis stored on this backend instance.
    # The frontend also sends the selected node as a fallback so Explain
    # still works if Render restarted the service between requests.
    node = None
    deps = []
    used_by = []

    if result:
        node = next(
            (
                n
                for n in result["nodes"]
                if n["id"] == file_id
            ),
            None
        )

        if node:
            deps = [
                e["target"]
                for e in result["edges"]
                if e["source"] == file_id
            ]

            used_by = [
                e["source"]
                for e in result["edges"]
                if e["target"] == file_id
            ]

    if not node:
        supplied_node = data.get("node")

        if isinstance(supplied_node, dict):
            supplied_id = supplied_node.get("id")

            if supplied_id == file_id:
                node = supplied_node
                deps = supplied_node.get("deps") or []
                used_by = supplied_node.get("usedBy") or []

    if not node:
        return jsonify({
            "error":
                "The analysis session expired. Please analyze the repository again."
        }), 400

    explanation = (
        _try_ollama_explain(
            node,
            deps,
            used_by
        )
    )

    if not explanation:
        explanation = (
            _heuristic_explain(
                node,
                deps,
                used_by
            )
        )

    return jsonify({
        "explanation":
            explanation
    })


# ---------------------------------------------------------
# Heuristic explanation
# ---------------------------------------------------------

def _heuristic_explain(
    node,
    deps,
    used_by
):
    parts = []

    if node["purpose"]:
        parts.append(
            node["purpose"].rstrip(".")
        )

    if node["classes"]:
        parts.append(
            f"defines "
            f"{', '.join(node['classes'][:4])}"
        )

    if node["functions"]:
        parts.append(
            f"exposes "
            f"{', '.join(node['functions'][:4])}"
        )

    if deps:
        parts.append(
            f"depends on "
            f"{len(deps)} module(s) "
            "in this repo"
        )

    if used_by:
        parts.append(
            f"is used by "
            f"{len(used_by)} other module(s)"
        )

    if not parts:
        parts.append(
            "a supporting module with "
            "no detected internal dependencies"
        )

    return (
        f"{node['name']} "
        + "; ".join(parts)
        + "."
    )


# ---------------------------------------------------------
# Ollama explanation
# ---------------------------------------------------------

def _try_ollama_explain(
    node,
    deps,
    used_by
):
    try:
        import requests

    except ImportError:
        return None

    prompt = (
        f"In two plain sentences, explain the likely role "
        f"of the file '{node['name']}' in a software "
        f"architecture. "
        f"It defines classes: {node['classes']}. "
        f"It defines functions: {node['functions']}. "
        f"It imports: {deps}. "
        f"It is imported by: {used_by}."
    )

    try:
        ollama_url = os.getenv(
            "OLLAMA_URL",
            "http://localhost:11434/api/generate"
        )

        ollama_model = os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:3b"
        )

        resp = requests.post(
            ollama_url,
            json={
                "model":
                    ollama_model,
                "prompt":
                    prompt,
                "stream":
                    False,
            },
            timeout=60,
        )

        if resp.ok:
            return (
                resp.json()
                .get(
                    "response",
                    ""
                )
                .strip()
                or None
            )

    except Exception:
        return None

    return None


# ---------------------------------------------------------
# Start server
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(
        debug=False,
        port=5000
    )