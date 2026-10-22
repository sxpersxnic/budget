# Registers an ephemeral runner with the token runners.sh passed in, runs one job and exits.
$ErrorActionPreference = 'Stop'

$token = $env:RUNNER_TOKEN
# Jobs inherit this process's environment.
Remove-Item Env:RUNNER_TOKEN

& .\config.cmd --unattended --url "https://github.com/$env:RUNNER_REPOSITORY" --token $token `
    --name $env:RUNNER_NAME --labels $env:RUNNER_LABELS --work C:\_work --ephemeral --replace
if ($LASTEXITCODE) { exit $LASTEXITCODE }

& .\run.cmd
exit $LASTEXITCODE
