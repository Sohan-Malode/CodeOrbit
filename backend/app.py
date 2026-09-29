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


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
SAMPLE_REPO = os.path.join(BASE_DIR, "sample_repo")


def _parse_github_url(github_url):
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
            base_path = os.path.realpath(upload_dir)

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
                        "GitHub archive contains an unsafe file path."
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

            match = re.search(
                r'^\s*name\s*=\s*["\']([^"\']+)["\']',
                content,
                re.MULTILINE
            )

            if match:
                return match.group(1).strip()

        except Exception:
            pass

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

    return "Sample Repository"


app = Flask(
    __name__,
    static_folder=None
)


ALLOWED_ORIGINS = {
    "https://sohan-malode.github.io",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
}


CORS(
    app,
    resources={
        r"/api/*": {
            "origins": list(ALLOWED_ORIGINS),
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type"],
            "max_age": 600,
        }
    },
)


@app.after_request
def add_cors_headers(response):
    """
    Ensure API responses always include the CORS header for
    approved frontend origins, including error responses.
    """
    origin = request.headers.get("Origin")

    if origin in ALLOWED_ORIGINS and request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"

    return response


LAST_ANALYSIS = {
    "root": None,
    "result": None,
}


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


@app.route(
    "/api/analyze",
    methods=["POST"]
)
def api_analyze():
    upload_dir = None
    project_name = "repository"
    github_url = None

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
                zf.extractall(upload_dir)

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
            root = SAMPLE_REPO

            project_name = (
                get_sample_project_name(
                    root
                )
            )

    else:
        root = SAMPLE_REPO

        project_name = (
            get_sample_project_name(
                root
            )
        )

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
            LAST_ANALYSIS["root"] = upload_dir
        else:
            LAST_ANALYSIS["root"] = SAMPLE_REPO

    result["projectName"] = project_name

    LAST_ANALYSIS["result"] = result

    return jsonify(result)


@app.route(
    "/api/explain",
    methods=["POST"]
)
def api_explain():
    data = (
        request.get_json(
            force=True
        )
        or {}
    )

    file_id = data.get("file")
    client_node = data.get("node") or {}

    result = LAST_ANALYSIS["result"]

    # Render instances can restart between /api/analyze
    # and /api/explain. In that case use the node supplied
    # by the frontend instead of failing immediately.
    node = None

    if result:
        node = next(
            (
                n
                for n in result["nodes"]
                if n["id"] == file_id
            ),
            None
        )

    if not node and client_node:
        node = {
            "id": client_node.get("id", file_id),
            "name": client_node.get(
                "name",
                file_id or "Selected file"
            ),
            "path": client_node.get(
                "path",
                file_id or ""
            ),
            "type": client_node.get(
                "type",
                "file"
            ),
            "language": client_node.get(
                "language",
                "Unknown"
            ),
            "purpose": client_node.get(
                "purpose",
                ""
            ),
            "classes": client_node.get(
                "classes",
                []
            ),
            "functions": client_node.get(
                "functions",
                []
            ),
        }

    if not node:
        if not result:
            return jsonify({
                "error":
                    "Run an analysis first."
            }), 400

        return jsonify({
            "error":
                "Unknown file."
        }), 404

    if result:
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
    else:
        deps = client_node.get("deps", [])
        used_by = client_node.get("usedBy", [])

    explanation = _try_gemini_explain(
        node,
        deps,
        used_by
    )

    if not explanation:
        explanation = _try_ollama_explain(
            node,
            deps,
            used_by
        )

    if not explanation:
        explanation = _heuristic_explain(
            node,
            deps,
            used_by
        )

    return jsonify({
        "explanation": explanation
    })


def _try_gemini_explain(
    node,
    deps,
    used_by
):
    """
    Use Gemini to produce a structured architecture explanation.

    The response is normalized into:
      role
      purpose
      dependencies
      how_it_fits

    Dependencies come from CodeOrbit's analyzed dependency graph rather
    than being invented by the model.
    """
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return None

    try:
        import requests
    except ImportError:
        return None

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.8-flash"
    )

    dependency_text = ", ".join(deps) if deps else "None detected"
    used_by_text = ", ".join(used_by) if used_by else "None detected"

    prompt = f"""
You are CodeOrbit, a software architecture assistant.

Analyze the selected source file using ONLY the supplied repository-analysis
information. Do not invent implementation details.

File name: {node.get("name", "Unknown")}
Path: {node.get("path", "")}
Language: {node.get("language", "Unknown")}
Detected purpose: {node.get("purpose", "")}
Classes: {node.get("classes", [])}
Functions: {node.get("functions", [])}
Internal dependencies detected by CodeOrbit: {dependency_text}
Files/modules that use this file: {used_by_text}

Return ONLY valid JSON with exactly these three string fields:
{{
  "role": "short architectural role",
  "purpose": "one concise sentence describing what the file does",
  "how_it_fits": "one concise sentence describing how this file fits into the architecture"
}}

Rules:
- Keep the role short, usually 2 to 5 words.
- Base the role and purpose on the supplied information.
- Do not invent APIs, classes, functions, or behavior.
- If the information is limited, use cautious wording.
- Do not include Markdown fences.
""".strip()

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )

    try:
        response = requests.post(
            url,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "thinkingConfig": {
                        "thinkingLevel": "low"
                    },
                    "maxOutputTokens": 512,
                },
            },
            timeout=30,
        )

        if not response.ok:
            return None

        payload = response.json()

        candidates = payload.get(
            "candidates",
            []
        )

        if not candidates:
            return None

        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )

        text_parts = [
            part.get("text", "").strip()
            for part in parts
            if part.get("text")
        ]

        raw_text = "\n".join(text_parts).strip()

        if not raw_text:
            return None

        # Be tolerant if the model returns a JSON code fence despite
        # being instructed not to.
        if raw_text.startswith("```"):
            raw_text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                raw_text,
                flags=re.IGNORECASE
            ).strip()

        parsed = json.loads(raw_text)

        role = str(
            parsed.get("role", "")
        ).strip()

        purpose = str(
            parsed.get("purpose", "")
        ).strip()

        how_it_fits = str(
            parsed.get("how_it_fits", "")
        ).strip()

        if not role or not purpose or not how_it_fits:
            return None

        return {
            "role": role,
            "purpose": purpose,
            "dependencies": list(deps),
            "how_it_fits": how_it_fits,
        }

    except Exception:
        return None


def _try_ollama_explain(
    node,
    deps,
    used_by
):
    try:
        import requests
    except ImportError:
        return None

    ollama_url = os.getenv(
        "OLLAMA_URL",
        "http://localhost:11434/api/generate"
    )

    ollama_model = os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5-coder:3b"
    )

    prompt = f"""
Return ONLY valid JSON with exactly these fields:
{{
  "role": "short architectural role",
  "purpose": "one concise sentence describing what the file does",
  "how_it_fits": "one concise sentence describing how it fits into the architecture"
}}

Selected file: {node.get("name", "Unknown")}
Path: {node.get("path", "")}
Language: {node.get("language", "Unknown")}
Detected purpose: {node.get("purpose", "")}
Classes: {node.get("classes", [])}
Functions: {node.get("functions", [])}
Internal dependencies: {deps}
Used by: {used_by}

Do not invent functionality.
""".strip()

    try:
        resp = requests.post(
            ollama_url,
            json={
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=15,
        )

        if not resp.ok:
            return None

        raw_text = (
            resp.json()
            .get("response", "")
            .strip()
        )

        if not raw_text:
            return None

        if raw_text.startswith("```"):
            raw_text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                raw_text,
                flags=re.IGNORECASE
            ).strip()

        parsed = json.loads(raw_text)

        role = str(
            parsed.get("role", "")
        ).strip()

        purpose = str(
            parsed.get("purpose", "")
        ).strip()

        how_it_fits = str(
            parsed.get("how_it_fits", "")
        ).strip()

        if not role or not purpose or not how_it_fits:
            return None

        return {
            "role": role,
            "purpose": purpose,
            "dependencies": list(deps),
            "how_it_fits": how_it_fits,
        }

    except Exception:
        return None


def _heuristic_explain(
    node,
    deps,
    used_by
):
    name = str(
        node.get("name")
        or node.get("id")
        or "This file"
    )

    path = str(
        node.get("path")
        or name
    )

    purpose = str(
        node.get("purpose")
        or ""
    ).strip()

    classes = node.get(
        "classes",
        []
    ) or []

    functions = node.get(
        "functions",
        []
    ) or []

    basename = os.path.basename(
        path
    ).lower()

    clean_purpose = purpose.rstrip(
        "."
    ).strip()

    entry_files = {
        "run.py",
        "main.py",
        "__main__.py",
        "index.py",
        "server.py",
        "app.py",
        "index.js",
        "server.js",
        "app.js",
        "main.js",
        "index.ts",
        "server.ts",
        "app.ts",
        "main.ts",
    }

    if basename in entry_files:
        role = "Application entry point"
        purpose_text = (
            "Starts the application and initializes the backend."
        )
        if deps:
            how_it_fits = (
                "Acts as a starting point for the application "
                "and connects to the modules it depends on."
            )
        else:
            how_it_fits = (
                "Acts as a starting point for the application "
                "within the analyzed repository."
            )

    elif "test" in basename or "tests" in path.lower():
        role = "Test module"
        purpose_text = (
            "Contains automated tests for the analyzed application."
        )
        how_it_fits = (
            "Validates behavior in the repository and helps verify "
            "that connected modules work as expected."
        )

    elif "route" in basename or "api" in basename:
        role = "Routing module"
        purpose_text = (
            clean_purpose
            if clean_purpose
            else "Defines application routing and request-handling logic."
        )
        how_it_fits = (
            "Provides a routing layer that connects application "
            "entry points with backend functionality."
        )

    elif "config" in basename or "settings" in basename:
        role = "Configuration module"
        purpose_text = (
            clean_purpose
            if clean_purpose
            else "Provides configuration used by the application."
        )
        how_it_fits = (
            "Centralizes configuration information used by other "
            "parts of the application."
        )

    elif classes:
        role = "Core module"
        purpose_text = (
            clean_purpose
            if clean_purpose
            else f"Defines {', '.join(map(str, classes[:4]))}."
        )
        how_it_fits = (
            "Provides reusable application logic for the modules "
            "that depend on it."
        )

    elif functions:
        role = "Utility module"
        purpose_text = (
            clean_purpose
            if clean_purpose
            else f"Provides reusable functions including "
                 f"{', '.join(map(str, functions[:4]))}."
        )
        how_it_fits = (
            "Provides reusable functionality that can be consumed "
            "by other modules in the repository."
        )

    elif clean_purpose:
        role = "Application module"
        purpose_text = clean_purpose + (
            "" if clean_purpose.endswith(".") else "."
        )
        how_it_fits = (
            "Contributes functionality to the application and "
            "connects to the repository through its detected relationships."
        )

    else:
        role = "Source module"
        language = node.get(
            "language",
            "source"
        )
        purpose_text = (
            f"Contains {language} source code used by the project."
        )
        how_it_fits = (
            "Contributes to the project structure through its "
            "detected dependencies and relationships."
        )

    return {
        "role": role,
        "purpose": purpose_text,
        "dependencies": list(deps),
        "how_it_fits": how_it_fits,
    }


if __name__ == "__main__":
    app.run(
        debug=False,
        port=5000
    )
