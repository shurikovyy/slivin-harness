from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from task_runner import resolve_runtime_path, resolve_toolchain


class RuntimeConfigTests(unittest.TestCase):
    def test_local_config_examples_keep_runtime_projection_project_specific(self) -> None:
        harness_root = Path(__file__).resolve().parents[1]
        with (harness_root / "harness.local.example.toml").open("rb") as handle:
            example = tomllib.load(handle)
        self.assertEqual(example["projects"]["example"]["workspace"]["copy_untracked"], [])
        self.assertEqual(
            example["projects"]["matrix_baseline"]["workspace"]["copy_untracked"],
            ["node_modules"],
        )
        matrix_readme = (harness_root / "cases" / "matrix-all-matching" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('copy_untracked = ["node_modules"]', matrix_readme)

    def test_project_toolchain_is_resolved_from_project_root_and_manifest_wins(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-config-"))
        project = root / "project"
        project.mkdir()

        project_python = project / "runtime" / "python.exe"
        project_python.parent.mkdir(parents=True)
        project_python.write_text("python", encoding="utf-8")

        project_jest = project / "deps" / "jest.js"
        project_jest.parent.mkdir(parents=True)
        project_jest.write_text("jest", encoding="utf-8")

        manifest_jest = root / "override-jest.js"
        manifest_jest.write_text("override", encoding="utf-8")

        local_config = {
            "toolchain": {"shared_tool": str(project_python)},
            "projects": {
                "demo": {
                    "toolchain": {
                        "project_python": "{project_root}/runtime/python.exe",
                        "jest": "{project_root}/deps/jest.js",
                    }
                }
            },
        }
        manifest = {
            "toolchain": {
                "jest": str(manifest_jest),
            }
        }

        toolchain = resolve_toolchain(
            local_config,
            manifest,
            project_name="demo",
            project_root=project,
        )

        self.assertEqual(Path(toolchain["project_python"]), project_python.resolve())
        self.assertEqual(Path(toolchain["jest"]), manifest_jest.resolve())
        self.assertEqual(Path(toolchain["shared_tool"]), project_python.resolve())

    def test_runtime_path_supports_home_and_project_root_without_project_hardcoding(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-path-"))
        project = root / "another-project"
        project.mkdir()
        resolved = resolve_runtime_path(
            "{project_root}/tools/tool.exe",
            base=root,
            project_root=project,
        )
        self.assertEqual(resolved, (project / "tools" / "tool.exe").resolve())


if __name__ == "__main__":
    unittest.main()
