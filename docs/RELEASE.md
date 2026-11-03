# Releasing a new version

A release of Budget is three things:

- the tag `vX.Y.Z` on `main`, at the commit whose `pyproject.toml` says `version = "X.Y.Z"`;
- a GitHub release of that tag with the installer for each system and its checksum: the macOS `.dmg`, the Windows
  `Setup.exe`, the Linux `.tar.gz`, each with its `.sha256`;
- the release being *published*: from then on the app's update check (F33) tells everyone who turned it on that
  the version is available.

`just pre-release X.Y.Z` does as much of this as it can ([`scripts/release.py`](../scripts/release.py)). `main`
is protected and pull requests are squash-merged, so the version change has to go through a pull request, and the
tag can only be made after the merge. Installers can't be cross-built either: each system builds its own. So you
run the same command a few times, and each time it works out by itself what is due:

| Round | When | What it does |
| --- | --- | --- |
| 1. Version | `main` has an older version | Creates the branch `release/vX.Y.Z`, sets the version in `pyproject.toml` and `uv.lock` and commits it. Then it pushes the branch (the pre-push hook runs the type check and the tests), opens the pull request *Release X.Y.Z* and switches back to `main`. |
| 2. Tag and build | `main` has the version, no tag yet | Brings `main` up to date with GitHub, tags it `vX.Y.Z` and pushes the tag. It runs `just build` (tests, then this system's installer) and creates a **draft** release with the installer and its `.sha256`. |
| 3. Build | the tag exists | Runs `just build` on the tagged commit and adds this system's installer and `.sha256` to the draft. If `main` has moved on since the tag, it switches to the tag for the build and back to your branch afterwards. |

Before it changes anything, the command shows what it will do and asks once. Every command that changes something
is printed as it runs. Publishing is not part of it: you do that by hand once the draft holds all three
installers.

Options: `--dry-run` shows the plan and the commands without running them. `--yes` skips the question.

## Before the first release

Do this once on each machine that builds an installer: the Mac, the Windows PC and the Linux machine.

1. Install the tools: [uv](https://docs.astral.sh/uv/), [just](https://just.systems), git and the
   [GitHub CLI](https://cli.github.com) (`gh`).
   - macOS: `brew install uv just gh`
   - Windows: `winget install --id astral-sh.uv -e`, `winget install --id Casey.Just -e`,
     `winget install --id GitHub.cli -e`, and Inno Setup: `winget install --id JRSoftware.InnoSetup -e`
   - Linux: the uv install script, just and gh from your distribution, and the X11 and EGL libraries listed in
     [packaging/README.md](../packaging/README.md#linux).
2. Sign in to GitHub: `gh auth login`. Check it with `gh auth status`. The command needs to push branches and tags,
   open pull requests and create releases.
3. Clone the repository and install the git hooks: `just setup`.
4. On Windows, build from a normal PowerShell window, not one run as administrator, and not over an elevated SSH
   session. A test folder made by an elevated shell is closed to a normal one.
5. To build the Linux installer in WSL, clone the repository a second time inside WSL's own file system, for example
   `~/budget`, and run `just setup` there. Don't build from the Windows checkout under `/mnt/c`: Windows and Linux
   would share one `.venv` and replace each other's, and files under `/mnt/c` are slow enough for UI tests to time
   out.

## Release step by step

The example releases 1.1.0. Run every command from the repository's folder.

1. **Choose the version.** Use three numbers.
   - Raise the last (1.1.**1**) for fixes only.
   - Raise the middle (1.**2**.0) for new features.
   - Raise the first (**2**.0.0) when the data files change so that an older version can't read them any more.

   A release that adds a field to the data files must give the field a default value. A release that changes
   the format must migrate old files when it loads them (see *What makes the new installer an update* in
   [packaging/README.md](../packaging/README.md#releasing-an-update)).
2. **Check that `main` is ready.** Everything planned for the release is merged, and the last CI run on `main`
   passed: `gh run list --branch main --limit 1`.
3. **Update your checkout on the Mac:**

   ```bash
   git switch main
   git pull --ff-only
   ```

4. **Look at the plan:** `just pre-release 1.1.0 --dry-run`. It should say it will create `release/v1.1.0` and
   open a pull request.
5. **Start the release:** `just pre-release 1.1.0`, and answer `y`. The push runs the tests (a few minutes). At
   the end you are back on `main`, and the pull request *Release 1.1.0* is open.
6. **Merge the pull request** *Release 1.1.0* on GitHub (squash) once its checks have passed.
7. **Tag and build on the Mac:** `just pre-release 1.1.0` again, and answer `y`.
   - It updates `main`, tags it `v1.1.0` and pushes the tag.
   - It builds the `.dmg` (the tests run first).
   - It creates the draft release *Budget 1.1.0* with `Budget-1.1.0-macOS-<arch>.dmg` and its `.sha256`. Its notes
     are generated from the pull requests merged since the last release.
8. **Build on the Windows PC.** In PowerShell, not as administrator:

   ```powershell
   git switch main
   git pull --ff-only
   just pre-release 1.1.0
   ```

   It finds the tag, builds `Budget-1.1.0-Windows-Setup.exe` and adds it and its `.sha256` to the draft.
9. **Build on Linux:** the same three commands as on Windows. They add `Budget-1.1.0-Linux-<arch>.tar.gz` and its
   `.sha256`.
10. **Check the draft:** `gh release view v1.1.0 --web`.
    - It holds six files: three installers and three `.sha256` files.
    - Edit the notes: start with two or three sentences on what is new for the user. If the data files changed,
      say that the first start copies them to a backup.
11. **Try at least one installer.** Install it over the previous version.
    - The sidebar says *Budget 1.1.0*.
    - Your data is still there.
    - The data folder has a new `backups/<date>_1.0.0_to_1.1.0/`.
    - Check a downloaded file against its checksum with `shasum -a 256 -c Budget-1.1.0-….sha256` (macOS, Linux).
12. **Publish:**

    ```bash
    gh release edit v1.1.0 --draft=false --latest
    ```

    From now on, the update check offers 1.1.0.
13. **Confirm the update check sees it.** On an installation of the previous version, with *Settings → Updates →
    Check for Updates* on, choose *Check Now*. The sidebar shows *Budget 1.1.0 is available*.
14. **Tidy up.** Delete the branch `release/v1.1.0` if GitHub kept it after the merge:
    `git push origin --delete release/v1.1.0`, then `git branch -D release/v1.1.0` and `git fetch --prune`.

### The first release, 1.0.0

`main` says 1.0.0 already and has no tag, so the first run of `just pre-release 1.0.0` goes straight to round 2.
Skip steps 4 to 6, start at step 7, and leave out the backup check in step 11.

## If something goes wrong

The command stops at the first step that fails and says which one. Until the release is published, everything can
be redone.

- **The pre-push hook fails in round 1.** You are on `release/v1.1.0` with the version commit, and nothing is on
  GitHub. Go back and remove the branch:

  ```bash
  git switch main
  git branch -D release/v1.1.0
  ```

  Fix the failure through a normal pull request, then start again at step 3.
- **The branch was pushed but the pull request didn't open.** Open it by hand:
  `gh pr create --base main --head release/v1.1.0 --title "Release 1.1.0"`. The command refuses to start round 1
  again while the branch exists, and its message says how to remove it.
- **A build fails because of the machine** (Inno Setup missing, a Linux library missing). Install what it names
  and run `just pre-release 1.1.0` again. The tag exists, so it only builds and adds the installer. Tests that
  fail with *JavaScript did not respond within 1.0 s* on one machine only point at a slow disk: in WSL, build from
  a clone in WSL's own file system (step 5 of *Before the first release*).
- **A build fails because of the code** (a test fails on one system). The tag points at a commit that can't be
  released. As long as the release is a draft, remove both and start over:

  ```bash
  gh release delete v1.1.0 --yes --cleanup-tag
  git tag -d v1.1.0
  ```

  Merge the fix into `main` through a pull request, then run step 7 again: `main` still says 1.1.0, so the command
  tags the new commit. On the other machines, the command replaces the old tag with GitHub's by itself.
- **The upload fails** (network). Run the command again: it builds again and replaces the files it uploads
  (`--clobber`).
- **"A local tag v1.1.0 points at another commit"**: a leftover tag from an attempt you removed. Delete it with
  `git tag -d v1.1.0` and run the command again.
- **A published release is wrong.** Don't move its tag: installations may have offered it already. Fix it in
  `main` and release 1.1.1.

## What the command checks

Before it changes anything, the command checks the following. A failed check stops it with a message that says
what to do.

- git, uv, just and gh are on `PATH`, and gh is signed in.
- The version is three numbers (a leading `v` is fine), and it is later than the version on `main`, unless that
  version is already tagged. Numbers are compared as numbers, so 1.0.10 comes after 1.0.9.
- The working tree has no changes.
- Rounds 1 and 2 run on `main`, as it is on GitHub. If your `main` is behind, it is fast-forwarded. If it has
  commits of its own, the command stops.
- Round 1 stops if a branch `release/vX.Y.Z` exists already, locally or on GitHub.
- Round 2 stops if a local tag `vX.Y.Z` points at another commit, or if a published release of that tag exists.
- Where GitHub has the tag, the local tag is GitHub's: a local one that points elsewhere is from an attempt that
  was removed, and the command replaces it (`git fetch --force`) and says so.
- Round 3 builds the tagged commit. Elsewhere, it switches to the tag (`git switch --detach vX.Y.Z`) and back to
  your branch afterwards, also when the build fails. It stops if the release is published already.

The release is a draft, not a GitHub *pre-release*: a draft is invisible to the update check and to users until
you publish it.
