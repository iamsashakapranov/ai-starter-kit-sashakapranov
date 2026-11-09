#!/usr/bin/env python3
"""Installer integration tests; every destination is an isolated temporary tree."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Tests require Python 3.11+.")

import os
import contextlib
import io
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def snapshot(root):
    if not root.exists():
        return None
    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if path.is_symlink():
            result[str(path.relative_to(root))] = ("link", os.readlink(path))
        elif path.is_file():
            result[str(path.relative_to(root))] = (path.read_bytes(), info.st_mode, info.st_ino, info.st_mtime_ns)
        else:
            result[str(path.relative_to(root))] = ("directory", stat.S_IMODE(info.st_mode))
    return result


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="starter-kit-test-")
        self.scratch = Path(self.temporary.name).resolve()
        self.source = self.scratch / "source with spaces"
        (self.source / "agents").mkdir(parents=True)
        (self.source / "skills" / "example" / "references").mkdir(parents=True)
        for name in ("install.py", "install.sh"):
            shutil.copyfile(REPO / name, self.source / name)
        self.agent = (
            '---\nname: alpha\ndescription: "Описание \\"агента\\""\n'
            'tools: Read, Write\nmodel: sonnet\n---\n\n'
            'Первая строка.\nКавычки """ и путь C:\\example.\n'
        )
        (self.source / "agents" / "alpha.md").write_text(self.agent, encoding="utf-8")
        (self.source / "agents" / "beta.md").write_text(
            "---\nname: beta\ndescription: Second agent\nmodel: sonnet\n---\n\nSecond body.\n", encoding="utf-8"
        )
        (self.source / "skills" / "example" / "SKILL.md").write_text("Example skill\n")
        (self.source / "skills" / "example" / "references" / "extra.md").write_text("Reference\n")
        (self.source / "ROUTING.md").write_text("Routing\n")
        self.target = self.scratch / "target root"
        self.home = self.scratch / "test home"
        # Explicit --target isolates subprocesses. Default paths use a Path.home
        # mock in process instead of changing the HOME environment variable.
        self.env = os.environ.copy()
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.env.pop("CODEX_HOME", None)
        self.env.pop("CLAUDE_CONFIG_DIR", None)

    def tearDown(self):
        self.temporary.cleanup()

    def run_install(self, *arguments, target=True, source=None, env=None, success=True, wrapper=False):
        source = source or self.source
        command = ["bash", str(source / "install.sh")] if wrapper else [sys.executable, str(source / "install.py")]
        if target:
            command += ["--target", str(self.target)]
        command += list(arguments)
        if target:
            result = subprocess.run(command, cwd=self.scratch, env=env or self.env, text=True, capture_output=True)
        else:
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(Path, "home", return_value=self.home), \
                 mock.patch.dict(os.environ, env or self.env, clear=True), \
                 mock.patch.object(sys, "argv", [str(source / "install.py"), *arguments]), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                try:
                    runpy.run_path(str(source / "install.py"), run_name="__main__")
                except SystemExit as exit_result:
                    returncode = exit_result.code
                else:
                    returncode = 0
            result = subprocess.CompletedProcess(command, returncode, output.getvalue(), errors.getvalue())
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_required_client_and_invalid_client_write_nothing(self):
        self.run_install(target=False, success=False)
        self.run_install("--client", "unknown", target=False, success=False)
        self.assertFalse(self.home.exists())
        self.assertFalse(self.target.exists())

    def test_fresh_both_install_native_toml_and_shared_skills(self):
        self.run_install("--client", "both", wrapper=True)
        self.assertEqual((self.target / ".claude/agents/alpha.md").read_text(), self.agent)
        native = tomllib.loads((self.target / ".codex/agents/alpha.toml").read_text())
        self.assertEqual(set(native), {"name", "description", "developer_instructions"})
        self.assertEqual(native["name"], "alpha")
        self.assertEqual(native["description"], 'Описание "агента"')
        self.assertEqual(native["developer_instructions"], self.agent.split("---", 2)[2].strip())
        self.assertNotIn("sonnet", (self.target / ".codex/agents/alpha.toml").read_text())
        for prefix in (".claude/skills", ".agents/skills"):
            self.assertEqual((self.target / prefix / "example/references/extra.md").read_text(), "Reference\n")
        for prefix in (".claude", ".codex"):
            self.assertEqual((self.target / prefix / "ROUTING.md").read_text(), "Routing\n")
        self.assertFalse((self.target / ".codex/skills").exists())
        self.assertFalse((self.target / ".codex/config.toml").exists())

    def test_actual_seven_agent_bodies_and_three_skills(self):
        self.run_install("--client", "both", source=REPO)
        agents = sorted((REPO / "agents").glob("*.md"))
        skills = sorted((REPO / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(agents), 7)
        self.assertEqual(len(skills), 3)
        for path in agents:
            native = tomllib.loads((self.target / ".codex/agents" / (path.stem + ".toml")).read_text())
            self.assertEqual(native["developer_instructions"], path.read_text().split("---", 2)[2].strip())
            self.assertEqual(set(native), {"name", "description", "developer_instructions"})
            self.assertEqual((self.target / ".claude/agents" / path.name).read_bytes(), path.read_bytes())
        for path in skills:
            for prefix in (".claude/skills", ".agents/skills"):
                self.assertEqual((self.target / prefix / path.parent.name / "SKILL.md").read_bytes(), path.read_bytes())

    def test_repeat_preserves_foreign_files_old_packages_and_configs(self):
        self.run_install("--client", "both")
        for relative in (".claude/agents/custom.md", ".agents/skills/old/SKILL.md", ".codex/config.toml", ".codex/auth.json", ".claude/CLAUDE.md", "AGENTS.md"):
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user-owned fixture\n")
        before = snapshot(self.target)
        self.run_install("--client", "both")
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.target / ".starter-kit-backups").exists())

    def test_both_conflict_preflight_prevents_writes_to_other_client(self):
        self.run_install("--client", "codex")
        (self.target / ".codex/agents/beta.toml").write_text("local changes\n")
        before = snapshot(self.target)
        self.run_install("--client", "both", success=False)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.target / ".claude").exists())

    def test_dry_run_does_not_create_any_files_or_directories(self):
        before = snapshot(self.scratch)
        self.run_install("--client", "both", "--dry-run")
        self.assertEqual(snapshot(self.scratch), before)
        self.run_install("--client", "both")
        (self.target / ".claude/ROUTING.md").write_text("conflict\n")
        before = snapshot(self.scratch)
        self.run_install("--client", "both", "--replace", "--dry-run")
        self.assertEqual(snapshot(self.scratch), before)

    def test_replace_backs_up_both_clients_and_preserves_foreign_files(self):
        self.run_install("--client", "both")
        (self.target / ".claude/agents/alpha.md").write_text("old Claude\n")
        (self.target / ".codex/agents/alpha.toml").write_text("old Codex\n")
        foreign = self.target / ".claude/agents/foreign.md"
        foreign.write_text("leave alone\n")
        result = self.run_install("--client", "both", "--replace")
        backups = list((self.target / ".starter-kit-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), result.stdout)
        self.assertEqual((backups[0] / "claude/agents/alpha.md").read_text(), "old Claude\n")
        self.assertEqual((backups[0] / "codex/agents/alpha.toml").read_text(), "old Codex\n")
        self.assertEqual(foreign.read_text(), "leave alone\n")
        self.assertEqual((self.target / ".claude/agents/alpha.md").read_text(), self.agent)
        tomllib.loads((self.target / ".codex/agents/alpha.toml").read_text())

    def test_replace_uses_new_inode_for_hardlinks(self):
        self.run_install("--client", "claude")
        outside = self.scratch / "external file"
        outside.write_text("outside original\n")
        routing = self.target / ".claude/ROUTING.md"
        routing.unlink()
        os.link(outside, routing)
        self.run_install("--client", "claude", "--replace")
        self.assertEqual(outside.read_text(), "outside original\n")
        self.assertEqual(routing.read_text(), "Routing\n")

    def test_symlinks_in_destinations_rejected_before_writes(self):
        outside = self.scratch / "outside"
        outside.mkdir()
        for relative in ("", ".claude", ".claude/agents", ".claude/agents/alpha.md", ".agents/skills"):
            with self.subTest(relative=relative):
                self.target = self.scratch / ("symlink-" + str(len(list(self.scratch.iterdir()))))
                link = self.target / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside)
                before = snapshot(self.scratch)
                self.run_install("--client", "both", "--replace", success=False)
                self.assertEqual(snapshot(self.scratch), before)
        self.target = self.scratch / "dangling target"
        self.target.symlink_to(self.scratch / "nonexistent")
        self.run_install("--client", "both", success=False)
        self.assertFalse((self.scratch / "nonexistent").exists())

    def test_backup_symlink_and_directory_conflicts_rejected(self):
        self.run_install("--client", "both")
        (self.target / ".claude/ROUTING.md").write_text("conflict\n")
        outside = self.scratch / "outside backups"
        outside.mkdir()
        (self.target / ".starter-kit-backups").symlink_to(outside)
        before = snapshot(self.scratch)
        self.run_install("--client", "both", "--replace", success=False)
        self.assertEqual(snapshot(self.scratch), before)
        self.target = self.scratch / "bad type"
        (self.target / ".claude/agents/alpha.md").mkdir(parents=True)
        before = snapshot(self.target)
        self.run_install("--client", "both", "--replace", success=False)
        self.assertEqual(snapshot(self.target), before)

    def test_default_environment_paths_and_backups_are_isolated(self):
        env = self.env | {
            "CLAUDE_CONFIG_DIR": str(self.scratch / "external Claude"),
            "CODEX_HOME": str(self.scratch / "external Codex"),
        }
        self.run_install("--client", "both", target=False, env=env)
        claude = Path(env["CLAUDE_CONFIG_DIR"])
        codex = Path(env["CODEX_HOME"])
        self.assertTrue((claude / "agents/alpha.md").is_file())
        self.assertTrue((codex / "agents/alpha.toml").is_file())
        self.assertTrue((self.home / ".agents/skills/example/SKILL.md").is_file())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())
        (claude / "ROUTING.md").write_text("external old\n")
        self.run_install("--client", "both", "--replace", target=False, env=env)
        backups = list((self.home / ".starter-kit-backups").glob("*/claude/ROUTING.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "external old\n")

    def test_explicit_target_ignores_environment_client_paths(self):
        env = self.env | {"CLAUDE_CONFIG_DIR": str(self.scratch / "ignored Claude"), "CODEX_HOME": str(self.scratch / "ignored Codex")}
        self.run_install("--client", "both", env=env)
        self.assertFalse(Path(env["CLAUDE_CONFIG_DIR"]).exists())
        self.assertFalse(Path(env["CODEX_HOME"]).exists())
        self.assertFalse(self.home.exists())

    def test_overlapping_client_paths_fail_before_writes(self):
        codex_root = self.scratch / "shared config"
        env = self.env | {"CODEX_HOME": str(codex_root), "CLAUDE_CONFIG_DIR": str(codex_root / "ROUTING.md")}
        self.run_install("--client", "both", target=False, env=env, success=False)
        self.assertFalse(codex_root.exists())
        self.assertFalse(self.home.exists())

    def test_defaults_without_overrides_use_isolated_home(self):
        self.run_install("--client", "both", target=False)
        for relative in (".claude/agents/alpha.md", ".codex/agents/alpha.toml", ".agents/skills/example/SKILL.md"):
            self.assertTrue((self.home / relative).is_file())
        self.assertFalse(self.target.exists())

    def test_invalid_agent_sources_fail_before_all_writes(self):
        cases = (
            "No frontmatter\n",
            "---\nname: alpha\n---\nbody\n",
            "---\nname: ../bad\ndescription: x\n---\nbody\n",
            "---\nname: alpha\nname: beta\ndescription: x\n---\nbody\n",
            "---\nname: alpha\ndescription: |\n  lines\n---\nbody\n",
            "---\nname: alpha\ndescription: x\n---\n",
        )
        for content in cases:
            with self.subTest(content=content):
                (self.source / "agents/alpha.md").write_text(content)
                self.run_install("--client", "both", success=False)
                self.assertFalse(self.target.exists())

    def test_source_symlink_and_skill_without_entrypoint_fail(self):
        outside = self.scratch / "outside source"
        outside.write_text("external\n")
        link = self.source / "skills/example/link.md"
        link.symlink_to(outside)
        self.run_install("--client", "both", success=False)
        self.assertFalse(self.target.exists())
        link.unlink()
        (self.source / "skills/example/SKILL.md").unlink()
        self.run_install("--client", "both", success=False)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
