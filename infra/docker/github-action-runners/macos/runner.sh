#!/usr/bin/env bash
# Ephemeral macOS runner in Tart VMs: every job runs in a fresh clone of a prepared VM,
# which is deleted afterwards. Settings come from .env next to this script; see ../README.md.
#
#   runner.sh prepare     pull the macOS image and install the GitHub runner into a template VM
#   runner.sh start       take jobs one after another (foreground)
#   runner.sh install     run `start` as a launchd agent at login
#   runner.sh uninstall   remove the launchd agent
set -euo pipefail

DIR=$(cd "$(dirname "$0")" && pwd)
AGENT=com.budget.macos-runner
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
LOG="$HOME/Library/Logs/$AGENT.log"

[[ -f "$DIR/.env" ]] || { echo "Missing $DIR/.env: copy .env.example" >&2; exit 1; }
set -a
# shellcheck source=/dev/null
source "$DIR/.env"
set +a
: "${RUNNER_ACCESS_TOKEN:?Set RUNNER_ACCESS_TOKEN in .env}"
RUNNER_REPOSITORY=${RUNNER_REPOSITORY:-sxpersxnic/budget}
RUNNER_LABELS=${RUNNER_LABELS:-budget-macos}
RUNNER_NAME_PREFIX=${RUNNER_NAME_PREFIX:-budget-macos}
MACOS_IMAGE=${MACOS_IMAGE:-ghcr.io/cirruslabs/macos-tahoe-base:latest}
VM_CPUS=${VM_CPUS:-4}
VM_MEMORY=${VM_MEMORY:-8192}
RUNNER_VERSION=${RUNNER_VERSION:-2.338.0}
RUNNER_SHA256=${RUNNER_SHA256:-df4cebda25c86a886ed204e49fee63f5c2e7cec5f447b5c98440a826bbdf9df2}
TEMPLATE="$RUNNER_NAME_PREFIX-template"

# stderr, so it stays out of $(…) captures.
log() { echo "$(date '+%F %T') $*" >&2; }

# Waits until the Tart guest agent answers, which is what `tart exec` talks to.
wait_for_agent() {
  for _ in $(seq 90); do
    tart exec "$1" true >/dev/null 2>&1 && return 0
    sleep 2
  done
  log "VM $1 did not come up"
  return 1
}

registration_token() {
  local token
  token=$(curl -fsS -X POST \
    -H "Authorization: Bearer $RUNNER_ACCESS_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$RUNNER_REPOSITORY/actions/runners/registration-token" | jq -r .token)
  [[ -n "$token" && "$token" != null ]] || { log "No registration token: check RUNNER_ACCESS_TOKEN"; return 1; }
  echo "$token"
}

local_vms() { tart list --source local --quiet; }

remove_vm() {
  tart stop "$1" --timeout 30 >/dev/null 2>&1 || true
  tart delete "$1" >/dev/null 2>&1 || true
}

prepare() {
  log "Pulling $MACOS_IMAGE"
  tart pull "$MACOS_IMAGE"
  remove_vm "$TEMPLATE"
  tart clone "$MACOS_IMAGE" "$TEMPLATE"
  tart set "$TEMPLATE" --cpu "$VM_CPUS" --memory "$VM_MEMORY"
  tart run --no-graphics "$TEMPLATE" &
  wait_for_agent "$TEMPLATE"
  log "Installing runner $RUNNER_VERSION"
  tart exec "$TEMPLATE" bash -c "set -e
    mkdir -p ~/actions-runner && cd ~/actions-runner
    curl -fsSL -o runner.tar.gz https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-osx-arm64-$RUNNER_VERSION.tar.gz
    echo '$RUNNER_SHA256  runner.tar.gz' | shasum -a 256 -c
    tar xzf runner.tar.gz && rm runner.tar.gz"
  tart stop "$TEMPLATE"
  log "Template $TEMPLATE ready"
}

# One VM, one job. Runs as its own process (`start` calls `runner.sh job`), so `set -e` holds.
job() {
  local vm token
  vm="$RUNNER_NAME_PREFIX-$(date +%s)"
  token=$(registration_token)
  tart clone "$TEMPLATE" "$vm"
  # shellcheck disable=SC2064
  trap "remove_vm $vm" EXIT
  trap 'exit 1' INT TERM
  tart run --no-graphics "$vm" &
  wait_for_agent "$vm"
  log "$vm waiting for a job"
  # Keep the Mac awake while the runner waits and works.
  caffeinate -i tart exec "$vm" bash -c "cd ~/actions-runner &&
    ./config.sh --unattended --url https://github.com/$RUNNER_REPOSITORY --token $token \
      --name $vm --labels $RUNNER_LABELS --ephemeral --replace && ./run.sh"
  log "$vm done"
}

start() {
  local vms
  vms=$(local_vms)
  grep -qx "$TEMPLATE" <<<"$vms" || { echo "No template VM: run '$0 prepare' first" >&2; exit 1; }
  # VMs left behind by an interrupted run.
  for vm in $(grep -E "^$RUNNER_NAME_PREFIX-[0-9]+$" <<<"$vms" || true); do
    remove_vm "$vm"
  done
  while true; do
    # A failed job (network, token, VM boot) must not end the loop.
    "$DIR/runner.sh" job || { log "Runner failed, retrying in 30 s"; sleep 30; }
  done
}

install() {
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT</string>
  <key>ProgramArguments</key><array><string>$DIR/runner.sh</string><string>start</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$AGENT" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Installed $AGENT; log: $LOG"
}

uninstall() {
  launchctl bootout "gui/$(id -u)/$AGENT" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $AGENT"
}

case "${1:-}" in
  prepare | start | job | install | uninstall) "$1" ;;
  *) sed -n '5,8s/^# //p' "$0"; exit 1 ;;
esac
