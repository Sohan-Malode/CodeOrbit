import tempfile
import unittest
from pathlib import Path

from analyzer import analyze_repo


class ArchitectureIssueDetectionTests(unittest.TestCase):
    def _write_repo(self, root):
        files = {
            "run.py": "from app import app\n",
            "app/__init__.py": "from .routes import register_routes\n\napp = object()\nregister_routes(app)\n",
            "app/routes.py": "from .services import Service\n\ndef register_routes(app):\n    return Service()\n",
            "app/services.py": "from .repository import Repository\n\nclass Service:\n    def __init__(self):\n        self.repo = Repository()\n",
            "app/repository.py": "from .models import Model\n\nclass Repository:\n    def get(self):\n        return Model()\n",
            "app/models.py": "class Model:\n    pass\n\ndef normalize(model):\n    from .services import Service\n    return model\n",
            "app/legacy.py": "def old_helper():\n    return None\n",
            "tests/test_app.py": "from app import app\n",
        }

        for relative, content in files.items():
            path = Path(root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_detects_cycles_and_disconnected_modules(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_repo(root)
            result = analyze_repo(root)

        self.assertEqual(result["stats"]["circular"], 1)
        self.assertIn(
            "app/models.py",
            result["issues"]["circularNodes"],
        )
        self.assertIn(
            "app/services.py",
            result["issues"]["circularNodes"],
        )
        self.assertIn(
            "app/legacy.py",
            result["issues"]["unusedNodes"],
        )
        self.assertIn(
            "app/legacy.py",
            result["issues"]["deadNodes"],
        )

        legacy_node = next(
            node
            for node in result["nodes"]
            if node["id"] == "app/legacy.py"
        )

        self.assertTrue(legacy_node["isUnused"])
        self.assertTrue(legacy_node["isDead"])

        mermaid = result["mermaid"]
        self.assertIn("CYCLE", mermaid)
        self.assertIn("UNUSED / DEAD", mermaid)
        self.assertIn("classDef circular", mermaid)
        self.assertIn("-.->", mermaid)


if __name__ == "__main__":
    unittest.main()

class MonorepoEntryPointTests(unittest.TestCase):
    def test_uses_multiple_package_entry_points_for_reachability(self):
        with tempfile.TemporaryDirectory() as root:
            files = {
                "package.json": '{"workspaces":["packages/*"]}',
                "packages/one/package.json": '{"main":"./dist/index.js"}',
                "packages/one/src/index.ts": "import { service } from './service'\nservice()\n",
                "packages/one/src/service.ts": "export function service() {}\n",
                "packages/one/src/legacy.ts": "export function legacy() {}\n",
                "packages/two/package.json": '{"main":"./dist/index.js"}',
                "packages/two/src/index.ts": "import { helper } from './helper'\nhelper()\n",
                "packages/two/src/helper.ts": "export function helper() {}\n",
                "packages/two/src/legacy.ts": "export function legacy() {}\n",
            }

            for relative, content in files.items():
                path = Path(root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            result = analyze_repo(root)

        self.assertTrue(result["isMonorepo"])
        self.assertIn("packages/one/src/index.ts", result["entryPoints"])
        self.assertIn("packages/two/src/index.ts", result["entryPoints"])
        self.assertIn("packages/one/src/service.ts", result["issues"]["reachableNodes"])
        self.assertIn("packages/two/src/helper.ts", result["issues"]["reachableNodes"])
        self.assertIn("packages/one/src/legacy.ts", result["issues"]["deadNodes"])
        self.assertIn("packages/two/src/legacy.ts", result["issues"]["deadNodes"])


if __name__ == "__main__":
    unittest.main()


def _write_files(root, files):
    for relative, content in files.items():
        path = Path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class MessyRepoTests(unittest.TestCase):
    """Regression tests for legacy-style repositories."""

    FILES = {
        "manage.py": (
            "from shop.api.orders import create\n"
            "if __name__ == '__main__':\n    create()\n"
        ),
        "shop/__init__.py": "",
        "shop/api/__init__.py": "",
        "shop/api/orders.py": (
            "from shop.core.billing import charge\n"
            "def create():\n    return charge()\n"
        ),
        "shop/core/__init__.py": "",
        "shop/core/billing.py": (
            "from shop.core.inventory import reserve\n"
            "def charge():\n    return reserve()\n"
        ),
        # package-attribute import of a submodule -> real cycle
        "shop/core/inventory.py": (
            "from shop.core import billing\n"
            "def reserve():\n    return billing\n"
        ),
        "shop/legacy/__init__.py": "",
        "shop/legacy/old_export.py": (
            "from shop.legacy.old_helpers import h\n"
        ),
        "shop/legacy/old_helpers.py": "def h():\n    pass\n",
        # sanitized ids collide: my-app/x.py vs my_app/x.py, a_b.py vs a/b.py
        "my-app/x.py": "def a(): pass\n",
        "my_app/x.py": "def b(): pass\n",
        "shop/a_b.py": "def c(): pass\n",
        "shop/a/b.py": "def d(): pass\n",
        # "end" is a reserved Mermaid word
        "end/z.py": "def z(): pass\n",
        "tests/test_billing.py": (
            "import unittest\n"
            "from shop.legacy.old_export import h\n"
            "if __name__ == '__main__':\n    unittest.main()\n"
        ),
    }

    def _analyze(self):
        with tempfile.TemporaryDirectory() as root:
            _write_files(root, self.FILES)
            return analyze_repo(root)

    def test_from_package_import_submodule_creates_cycle(self):
        result = self._analyze()
        components = [
            sorted(c) for c in result["issues"]["circularComponents"]
        ]
        self.assertIn(
            ["shop/core/billing.py", "shop/core/inventory.py"],
            components,
        )

    def test_test_module_with_main_guard_is_not_an_entry_point(self):
        result = self._analyze()
        self.assertEqual(result["entryPoints"], ["manage.py"])
        # only imported by a test -> must still be flagged dead
        self.assertIn(
            "shop/legacy/old_export.py",
            result["issues"]["deadNodes"],
        )
        self.assertIn(
            "shop/legacy/old_helpers.py",
            result["issues"]["deadNodes"],
        )

    def test_mermaid_node_ids_are_unique_and_tree_is_nested(self):
        import re

        result = self._analyze()
        mermaid = result["mermaid"]

        node_ids = re.findall(r"^\s+(n\w+)\[\"", mermaid, re.M)
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertEqual(len(node_ids), len(result["nodes"]))

        subgraph_ids = re.findall(r"subgraph (\w+) ", mermaid)
        self.assertEqual(len(subgraph_ids), len(set(subgraph_ids)))
        self.assertNotIn("end", subgraph_ids)

        # every subgraph is closed
        self.assertEqual(
            len(re.findall(r"^\s*end$", mermaid, re.M)),
            len(subgraph_ids),
        )

        # nested folders: "core/" sits inside "shop/" (deeper indentation)
        shop_line = next(
            l for l in mermaid.splitlines() if '["shop/"]' in l
        )
        core_line = next(
            l for l in mermaid.splitlines() if '["core/"]' in l
        )
        self.assertGreater(
            len(core_line) - len(core_line.lstrip()),
            len(shop_line) - len(shop_line.lstrip()),
        )

    def test_cycle_and_dead_are_flagged_in_mermaid(self):
        mermaid = self._analyze()["mermaid"]
        self.assertIn("[CYCLE] billing.py", mermaid)
        self.assertIn("[DEAD] old_export.py", mermaid)
        self.assertIn("-.->", mermaid)

    def test_bundled_legacy_sample(self):
        sample = Path(__file__).parent / "sample_repo_legacy"
        result = analyze_repo(str(sample))

        self.assertEqual(result["entryPoints"], ["manage.py"])
        self.assertEqual(result["stats"]["circular"], 2)
        components = [sorted(c) for c in result["issues"]["circularComponents"]]
        self.assertIn(
            [
                "shop/core/accounts.py",
                "shop/core/notifications.py",
                "shop/utils/audit.py",
            ],
            components,
        )
        self.assertIn("shop/legacy/coupon_engine.py", result["issues"]["unusedNodes"])
        self.assertIn("shop/legacy/old_export.py", result["issues"]["deadNodes"])


if __name__ == "__main__":
    unittest.main()


class TypeScriptEsmTests(unittest.TestCase):
    """TS ESM repos import './x.js' for x.ts and use barrel re-exports."""

    FILES = {
        "package.json": '{"workspaces":["packages/*"]}',
        "packages/app/package.json": '{"main":"./dist/index.js"}',
        "packages/app/src/index.ts": (
            "import { run } from './commands/run.js';\n"
            "export { helper } from './helper.js';\n"
            "const lazy = await import('./lazy.js');\n"
        ),
        "packages/app/src/commands/run.ts": "export function run() {}\n",
        "packages/app/src/helper.ts": "export const helper = 1;\n",
        "packages/app/src/lazy.ts": "export default 1;\n",
        "packages/app/src/orphan.ts": "export const orphan = 1;\n",
        "packages/app/depxray.config.js": "export default {};\n",
        "packages/app/__tests__/run.test.ts": (
            "import { run } from '../src/commands/run.js';\n"
        ),
        "packages/app/__tests__/fixtures/demo/src/index.ts": "export {};\n",
    }

    def _analyze(self):
        with tempfile.TemporaryDirectory() as root:
            _write_files(root, self.FILES)
            return analyze_repo(root)

    def test_js_specifier_resolves_to_ts_files(self):
        dead = self._analyze()["issues"]["deadNodes"]
        for live in (
            "packages/app/src/commands/run.ts",
            "packages/app/src/helper.ts",
            "packages/app/src/lazy.ts",
        ):
            self.assertNotIn(live, dead)

    def test_real_orphan_is_still_flagged(self):
        issues = self._analyze()["issues"]
        self.assertIn("packages/app/src/orphan.ts", issues["deadNodes"])
        self.assertIn("packages/app/src/orphan.ts", issues["unusedNodes"])

    def test_config_and_tests_are_not_flagged(self):
        issues = self._analyze()["issues"]
        for name in (
            "packages/app/depxray.config.js",
            "packages/app/__tests__/run.test.ts",
        ):
            self.assertNotIn(name, issues["deadNodes"])
            self.assertNotIn(name, issues["unusedNodes"])

    def test_fixtures_are_not_entry_points(self):
        entries = self._analyze()["entryPoints"]
        self.assertEqual(entries, ["packages/app/src/index.ts"])


class BuildScriptTests(unittest.TestCase):
    FILES = {
        "package.json": '{"workspaces":["packages/*"],"scripts":{"sync":"node scripts/sync.mjs"}}',
        "scripts/sync.mjs": "import { x } from '../packages/app/src/tool.js';\n",
        "packages/app/package.json": (
            '{"main":"./dist/index.js","scripts":{"build":"node ./scripts/build.mjs && tsx tools/seed.ts"}}'
        ),
        "packages/app/src/index.ts": "export const a = 1;\n",
        "packages/app/src/tool.ts": "export const x = 1;\n",
        "packages/app/scripts/build.mjs": "console.log('build');\n",
        "packages/app/tools/seed.ts": "console.log('seed');\n",
        "packages/app/src/orphan.ts": "export const o = 1;\n",
    }

    def _analyze(self):
        with tempfile.TemporaryDirectory() as root:
            _write_files(root, self.FILES)
            return analyze_repo(root)

    def test_package_json_scripts_are_entry_points(self):
        entries = self._analyze()["entryPoints"]
        for expected in (
            "scripts/sync.mjs",
            "packages/app/scripts/build.mjs",
            "packages/app/tools/seed.ts",
        ):
            self.assertIn(expected, entries)

    def test_code_imported_by_a_script_is_reachable(self):
        issues = self._analyze()["issues"]
        self.assertNotIn("packages/app/src/tool.ts", issues["deadNodes"])

    def test_scripts_are_never_dead_but_orphans_still_are(self):
        issues = self._analyze()["issues"]
        for name in ("scripts/sync.mjs", "packages/app/scripts/build.mjs"):
            self.assertNotIn(name, issues["deadNodes"])
            self.assertNotIn(name, issues["unusedNodes"])
        self.assertIn("packages/app/src/orphan.ts", issues["deadNodes"])


class HtmlScriptEntryTests(unittest.TestCase):
    FILES = {
        "index.html": (
            '<script src="https://cdn.example.com/x.js"></script>\n'
            '<script defer src="./script.js?v=2"></script>\n'
            '<script src="/static/extra.js"></script>\n'
        ),
        "script.js": "console.log('ui');\n",
        "static/extra.js": "console.log('extra');\n",
        "static/orphan.js": "console.log('orphan');\n",
        "backend/app.py": "if __name__ == '__main__':\n    pass\n",
        "backend/util.py": "def f(): pass\n",
    }

    def _analyze(self):
        with tempfile.TemporaryDirectory() as root:
            _write_files(root, self.FILES)
            return analyze_repo(root)

    def test_scripts_loaded_by_html_are_entries_not_dead(self):
        result = self._analyze()
        for name in ("script.js", "static/extra.js"):
            self.assertIn(name, result["entryPoints"])
            self.assertNotIn(name, result["issues"]["deadNodes"])
            self.assertNotIn(name, result["issues"]["unusedNodes"])

    def test_unreferenced_script_is_still_flagged(self):
        issues = self._analyze()["issues"]
        self.assertIn("static/orphan.js", issues["deadNodes"])

    def test_html_entries_do_not_hide_python_entry(self):
        self.assertIn("backend/app.py", self._analyze()["entryPoints"])


if __name__ == "__main__":
    unittest.main()
