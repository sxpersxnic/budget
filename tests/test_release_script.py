"""The release command (scripts/release.py, `just pre-release`): which round is due, and what each round runs."""

import importlib.util
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("release_script", PATH)
assert _spec and _spec.loader
release = importlib.util.module_from_spec(_spec)
sys.modules["release_script"] = release  # its dataclasses look their module up there
_spec.loader.exec_module(release)

MAIN_SHA, TAG_SHA, OLD_SHA = "a" * 40, "b" * 40, "c" * 40
PYPROJECT = '[project]\nname = "budget-app"\nversion = "1.0.0"\n\n[tool.other]\nversion = "9.9.9"\n'


class FakeShell(release.Shell):
    """Answers the reading commands from `answers` (output) and `ok` (exit 0); records the changing ones."""

    def __init__(self, cwd: Path, answers: dict, ok: set, dry_run: bool = False):
        super().__init__(cwd=cwd, dry_run=dry_run)
        self.answers, self.ok = answers, ok

    def read(self, *cmd: str) -> str:
        if cmd[:2] == ("git", "fetch"):
            return ""
        assert cmd in self.answers, f"unexpected command: {cmd}"
        return self.answers[cmd]

    def test(self, *cmd: str) -> bool:
        return cmd in self.ok

    def change(self, *cmd: str) -> None:
        self.changed.append(list(cmd))
        if cmd == ("just", "build") and not self.dry_run:
            dist = self.cwd / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "Budget-1.1.0-macOS-arm64.dmg").write_text("dmg")
            (dist / "Budget-1.1.0-macOS-arm64.dmg.sha256").write_text("sum")


def repo(tmp_path: Path, main_version: str = "1.0.0", over: dict | None = None) -> tuple[FakeShell, Path]:
    """A checkout on `main` at origin/main, clean, signed in to gh; `over` replaces answers."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT.replace("1.0.0", main_version, 1))
    answers = {
        ("git", "ls-remote", "--tags", "origin", "refs/tags/v1.1.0"): "",
        ("git", "show", "origin/main:pyproject.toml"): PYPROJECT.replace("1.0.0", main_version, 1),
        ("git", "status", "--porcelain"): "",
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("git", "rev-parse", "HEAD^{commit}"): MAIN_SHA,
        ("git", "rev-parse", "origin/main^{commit}"): MAIN_SHA,
        ("git", "ls-remote", "--heads", "origin", "release/v1.1.0"): "",
        ("git", "rev-parse", "v1.1.0^{commit}"): TAG_SHA,
    }
    answers.update(over or {})
    return FakeShell(tmp_path, answers, {("gh", "auth", "status")}), tmp_path


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")


def run(sh, version: str = "1.1.0", dist: Path | None = None) -> str:
    return release.Release(version, sh, yes=True, dist=dist or sh.cwd / "dist").run()


# ------------------------------------------------------------------ versions and files
def test_a_version_is_three_numbers_with_or_without_v() -> None:
    assert release.normalized("v1.1.0") == release.normalized("1.1.0") == "1.1.0"
    for bad in ("1.1", "1.1.0-rc1", "latest", ""):
        with pytest.raises(release.ReleaseError, match="not a version"):
            release.normalized(bad)


def test_only_the_project_version_changes_and_every_other_byte_stays() -> None:
    crlf = PYPROJECT.replace("\n", "\r\n")
    changed = release.with_version(crlf, "1.1.0")
    assert changed == crlf.replace('version = "1.0.0"', 'version = "1.1.0"')  # line endings kept
    assert 'version = "9.9.9"' in changed  # another table's version is not the app's
    assert release.project_version(changed) == "1.1.0"
    with pytest.raises(release.ReleaseError, match="no version"):
        release.with_version('[project]\nname = "x"\n\n[tool]\nversion = "1"\n', "1.1.0")


def test_the_round_follows_main_and_the_tag() -> None:
    assert release.due("1.0.0", "1.1.0", tag_on_remote=False) == release.BUMP
    assert release.due("1.1.0", "1.1.0", tag_on_remote=False) == release.TAG
    assert release.due("1.2.0", "1.1.0", tag_on_remote=True) == release.BUILD  # another machine, an older tag
    assert release.due("1.0.10", "1.0.9", tag_on_remote=True) == release.BUILD
    with pytest.raises(release.ReleaseError, match=r"past 1\.0\.9"):
        release.due("1.0.10", "1.0.9", tag_on_remote=False)  # compared as numbers, not as text


def test_the_installer_and_its_checksum_are_attached(tmp_path) -> None:
    (tmp_path / "Budget-1.1.0-Windows-Setup.exe").write_text("x")
    (tmp_path / "Budget-1.1.0-Windows-Setup.exe.sha256").write_text("x")
    (tmp_path / "Budget-1.0.0-Windows-Setup.exe").write_text("x")  # an older build stays out
    assert [p.name for p in release.installers(tmp_path, "1.1.0")] == [
        "Budget-1.1.0-Windows-Setup.exe",
        "Budget-1.1.0-Windows-Setup.exe.sha256",
    ]
    with pytest.raises(release.ReleaseError, match="no installer"):
        release.installers(tmp_path, "2.0.0")


# ------------------------------------------------------------------ the rounds
def test_round_one_bumps_the_version_on_a_branch_and_opens_a_pull_request(tmp_path) -> None:
    sh, root = repo(tmp_path)
    assert run(sh) == release.BUMP
    assert release.project_version((root / "pyproject.toml").read_text()) == "1.1.0"
    pr = next(c for c in sh.changed if c[:3] == ["gh", "pr", "create"])
    assert pr[pr.index("--title") + 1] == "Release 1.1.0" and pr[pr.index("--base") + 1] == "main"
    assert [c for c in sh.changed if c is not pr] == [
        ["git", "switch", "-c", "release/v1.1.0"],
        ["uv", "lock"],
        ["git", "add", "pyproject.toml", "uv.lock"],
        ["git", "commit", "-m", "Release 1.1.0"],
        ["git", "push", "-u", "origin", "release/v1.1.0"],
        ["git", "switch", "main"],  # back where it started
    ]


def test_round_one_refuses_a_dirty_tree_another_branch_or_an_existing_release_branch(tmp_path) -> None:
    sh, _ = repo(tmp_path, over={("git", "status", "--porcelain"): " M src/budget_app/main.py"})
    with pytest.raises(release.ReleaseError, match="working tree has changes"):
        run(sh)
    sh, _ = repo(tmp_path, over={("git", "rev-parse", "--abbrev-ref", "HEAD"): "feat/x"})
    with pytest.raises(release.ReleaseError, match="git switch main"):
        run(sh)
    sh, _ = repo(tmp_path, over={("git", "ls-remote", "--heads", "origin", "release/v1.1.0"): "x refs/heads/release"})
    with pytest.raises(release.ReleaseError, match="exists already"):
        run(sh)
    assert sh.changed == []


def test_main_behind_github_is_fast_forwarded_and_ahead_is_refused(tmp_path) -> None:
    behind = {("git", "rev-parse", "HEAD^{commit}"): OLD_SHA}
    sh, _ = repo(tmp_path, over=behind)
    sh.ok.add(("git", "merge-base", "--is-ancestor", "HEAD", "origin/main"))
    run(sh)
    assert sh.changed[0] == ["git", "merge", "--ff-only", "origin/main"]
    sh, _ = repo(tmp_path, over=behind)  # not an ancestor: commits of its own
    with pytest.raises(release.ReleaseError, match="not on origin/main"):
        run(sh)


def test_round_two_tags_main_builds_and_starts_a_draft_release(tmp_path) -> None:
    sh, root = repo(tmp_path, "1.1.0")
    assert run(sh) == release.TAG
    assert sh.changed[:3] == [
        ["git", "tag", "-a", "v1.1.0", "-m", "Budget 1.1.0"],
        ["git", "push", "origin", "v1.1.0"],
        ["just", "build"],
    ]
    create = sh.changed[3]
    assert create[:4] == ["gh", "release", "create", "v1.1.0"]
    assert create[4:6] == [
        str(root / "dist" / "Budget-1.1.0-macOS-arm64.dmg"),
        str(root / "dist" / "Budget-1.1.0-macOS-arm64.dmg.sha256"),
    ]
    assert "--draft" in create and "--verify-tag" in create  # nobody is offered it before it is published
    assert len(sh.changed) == 4


def test_another_machine_builds_the_tag_and_adds_its_installer_to_the_draft(tmp_path) -> None:
    tagged = {
        ("git", "ls-remote", "--tags", "origin", "refs/tags/v1.1.0"): f"{TAG_SHA}\trefs/tags/v1.1.0",
        ("git", "rev-parse", "HEAD^{commit}"): TAG_SHA,
        ("gh", "release", "view", "v1.1.0", "--json", "isDraft"): '{"isDraft": true}',
    }
    sh, _ = repo(tmp_path, "1.1.0", over=tagged)
    sh.ok |= {("git", "rev-parse", "-q", "--verify", "refs/tags/v1.1.0"), ("gh", "release", "view", "v1.1.0")}
    assert run(sh) == release.BUILD
    assert sh.changed[0] == ["just", "build"]
    assert sh.changed[1][:4] == ["gh", "release", "upload", "v1.1.0"] and sh.changed[1][-1] == "--clobber"


def test_a_published_release_is_left_alone(tmp_path) -> None:
    tagged = {
        ("git", "ls-remote", "--tags", "origin", "refs/tags/v1.1.0"): f"{TAG_SHA}\trefs/tags/v1.1.0",
        ("git", "rev-parse", "HEAD^{commit}"): TAG_SHA,
        ("gh", "release", "view", "v1.1.0", "--json", "isDraft"): '{"isDraft": false}',
    }
    sh, _ = repo(tmp_path, "1.1.0", over=tagged)
    sh.ok |= {("git", "rev-parse", "-q", "--verify", "refs/tags/v1.1.0"), ("gh", "release", "view", "v1.1.0")}
    with pytest.raises(release.ReleaseError, match="published already"):
        run(sh)
    assert sh.changed == []  # not even a build


def test_a_dry_run_shows_the_plan_and_changes_nothing(tmp_path, capsys) -> None:
    sh, root = repo(tmp_path)
    sh.dry_run = True
    run(sh)
    assert release.project_version((root / "pyproject.toml").read_text()) == "1.0.0"
    out = capsys.readouterr().out
    assert "Dry run" in out and "create the branch release/v1.1.0" in out


def test_the_command_line_says_why_it_stopped(capsys) -> None:
    assert release.main(["1.1"]) == 1
    assert "not a version like 1.1.0" in capsys.readouterr().err


def test_a_shown_command_can_be_typed_and_a_long_text_is_cut_short() -> None:
    assert release.Shell.shown(("git", "commit", "-m", "Release 1.1.0")) == "git commit -m 'Release 1.1.0'"
    shown = release.Shell.shown(("gh", "pr", "create", "--body", release.pr_body("1.1.0")))
    assert shown.endswith("…'") and len(shown) < 100


def test_another_machine_whose_main_moved_on_builds_the_tag_and_comes_back(tmp_path) -> None:
    tagged = {
        ("git", "ls-remote", "--tags", "origin", "refs/tags/v1.1.0"): f"{TAG_SHA}\trefs/tags/v1.1.0",
        ("gh", "release", "view", "v1.1.0", "--json", "isDraft"): '{"isDraft": true}',
    }  # HEAD is main's newer commit, not the tag's
    sh, _ = repo(tmp_path, "1.1.0", over=tagged)
    sh.ok.add(("gh", "release", "view", "v1.1.0"))
    run(sh)
    assert sh.changed[0] == ["git", "switch", "--detach", "v1.1.0"]
    assert sh.changed[1] == ["just", "build"]
    assert sh.changed[-1] == ["git", "switch", "main"]  # back on main afterwards


def test_a_failed_build_on_the_tag_still_switches_back(tmp_path) -> None:
    tagged = {
        ("git", "ls-remote", "--tags", "origin", "refs/tags/v1.1.0"): f"{TAG_SHA}\trefs/tags/v1.1.0",
        ("gh", "release", "view", "v1.1.0", "--json", "isDraft"): '{"isDraft": true}',
    }
    sh, _ = repo(tmp_path, "1.1.0", over=tagged)
    sh.ok.add(("gh", "release", "view", "v1.1.0"))
    sh.cwd = tmp_path / "elsewhere"  # the build leaves nothing in dist/: no installer to attach
    sh.cwd.mkdir()
    with pytest.raises(release.ReleaseError, match="no installer"):
        release.Release("1.1.0", sh, yes=True, dist=tmp_path / "empty").run()
    assert sh.changed[-1] == ["git", "switch", "main"]
