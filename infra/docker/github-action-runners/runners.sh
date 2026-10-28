#!/usr/bin/env bash
# Self-hosted GitHub Actions runners, managed from this Mac through `just runners-*`:
#   linux    Docker containers on this Mac
#   windows  Docker Windows containers on the PC, over SSH
#   macos    Tart VMs on this Mac
# Every job runs in a fresh container or VM. The access token stays on this Mac: a runner only
# gets a registration token, which is good for one registration and expires after an hour.
# See README.md.
set -euo pipefail

DIR=$(cd "$(dirname "$0")" && pwd)
ALL=(linux windows macos)
AGENT=com.budget.runner
TEMPLATE=budget-macos-template
SELECTED=()

# stderr, so it stays out of $(…) captures.
log() { echo "$(date '+%F %T') $*" >&2; }
die() { echo "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: runners.sh <command> [linux|windows|macos ...]   (no platform = all three)

  setup     check prerequisites, build the images and VM template, start the runners
  start     start the runners (launchd agents on this Mac)
  stop      stop the runners and remove them from GitHub
  status    agents, containers/VMs and the runners GitHub sees
  logs      follow the logs
  enable    send the workflow jobs to these runners (repository variables)
  disable   send the workflow jobs back to GitHub's runners
EOF
}

select_platforms() {
  (($#)) || set -- "${ALL[@]}"
  for p in "$@"; do
    [[ " ${ALL[*]} " == *" $p "* ]] || die "Unknown platform '$p': linux, windows or macos"
  done
  SELECTED=("$@")
}

load_env() {
  [[ -f "$DIR/.env" ]] || die "Missing $DIR/.env: run 'just runners-setup'"
  set -a
  # shellcheck source=/dev/null
  source "$DIR/.env"
  set +a
  : "${RUNNER_ACCESS_TOKEN:?Set RUNNER_ACCESS_TOKEN in .env}"
  RUNNER_REPOSITORY=${RUNNER_REPOSITORY:-sxpersxnic/budget}
  LINUX_RUNNERS=${LINUX_RUNNERS:-2}
  WINDOWS_SSH_HOST=${WINDOWS_SSH_HOST:-pc-01}
  WINDOWS_RUNNERS=${WINDOWS_RUNNERS:-1}
  WINDOWS_DIR=${WINDOWS_DIR:-budget-runner-windows}
  [[ "$WINDOWS_DIR" =~ ^[A-Za-z0-9._-]+$ ]] || die "WINDOWS_DIR must be a plain folder name"
  MACOS_RUNNERS=${MACOS_RUNNERS:-1}
  MACOS_IMAGE=${MACOS_IMAGE:-ghcr.io/cirruslabs/macos-tahoe-base:latest}
  MACOS_CPUS=${MACOS_CPUS:-4}
  MACOS_MEMORY=${MACOS_MEMORY:-8192}
  MACOS_RUNNER_VERSION=${MACOS_RUNNER_VERSION:-2.338.0}
  MACOS_RUNNER_SHA256=${MACOS_RUNNER_SHA256:-df4cebda25c86a886ed204e49fee63f5c2e7cec5f447b5c98440a826bbdf9df2}
}

runner_count() {
  case $1 in
    linux) echo "$LINUX_RUNNERS" ;;
    windows) echo "$WINDOWS_RUNNERS" ;;
    # macOS allows at most two macOS VMs per Mac.
    macos) echo $((MACOS_RUNNERS > 2 ? 2 : MACOS_RUNNERS)) ;;
  esac
}

variable_name() { echo "$(tr '[:lower:]' '[:upper:]' <<<"$1")_RUNNER"; }

# --- GitHub ---------------------------------------------------------------------------------

api() {
  local method=$1 path=$2
  curl -fsS -X "$method" \
    -H "Authorization: Bearer $RUNNER_ACCESS_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$RUNNER_REPOSITORY/actions/runners$path"
}

registration_token() {
  local token
  token=$(api POST /registration-token | jq -r .token)
  [[ -n "$token" && "$token" != null ]] || { log "No registration token: check RUNNER_ACCESS_TOKEN"; return 1; }
  echo "$token"
}

# Runners named budget-<platform>-…; a runner that finished its job has removed itself already.
deregister() {
  local id
  for id in $(api GET '?per_page=100' | jq -r --arg p "budget-$1-" '.runners[] | select(.name | startswith($p)) | .id'); do
    api DELETE "/$id" >/dev/null
  done
}

# --- Docker (linux here, windows on the PC) --------------------------------------------------

# Runs a command on the machine that hosts the platform's containers.
on_host() {
  local platform=$1
  shift
  if [[ $platform == windows ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=10 -T "$WINDOWS_SSH_HOST" "$@"
  else
    "$@"
  fi
}

# PowerShell on the PC; encoded, so neither the SSH command line nor PowerShell mangle quotes and braces.
# Progress output would come back as CLIXML on stderr.
remote_ps() {
  on_host windows powershell -NoLogo -NoProfile -NonInteractive -EncodedCommand \
    "$(printf '%s' "\$ProgressPreference = 'SilentlyContinue'; $1" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')"
}

compose() {
  local platform=$1
  shift
  case $platform in
    linux) docker compose --env-file "$DIR/.env" -f "$DIR/linux/compose.yaml" "$@" ;;
    # On the PC the project folder holds its own .env (written by setup, without the token).
    windows) on_host windows docker compose -f "$WINDOWS_DIR/compose.yaml" "$@" ;;
  esac
}

container_job() {
  local platform=$1 slot=$2 token
  if [[ -z "$(on_host "$platform" docker ps -q --filter "name=$slot")" ]]; then
    token=$(registration_token)
    compose "$platform" run -d --rm --name "$slot" \
      -e "RUNNER_TOKEN=$token" -e "RUNNER_NAME=$slot-$(date +%s)" runner >/dev/null
    log "$slot started"
  else
    # Still running, e.g. after the SSH connection to the PC dropped.
    log "$slot still running, waiting for it"
  fi
  on_host "$platform" docker wait "$slot" >/dev/null || true
  log "$slot finished"
}

stop_containers() {
  local ids
  ids=$(on_host "$1" docker ps -q --filter "name=budget-$1-") || { log "$1: cannot reach Docker"; return 0; }
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || on_host "$1" docker stop $ids >/dev/null
}

setup_linux() {
  docker info >/dev/null 2>&1 || die "Docker is not running: start Docker Desktop. For amd64 containers, turn on Rosetta in its settings."
  compose linux pull
}

setup_windows() {
  local os tmp
  on_host windows hostname >/dev/null || die "Cannot reach $WINDOWS_SSH_HOST over SSH (key-based login needed)"
  # Brings Docker Desktop up in Windows-container mode and prints the engine, or what is missing.
  # Anything started over SSH ends with the SSH session, so Docker Desktop is started through a
  # one-off scheduled task in the signed-in user's session; that also needs someone signed in.
  os=$(remote_ps "$(cat <<'PS'
function Engine { cmd /c "docker version --format {{.Server.Os}} 2>nul" }
function Wait-Engine($os) { foreach ($i in 1..60) { if ((Engine) -eq $os) { return }; Start-Sleep 2 } }
if ((Get-WindowsOptionalFeature -Online -FeatureName Containers).State -ne 'Enabled') {
    Enable-WindowsOptionalFeature -Online -FeatureName Containers -All -NoRestart | Out-Null
    'restart'; exit
}
if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Where-Object SessionId -ne 0)) {
    if (-not (quser 2>$null | Select-String $env:USERNAME)) { 'signed-out'; exit }
    $task = 'Start Docker Desktop (budget runners)'
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Register-ScheduledTask -TaskName $task -Force `
        -Action (New-ScheduledTaskAction -Execute 'C:\Program Files\Docker\Docker\Docker Desktop.exe') `
        -Principal (New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive) | Out-Null
    Start-ScheduledTask -TaskName $task
    Start-Sleep 5
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
    Wait-Engine 'linux'
}
if ((Engine) -ne 'windows') {
    cmd /c "docker desktop engine use windows 2>&1" | Out-Null
    Wait-Engine 'windows'
}
Engine
PS
)" | tr -d '\r')
  case $os in
    windows) ;;
    restart) die "Enabled the Windows feature Containers on $WINDOWS_SSH_HOST. Restart the PC
(ssh $WINDOWS_SSH_HOST shutdown /r /t 0), sign in there (Docker Desktop starts at sign-in), then run setup again." ;;
    signed-out) die "Nobody is signed in on $WINDOWS_SSH_HOST, so Docker Desktop cannot start: sign in there, then run setup again." ;;
    *) die "Docker on $WINDOWS_SSH_HOST did not switch to Windows containers ('${os:-not running}'); check Docker Desktop there." ;;
  esac

  log "Copying the image files to $WINDOWS_SSH_HOST:$WINDOWS_DIR"
  remote_ps "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '$WINDOWS_DIR'; New-Item -ItemType Directory '$WINDOWS_DIR' | Out-Null"
  tmp=$(mktemp -d)
  grep -E '^(RUNNER_REPOSITORY|WINDOWS_(TAG|MEMORY|CPUS))=' "$DIR/.env" >"$tmp/.env" || true
  scp -q -o BatchMode=yes "$DIR/windows/Dockerfile" "$DIR/windows/compose.yaml" "$DIR/windows/entrypoint.ps1" \
    "$tmp/.env" "$WINDOWS_SSH_HOST:$WINDOWS_DIR/"
  rm -rf "$tmp"
  log "Building the image on $WINDOWS_SSH_HOST (the first build downloads Windows Server Core)"
  compose windows build
}

# --- Tart (macos) ----------------------------------------------------------------------------

# Waits until the Tart guest agent answers, which is what `tart exec` talks to.
wait_for_agent() {
  for _ in $(seq 90); do
    tart exec "$1" true >/dev/null 2>&1 && return 0
    sleep 2
  done
  log "VM $1 did not come up"
  return 1
}

remove_vm() {
  tart stop "$1" --timeout 30 >/dev/null 2>&1 || true
  tart delete "$1" >/dev/null 2>&1 || true
}

# Job VMs are budget-macos-<slot>-<time>; the template does not match.
remove_job_vms() {
  local vm
  for vm in $(tart list --source local --quiet | grep -E '^budget-macos-[0-9]+-[0-9]+$' || true); do
    remove_vm "$vm"
  done
}

macos_job() {
  local slot=$1 vm token
  vm="$slot-$(date +%s)"
  token=$(registration_token)
  tart clone "$TEMPLATE" "$vm"
  # shellcheck disable=SC2064
  trap "remove_vm $vm" EXIT
  trap 'exit 1' INT TERM
  tart run --no-graphics "$vm" >/dev/null 2>&1 &
  wait_for_agent "$vm"
  log "$vm started"
  tart exec "$vm" bash -c "cd ~/actions-runner &&
    ./config.sh --unattended --url https://github.com/$RUNNER_REPOSITORY --token $token \
      --name $vm --labels budget-macos --ephemeral --replace >/dev/null && ./run.sh"
  log "$vm finished"
}

setup_macos() {
  command -v tart >/dev/null || { log "Installing Tart"; brew install cirruslabs/cli/tart; }
  log "Pulling $MACOS_IMAGE (about 25 GB the first time)"
  tart pull "$MACOS_IMAGE"
  remove_vm "$TEMPLATE"
  tart clone "$MACOS_IMAGE" "$TEMPLATE"
  tart set "$TEMPLATE" --cpu "$MACOS_CPUS" --memory "$MACOS_MEMORY"
  tart run --no-graphics "$TEMPLATE" >/dev/null 2>&1 &
  wait_for_agent "$TEMPLATE"
  log "Installing runner $MACOS_RUNNER_VERSION into $TEMPLATE"
  tart exec "$TEMPLATE" bash -c "set -e
    mkdir -p ~/actions-runner && cd ~/actions-runner
    curl -fsSL -o runner.tar.gz https://github.com/actions/runner/releases/download/v$MACOS_RUNNER_VERSION/actions-runner-osx-arm64-$MACOS_RUNNER_VERSION.tar.gz
    echo '$MACOS_RUNNER_SHA256  runner.tar.gz' | shasum -a 256 -c
    tar xzf runner.tar.gz && rm runner.tar.gz"
  tart stop "$TEMPLATE"
}

# --- Supervisor (one launchd agent per platform) ---------------------------------------------

# One job in a fresh container or VM. Runs as its own process, so `set -e` holds inside.
job() {
  if [[ $1 == macos ]]; then macos_job "$2"; else container_job "$1" "$2"; fi
}

slot() {
  while true; do
    "$DIR/runners.sh" _job "$1" "$2" || { log "$2 failed, retrying in 30 s"; sleep 30; }
  done
}

supervise() {
  local platform=$1 i
  [[ $platform != macos ]] || remove_job_vms
  trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM
  for i in $(seq "$(runner_count "$platform")"); do
    slot "$platform" "budget-$platform-$i" &
  done
  wait
}

plist_path() { echo "$HOME/Library/LaunchAgents/$AGENT.$1.plist"; }
log_path() { echo "$HOME/Library/Logs/$AGENT.$1.log"; }

cmd_start() {
  local p plist
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  for p in "${SELECTED[@]}"; do
    plist=$(plist_path "$p")
    # caffeinate: the Mac does not go to sleep while the runners are started.
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT.$p</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$DIR/runners.sh</string><string>supervise</string><string>$p</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(log_path "$p")</string>
  <key>StandardErrorPath</key><string>$(log_path "$p")</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$AGENT.$p" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "$p: started (just runners-logs $p)"
  done
}

cmd_stop() {
  local p
  for p in "${SELECTED[@]}"; do
    launchctl bootout "gui/$(id -u)/$AGENT.$p" 2>/dev/null || true
    rm -f "$(plist_path "$p")"
    if [[ $p == macos ]]; then
      command -v tart >/dev/null && remove_job_vms
    else
      stop_containers "$p"
    fi
    deregister "$p"
    echo "$p: stopped"
  done
}

cmd_status() {
  local p
  for p in "${SELECTED[@]}"; do
    if launchctl print "gui/$(id -u)/$AGENT.$p" >/dev/null 2>&1; then
      echo "== $p: started"
    else
      echo "== $p: stopped"
    fi
    case $p in
      macos) command -v tart >/dev/null && tart list --source local --quiet | grep '^budget-macos-' || true ;;
      *) on_host "$p" docker ps --filter "name=budget-$p-" 2>/dev/null || echo "Docker not reachable" ;;
    esac
  done
  echo "== GitHub"
  api GET '?per_page=100' |
    jq -r '.runners[] | select(.name | startswith("budget-")) | [.name, .status, (if .busy then "busy" else "idle" end)] | @tsv' ||
    echo "Cannot list the runners: the token needs repository $RUNNER_REPOSITORY with Administration: Read and write"
  for p in "${SELECTED[@]}"; do
    echo "$(variable_name "$p")=$(gh variable get "$(variable_name "$p")" --repo "$RUNNER_REPOSITORY" 2>/dev/null || echo '(unset: GitHub-hosted)')"
  done
}

cmd_logs() {
  local files=() p
  for p in "${SELECTED[@]}"; do
    touch "$(log_path "$p")"
    files+=("$(log_path "$p")")
  done
  tail -n 50 -f "${files[@]}"
}

cmd_enable() {
  local p
  for p in "${SELECTED[@]}"; do
    gh variable set "$(variable_name "$p")" --body "budget-$p" --repo "$RUNNER_REPOSITORY"
  done
}

cmd_disable() {
  local p
  for p in "${SELECTED[@]}"; do
    gh variable delete "$(variable_name "$p")" --repo "$RUNNER_REPOSITORY" 2>/dev/null || true
    echo "$p: jobs run on GitHub's runners"
  done
}

ensure_env() {
  local token
  [[ -f "$DIR/.env" ]] || { cp "$DIR/.env.example" "$DIR/.env"; chmod 600 "$DIR/.env"; }
  grep -qE '^RUNNER_ACCESS_TOKEN=.+' "$DIR/.env" && return
  echo "Fine-grained access token: repository sxpersxnic/budget, permission Administration: Read and write"
  read -rsp "Token: " token
  echo
  [[ -n "$token" ]] || die "No token given"
  sed -i '' "s|^RUNNER_ACCESS_TOKEN=.*|RUNNER_ACCESS_TOKEN=$token|" "$DIR/.env"
}

cmd_setup() {
  local p tool ok=() failed=() answer
  ensure_env
  load_env
  for tool in curl jq gh docker ssh; do
    command -v "$tool" >/dev/null || die "Missing $tool (brew install $tool)"
  done
  api GET '?per_page=1' >/dev/null || die "The token cannot read the runners of $RUNNER_REPOSITORY: check its repository and permission"

  for p in "${SELECTED[@]}"; do
    echo "== $p"
    if "$DIR/runners.sh" _setup "$p"; then ok+=("$p"); else failed+=("$p"); fi
  done
  ((${#ok[@]})) || die "No runner set up"

  SELECTED=("${ok[@]}")
  cmd_start
  ((${#failed[@]} == 0)) || echo "Not set up: ${failed[*]} (see above; run 'just runners-setup ${failed[*]}' again)"

  read -rp "Send the jobs for ${ok[*]} to these runners now? [y/N] " answer
  if [[ $answer == [yY]* ]]; then cmd_enable; else echo "Later: just runners-enable ${ok[*]}"; fi
}

cmd=${1:-help}
shift || true
case $cmd in
  setup | start | stop | status | logs | enable | disable)
    select_platforms "$@"
    [[ $cmd == setup ]] || load_env
    "cmd_$cmd"
    ;;
  supervise) load_env; supervise "$1" ;;
  _setup) load_env; "setup_$1" ;;
  _job) load_env; job "$1" "$2" ;;
  *) usage; [[ $cmd == help ]] ;;
esac
