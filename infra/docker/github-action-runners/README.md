# Self-hosted GitHub Actions runners

Runners for when GitHub's hosted minutes run out. PyInstaller cannot cross-compile, so each
installer needs a runner on its own operating system:

| Folder | Runs on | Jobs | Repository variable | Label |
| --- | --- | --- | --- | --- |
| [`linux/`](linux) | Docker on any machine | CI; Installers: Linux build, Release | `LINUX_RUNNER` | `budget-linux` |
| [`windows/`](windows) | Docker in Windows-container mode on a Windows PC | Installers: Windows build | `WINDOWS_RUNNER` | `budget-windows` |
| [`macos/`](macos) | [Tart](https://tart.run) VMs on an Apple Silicon Mac | Installers: macOS build | `MACOS_RUNNER` | `budget-macos` |

A job whose variable is unset runs on GitHub's runner as before, so the three can be set up
independently. Docker can only run Linux containers on a Mac, Windows containers need a Windows
kernel, and macOS may only be virtualised on Apple hardware, hence three different hosts.

Every runner is ephemeral: it takes one job, then registers anew.

## Token

All three use a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new)
to fetch short-lived registration tokens; jobs never see it.

- Repository access: *Only select repositories* → `sxpersxnic/budget`
- Repository permissions: *Administration: Read and write* (Metadata: Read is added automatically)

Put it in each folder's `.env` (copy `.env.example`). When it expires, runners can no longer
register: put a new one in and restart them.

## Linux

```sh
cd linux
cp .env.example .env    # set RUNNER_ACCESS_TOKEN
docker compose up -d
docker compose logs -f  # "Listening for Jobs"
gh variable set LINUX_RUNNER --body budget-linux
```

Two runners from [`myoung34/github-runner`](https://github.com/myoung34/docker-github-actions-runner)
(Ubuntu 22.04, whose glibc the Linux installer is built against). They run as `linux/amd64`
like GitHub's runners; on Apple Silicon turn on Rosetta in Docker Desktop first.

- A container exits after its job and restarts with a new registration. It keeps its files,
  so the uv cache stays warm; `docker compose up -d --force-recreate` starts from scratch.
- `docker compose down` deregisters the runners.

## Windows

On a Windows 10/11 Pro or Windows Server machine with Docker Desktop switched to Windows
containers (or Docker Engine on Windows Server), in a clone of this repository:

```powershell
cd infra\docker\github-action-runners\windows
copy .env.example .env    # set RUNNER_ACCESS_TOKEN
docker compose up -d --build
docker compose logs -f
gh variable set WINDOWS_RUNNER --body budget-windows
```

The image (Windows Server Core) brings the GitHub runner, PowerShell 7, Git and Inno Setup 6;
every download is pinned and checked against its SHA-256. Versions are build arguments in the
[`Dockerfile`](windows/Dockerfile). `WINDOWS_TAG` must not be newer than the host.

- Windows 10/11 run containers in Hyper-V isolation, which starts with 1 GB of memory:
  `RUNNER_MEMORY` and `RUNNER_CPUS` in `.env` size the container.
- A stopped runner stays listed as offline until GitHub removes it (ephemeral runners after a
  day); the restarted container replaces its own registration.

## macOS

On this Mac (Apple Silicon):

```sh
brew install cirruslabs/cli/tart
cd macos
cp .env.example .env    # set RUNNER_ACCESS_TOKEN
./runner.sh prepare     # pulls the macOS image (~25 GB) and installs the runner into a template VM
./runner.sh install     # launchd agent: starts at login, log in ~/Library/Logs/com.budget.macos-runner.log
gh variable set MACOS_RUNNER --body budget-macos
```

Each job gets a fresh clone of the template VM (an APFS copy, so it takes seconds and no extra
space), which is deleted afterwards. The VMs are
[Cirrus Labs images](https://github.com/cirruslabs/macos-image-templates) with Homebrew and the
Tart guest agent that `runner.sh` uses to run commands inside them.

- `./runner.sh start` runs in the foreground instead; `./runner.sh uninstall` removes the agent.
- Rerun `prepare` to pick up a newer image or runner version (`.env`).
- The Mac must be awake and logged in for jobs to start; it stays awake while a VM runs.
- Tart is free for personal use; see its [licence](https://tart.run/licensing/).

## Switching back

`gh variable delete LINUX_RUNNER` (or `WINDOWS_RUNNER`, `MACOS_RUNNER`) sends the jobs back to
GitHub's runners. Jobs for a variable that is set wait until a matching runner is online, so
delete the variable before stopping its runners for a longer time.

## Security

The repository is public. Pull requests from forks always run on GitHub's runners (see
`runs-on` in `ci.yml`); the Installers workflow runs only on pushes to `main`, tags and manual
runs. Outside code therefore never reaches these machines. Additionally, require approval for
fork pull request workflows (Settings → Actions → General).

- Linux: jobs run as the image's `runner` user, which has passwordless `sudo` inside the
  container (the Linux build installs Qt libraries with `apt-get`). The Docker socket is not
  mounted.
- Windows: jobs run as the container's administrator, isolated from the host by the container.
- macOS: jobs run as `admin` inside a VM that is thrown away after the job.
