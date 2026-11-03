"""Prepare a release of Budget on this machine: `just pre-release 1.1.0` (docs/RELEASE.md).

`main` is protected and pull requests are squash-merged, so a new version reaches `main` only through a pull
request, and its tag must point at the squashed commit on `main`, not at the branch commit it replaces. The command
therefore works in rounds and finds out by itself which one is due:

1. **Bump** (`main` has an older version): a branch `release/v1.1.0` that sets `[project] version` in
   pyproject.toml and uv.lock, committed, pushed (the pre-push hook runs the checks) and opened as a pull request to
   `main`. Merge it, then run the command again.
2. **Tag and build** (`main` has the version): tag `main` as `v1.1.0` and push the tag, build this machine's
   installer with `just build` (which runs the tests first) and attach it and its `.sha256` to a *draft* GitHub
   release, created on the first run.
3. **Another machine** (the tag exists): build there and add that installer to the same draft.

Publishing the draft stays a deliberate last step (`gh release edit v1.1.0 --draft=false --latest`): from then on
the app's update check (F33) offers the version to everyone who turned it on; a draft it never sees.

Every command that changes something is printed before it runs, and asked for once (`--yes` skips the question,
`--dry-run` only shows the plan). Standard library only, plus git, uv, just and gh on PATH; macOS, Windows, Linux.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess  # nosec B404 - a release tool running fixed commands
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
MAIN = "main"
REMOTE = "origin"
TOOLS = ("git", "uv", "just", "gh")
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")  # 1.1.0 (or v1.1.0); the update check reads tags like v1.1.0
_PROJECT = re.compile(r"^\[project\][ \t]*\r?$", re.M)
_TABLE = re.compile(r"^\[", re.M)
_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]*)"', re.M)

BUMP, TAG, BUILD = "bump", "tag", "build"  # the rounds


class ReleaseError(Exception):
    """A reason to stop, worded as what to do about it."""


# ------------------------------------------------------------------ versions and files
def parse_version(text: str) -> tuple[int, int, int]:
    m = VERSION_RE.match(text.strip())
    if not m:
        raise ReleaseError(f"“{text}” is not a version like 1.1.0.")
    return int(m[1]), int(m[2]), int(m[3])


def normalized(text: str) -> str:
    """ "v1.1.0" → "1.1.0"; refuses anything else."""
    return ".".join(map(str, parse_version(text)))


def project_version(pyproject: str) -> str:
    try:
        return str(tomllib.loads(pyproject)["project"]["version"])
    except (tomllib.TOMLDecodeError, KeyError) as exc:
        raise ReleaseError("pyproject.toml has no [project] version.") from exc


def with_version(pyproject: str, version: str) -> str:
    """`pyproject` with `[project] version` set to `version`; every other byte stays as it was (line endings too)."""
    head = _PROJECT.search(pyproject)
    if head is None:
        raise ReleaseError("pyproject.toml has no [project] table.")
    nxt = _TABLE.search(pyproject, head.end())
    end = nxt.start() if nxt else len(pyproject)
    line = _VERSION_LINE.search(pyproject, head.end(), end)
    if line is None:
        raise ReleaseError("pyproject.toml has no version in its [project] table.")
    changed = pyproject[: line.start(1)] + version + pyproject[line.end(1) :]
    if project_version(changed) != version:  # the file says what it should, read as TOML
        raise ReleaseError("Could not set the version in pyproject.toml.")
    return changed


def due(main_version: str, version: str, tag_on_remote: bool) -> str:
    """Which round a run is: BUMP while `main` is older, TAG once it has the version, BUILD once it is tagged."""
    if tag_on_remote:
        return BUILD
    current, target = parse_version(main_version), parse_version(version)
    if target > current:
        return BUMP
    if target == current:
        return TAG
    raise ReleaseError(f"main is at {main_version} already, past {version}. Choose a version after {main_version}.")


def installers(dist: Path, version: str) -> list[Path]:
    """The files `just build` left for `version`: the installer and its .sha256."""
    files = sorted(p for p in dist.glob(f"Budget-{version}-*") if p.is_file())
    if not any(not p.name.endswith(".sha256") for p in files):
        raise ReleaseError(f"The build left no installer for {version} in {dist}.")
    return files


def pr_body(version: str) -> str:
    return (
        f"Sets the version to {version} in `pyproject.toml` and `uv.lock`.\n\n"
        f"After the merge, run `just pre-release {version}` again on each machine that builds an installer: the "
        f"first run tags `main` as `v{version}`, and every run builds its installer and attaches it to the draft "
        "release (docs/RELEASE.md)."
    )


# ------------------------------------------------------------------ commands
@dataclass
class Shell:
    """Runs the commands. `read` and `test` change nothing and always run; `change` is shown, then run unless dry."""

    cwd: Path = ROOT
    dry_run: bool = False
    changed: list[list[str]] = field(default_factory=list)

    @staticmethod
    def _exe(cmd: tuple[str, ...]) -> list[str]:
        return [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]  # Windows: finds just.exe, gh.exe (PATHEXT)

    def read(self, *cmd: str) -> str:
        done = subprocess.run(self._exe(cmd), cwd=self.cwd, check=True, capture_output=True, text=True)  # noqa: S603  # nosec B603
        return done.stdout.strip()

    def test(self, *cmd: str) -> bool:
        return subprocess.run(self._exe(cmd), check=False, cwd=self.cwd, capture_output=True).returncode == 0  # noqa: S603  # nosec B603

    @staticmethod
    def shown(cmd: tuple[str, ...]) -> str:
        """The command as one could type it, a long argument (the pull request's text) cut short."""
        return shlex.join(a if len(a) <= 60 else a[:57].split("\n")[0] + "…" for a in cmd)

    def change(self, *cmd: str) -> None:
        print(("  would run: " if self.dry_run else "+ ") + self.shown(cmd), flush=True)
        self.changed.append(list(cmd))
        if not self.dry_run:
            subprocess.run(self._exe(cmd), cwd=self.cwd, check=True)  # noqa: S603  # nosec B603


@dataclass
class Release:
    version: str
    sh: Shell
    yes: bool = False
    dist: Path = DIST

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def branch(self) -> str:
        return f"release/{self.tag}"

    # ---------------------------------------------------------------- checks
    def check_tools(self) -> None:
        missing = [t for t in TOOLS if shutil.which(t) is None]
        if missing:
            raise ReleaseError(f"Needs {', '.join(missing)} on PATH (docs/RELEASE.md, Before the first release).")
        if not self.sh.test("gh", "auth", "status"):
            raise ReleaseError("gh is not signed in to GitHub. Run: gh auth login")

    def require_clean(self) -> None:
        if self.sh.read("git", "status", "--porcelain"):
            raise ReleaseError("The working tree has changes. Commit or remove them first (git status).")

    def rev(self, ref: str) -> str:
        return self.sh.read("git", "rev-parse", f"{ref}^{{commit}}")

    def on_main_at_remote(self) -> None:
        """On `main`, at `origin/main`: fast-forwarded when behind, refused when it has commits of its own."""
        if self.sh.read("git", "rev-parse", "--abbrev-ref", "HEAD") != MAIN:
            raise ReleaseError(f"Run it on {MAIN}: git switch {MAIN}")
        if self.rev("HEAD") == self.rev(f"{REMOTE}/{MAIN}"):
            return
        if not self.sh.test("git", "merge-base", "--is-ancestor", "HEAD", f"{REMOTE}/{MAIN}"):
            raise ReleaseError(
                f"{MAIN} has commits that are not on {REMOTE}/{MAIN}. Releases are made from {MAIN} as it is on GitHub."
            )
        self.sh.change("git", "merge", "--ff-only", f"{REMOTE}/{MAIN}")

    def tag_on_remote(self) -> bool:
        return bool(self.sh.read("git", "ls-remote", "--tags", REMOTE, f"refs/tags/{self.tag}"))

    def local_tag(self) -> str:
        """The commit of the local tag, or "" without one."""
        if not self.sh.test("git", "rev-parse", "-q", "--verify", f"refs/tags/{self.tag}"):
            return ""
        return self.rev(self.tag)

    def draft_state(self) -> str:
        """ "none", "draft" or "published": the GitHub release of the tag."""
        if not self.sh.test("gh", "release", "view", self.tag):
            return "none"
        data = json.loads(self.sh.read("gh", "release", "view", self.tag, "--json", "isDraft"))
        return "draft" if data.get("isDraft") else "published"

    def confirm(self, plan: list[str]) -> None:
        print(f"\nBudget {self.version}: this run will")
        for step in plan:
            print(f"  - {step}")
        if self.sh.dry_run:
            print("\nDry run: the commands that would change something are shown, not run.\n")
            return
        if self.yes:
            print()
            return
        try:
            answer = input("\nContinue? [y/N] ")
        except EOFError:  # no terminal to ask
            raise ReleaseError("Nothing done. Run it in a terminal, or add --yes.") from None
        if answer.strip().lower() not in ("y", "yes"):
            raise ReleaseError("Nothing done.")
        print()

    # ---------------------------------------------------------------- the rounds
    def run(self) -> str:
        self.check_tools()
        self.sh.read("git", "fetch", "--tags", REMOTE)
        remote_tag = self.tag_on_remote()
        main_version = project_version(self.sh.read("git", "show", f"{REMOTE}/{MAIN}:pyproject.toml"))
        round_ = due(main_version, self.version, remote_tag)
        {BUMP: self.bump, TAG: self.tag_and_build, BUILD: self.build_here}[round_]()
        return round_

    def bump(self) -> None:
        self.require_clean()
        self.on_main_at_remote()
        if self.sh.test("git", "rev-parse", "-q", "--verify", f"refs/heads/{self.branch}") or self.sh.read(
            "git", "ls-remote", "--heads", REMOTE, self.branch
        ):
            raise ReleaseError(
                f"The branch {self.branch} exists already: its pull request may be waiting (gh pr view {self.branch}). "
                f"To start over: git branch -D {self.branch} && git push {REMOTE} --delete {self.branch}"
            )
        self.confirm(
            [
                f"create the branch {self.branch} from {MAIN}",
                f"set the version to {self.version} in pyproject.toml and uv.lock, and commit it",
                "push the branch (the pre-push hook runs the type check and the tests)",
                f"open a pull request to {MAIN}, then switch back to {MAIN}",
            ]
        )
        self.sh.change("git", "switch", "-c", self.branch)
        pyproject = self.sh.cwd / "pyproject.toml"
        print(
            f"+ set [project] version = {self.version} in pyproject.toml"
            if not self.sh.dry_run
            else "  would set the version in pyproject.toml"
        )
        if not self.sh.dry_run:
            text = pyproject.read_bytes().decode("utf-8")
            pyproject.write_bytes(with_version(text, self.version).encode("utf-8"))
        self.sh.change("uv", "lock")
        self.sh.change("git", "add", "pyproject.toml", "uv.lock")
        self.sh.change("git", "commit", "-m", f"Release {self.version}")
        self.sh.change("git", "push", "-u", REMOTE, self.branch)
        self.sh.change(
            "gh", "pr", "create", "--base", MAIN, "--head", self.branch,
            "--title", f"Release {self.version}", "--body", pr_body(self.version),
        )  # fmt: skip
        self.sh.change("git", "switch", MAIN)
        print(
            f"\nNext: merge the pull request “Release {self.version}” (squash), then run\n"
            f"  just pre-release {self.version}\n"
            f"again: it tags {MAIN}, builds this machine's installer and starts the draft release."
        )

    def tag_and_build(self) -> None:
        self.require_clean()
        self.on_main_at_remote()
        head = self.rev(f"{REMOTE}/{MAIN}")
        local = self.local_tag()
        if local and local != head:
            raise ReleaseError(
                f"A local tag {self.tag} points at another commit. Remove it if it is a leftover: git tag -d {self.tag}"
            )
        state = self.draft_state()
        if state == "published":
            raise ReleaseError(
                f"A published release {self.tag} exists without its tag. Look at it first: gh release view {self.tag}"
            )
        self.confirm(
            [
                f"tag {MAIN} ({head[:7]}) as {self.tag} and push the tag",
                "build this machine's installer: just build (it runs the tests first)",
                f"create the draft release {self.tag} with the installer and its .sha256",
            ]
        )
        if not local:
            self.sh.change("git", "tag", "-a", self.tag, "-m", f"Budget {self.version}")
        self.sh.change("git", "push", REMOTE, self.tag)
        self.build_and_attach(state)

    def build_here(self) -> None:
        """The tag exists (another machine made it): build its commit here and add the installer to the draft.

        Away from the tagged commit (`main` moved on), it switches to the tag for the build and back afterwards,
        also when the build fails."""
        self.require_clean()
        state = self.draft_state()
        if state == "published":
            raise ReleaseError(
                f"{self.tag} is published already. To add an installer anyway: "
                f"gh release upload {self.tag} dist/Budget-{self.version}-*"
            )
        away = self.rev("HEAD") != self.rev(self.tag)
        back = self.sh.read("git", "rev-parse", "--abbrev-ref", "HEAD") if away else ""
        back = "" if back == "HEAD" else back  # a detached checkout has no branch to return to
        self.confirm(
            [
                *(
                    [f"switch to the tag {self.tag} for the build{f', then back to {back}' if back else ''}"]
                    if away
                    else []
                ),
                f"build this machine's installer of {self.tag}: just build (it runs the tests first)",
                f"{'create' if state == 'none' else 'add it to'} the draft release {self.tag}, with its .sha256",
            ]
        )
        if not away:
            self.build_and_attach(state)
            return
        self.sh.change("git", "switch", "--detach", self.tag)
        try:
            self.build_and_attach(state)
        finally:
            if back:
                self.sh.change("git", "switch", back)

    def build_and_attach(self, state: str) -> None:
        self.sh.change("just", "build")
        files = (
            [str(p) for p in installers(self.dist, self.version)]
            if not self.sh.dry_run
            else [f"dist/Budget-{self.version}-*"]
        )
        if state == "none":
            self.sh.change(
                "gh", "release", "create", self.tag, *files, "--draft", "--verify-tag",
                "--title", f"Budget {self.version}", "--generate-notes",
            )  # fmt: skip
        else:
            self.sh.change("gh", "release", "upload", self.tag, *files, "--clobber")
        print(
            f"\nThe draft release {self.tag} {'would have' if self.sh.dry_run else 'has'} this machine's installer.\n"
            f"Next: run `just pre-release {self.version}` on the other machines (macOS, Windows, Linux),\n"
            f"check the draft (gh release view {self.tag} --web), then publish it:\n"
            f"  gh release edit {self.tag} --draft=false --latest"
        )


# ------------------------------------------------------------------ entry point
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just pre-release", description=__doc__.split("\n\n")[0])
    parser.add_argument("version", help="the new version, like 1.1.0")
    parser.add_argument("--dry-run", action="store_true", help="show what would be done, change nothing")
    parser.add_argument("--yes", "-y", action="store_true", help="don't ask before changing something")
    args = parser.parse_args(argv)
    try:
        release = Release(normalized(args.version), Shell(dry_run=args.dry_run), yes=args.yes)
        release.run()
    except ReleaseError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(str(c) for c in exc.cmd)
        print(
            f"\nStopped: `{cmd}` failed (exit {exc.returncode}). Fix that and run the command again.", file=sys.stderr
        )
        print("docs/RELEASE.md, If something goes wrong, says how to pick up from each step.", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
