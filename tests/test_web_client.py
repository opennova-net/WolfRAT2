from pathlib import Path
import shutil
import subprocess
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WebClientResultTests(unittest.TestCase):
    def test_browser_renders_operation_results(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the browser client behavior test")

        completed = subprocess.run(
            [
                node,
                str(REPOSITORY_ROOT / "tests" / "web_client_result_test.js"),
                str(REPOSITORY_ROOT / "wolfrat" / "web_templates" / "app.js"),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
