# Self-hosted GitHub Actions runners

Runners for when GitHub's hosted minutes run out. PyInstaller cannot cross-compile, so each
installer needs a runner on its own operating system. The Mac manages all three:

| Platform | Runs on | Jobs | Repository variable |
| --- | --- | --- | --- |
| `linux` | Docker on the Mac ([`linux/compose.yaml`](linux/compose.yaml)) | CI; Installers: Linux build, Release | `LINUX_RUNNER` |
| `windows` | Docker in Windows-container mode on the PC, over SSH ([`windows/`](windows)) | Installers: Windows build | `WINDOWS_RUNNER` |
| `macos` | [Tart](https://tart.run) VMs on the Mac | Installers: macOS build | `MACOS_RUNNER` |

Every job runs in a fresh container or VM that is deleted afterwards. The access token stays
on the Mac: [`runners.sh`](runners.sh) fetches a registration token for each job (good for one
registration, expires after an hour) and hands only that to the container or VM.

## Commands

All on the Mac, from the repository root. Each takes platforms (`linux windows macos`); none
means all three.

| Command | |
| --- | --- |
| `just runners-setup` | Check prerequisites, build images and the VM template, start the runners, offer to enable them |
| `just runners-start` / `runners-stop` | Start (launchd agents) / stop and remove from GitHub |
| `just runners-status` | Agents, containers and VMs, the runners GitHub sees, routing |
| `just runners-logs` | Follow the logs (`~/Library/Logs/com.budget.runner.<platform>.log`) |
| `just runners-enable` / `runners-disable` | Send the workflow jobs to these runners / back to GitHub's |

Rerun `just runners-setup <platform>` after changing `.env`, `windows/` or `linux/compose.yaml`.

## Before the first setup

- **Token**: a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new)
  with repository access *Only select repositories* → `sxpersxnic/budget` and repository
  permission *Administration: Read and write*. Setup asks for it and stores it in `.env`
  (git-ignored, only on the Mac). When it expires, put a new one there.
- **Mac**: Docker Desktop running, with Rosetta turned on (Settings → General) for the amd64
  Linux containers; Homebrew (setup installs Tart); `gh` logged in for `runners-enable`; about
  30 GB free for the macOS image.
- **PC** (`WINDOWS_SSH_HOST`, default `pc-01`): SSH login with a key, Docker Desktop starting at
  sign-in. Setup switches it to Windows containers; the first switch asks on the PC to enable
  Hyper-V and Containers and restart.

## Settings

[`.env.example`](.env.example) lists them: number of runners per platform, images, VM and
container sizes. Setup copies the `WINDOWS_*` settings to the PC without the token.

- Linux: [`myoung34/github-runner`](https://github.com/myoung34/docker-github-actions-runner) on
  Ubuntu 22.04, whose glibc the Linux installer is built against.
- Windows: Windows Server Core with the GitHub runner, PowerShell 7, Git and Inno Setup 6, every
  download pinned and checked against its SHA-256 (build arguments in the
  [`Dockerfile`](windows/Dockerfile)). `WINDOWS_TAG` must not be newer than the PC's Windows.
- macOS: a [Cirrus Labs image](https://github.com/cirruslabs/macos-image-templates) with the
  Tart guest agent; each job clones the template VM (an APFS copy: seconds, no extra space). At
  most two macOS VMs run at a time. Tart is free for personal use ([licence](https://tart.run/licensing/)).

## Operation

- The Mac stays awake while runners are started (`caffeinate`), and must be logged in. The
  Windows runner also depends on it: the Mac starts the PC's containers.
- Jobs for an enabled platform wait until its runner is online: `just runners-disable` before
  stopping runners for a longer time.
- `runners-stop` stops running jobs too.
- If `runners-logs windows` shows SSH failures while `ssh pc-01` works in a terminal, the key
  is probably only unlocked there: add it to the macOS keychain (`ssh-add --apple-use-keychain`).

## Security

The repository is public. Pull requests from forks always run on GitHub's runners (see
`runs-on` in `ci.yml`); the Installers workflow runs only on pushes to `main`, tags and manual
runs, so outside code never reaches these machines. Additionally, require approval for fork
pull request workflows (Settings → Actions → General).

A job can read everything in its container or VM, so nothing long-lived is in there: the
registration token is spent by the time the job runs, and the container or VM is deleted
afterwards. Jobs run as the image's `runner` user with `sudo` inside the Linux container (the
Linux build installs Qt libraries with `apt-get`), as administrator in the Windows container,
and as `admin` in the macOS VM. The Docker socket is not mounted.
