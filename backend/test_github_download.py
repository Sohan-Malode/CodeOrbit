import io
import os
import shutil
import sys
import types
import unittest
import zipfile
from unittest import mock
from urllib.error import HTTPError

if "flask_cors" not in sys.modules:
    try:
        import flask_cors  # noqa: F401
    except ImportError:  # keep tests runnable without the optional dep
        stub = types.ModuleType("flask_cors")
        stub.CORS = lambda *a, **k: None
        sys.modules["flask_cors"] = stub

import app as codeorbit


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/main.py", "print('hi')\n")
    return io.BytesIO(buf.getvalue())


def _http_error(url, code):
    return HTTPError(url, code, "err", {}, io.BytesIO(b""))


class GithubDownloadTests(unittest.TestCase):
    def test_default_branch_download_never_calls_rate_limited_api(self):
        urls = []

        def fake_urlopen(req, timeout=0):
            urls.append(req.full_url)
            return _zip_bytes()

        with mock.patch.object(codeorbit, "urlopen", fake_urlopen):
            upload_dir, root, name = codeorbit._download_github_repo(
                "https://github.com/Pannawish/depxray"
            )

        self.addCleanup(shutil.rmtree, upload_dir, True)
        self.assertEqual(name, "depxray")
        self.assertTrue(os.path.isfile(os.path.join(root, "main.py")))
        self.assertEqual(len(urls), 1)
        self.assertNotIn("api.github.com", urls[0])

    def test_api_403_no_longer_breaks_download(self):
        def fake_urlopen(req, timeout=0):
            if "api.github.com" in req.full_url:
                raise _http_error(req.full_url, 403)
            return _zip_bytes()

        with mock.patch.object(codeorbit, "urlopen", fake_urlopen):
            upload_dir, _, _ = codeorbit._download_github_repo(
                "https://github.com/Pannawish/depxray"
            )

        self.addCleanup(shutil.rmtree, upload_dir, True)

    def test_tree_branch_url(self):
        urls = []

        def fake_urlopen(req, timeout=0):
            urls.append(req.full_url)
            return _zip_bytes()

        with mock.patch.object(codeorbit, "urlopen", fake_urlopen):
            upload_dir, _, _ = codeorbit._download_github_repo(
                "https://github.com/o/r/tree/dev"
            )

        self.addCleanup(shutil.rmtree, upload_dir, True)
        self.assertTrue(urls[0].endswith("/zip/refs/heads/dev"))

    def test_rate_limit_on_archive_gives_helpful_message(self):
        def fake_urlopen(req, timeout=0):
            raise _http_error(req.full_url, 403)

        with mock.patch.object(codeorbit, "urlopen", fake_urlopen):
            with self.assertRaises(ValueError) as ctx:
                codeorbit._download_github_repo("https://github.com/o/r")

        self.assertIn("rate-limiting", str(ctx.exception))
        self.assertIn("Upload ZIP", str(ctx.exception))

    def test_missing_repo_message(self):
        def fake_urlopen(req, timeout=0):
            raise _http_error(req.full_url, 404)

        with mock.patch.object(codeorbit, "urlopen", fake_urlopen):
            with self.assertRaises(ValueError) as ctx:
                codeorbit._download_github_repo("https://github.com/o/r")

        self.assertIn("not found", str(ctx.exception))

    def test_invalid_url(self):
        with self.assertRaises(ValueError):
            codeorbit._download_github_repo("https://example.com/o/r")


if __name__ == "__main__":
    unittest.main()
