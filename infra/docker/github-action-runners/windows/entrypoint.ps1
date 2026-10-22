# Registers an ephemeral runner, runs one job and exits; the restart policy starts the next one.
$ErrorActionPreference = 'Stop'

$repo = $env:RUNNER_REPOSITORY
$pat = $env:RUNNER_ACCESS_TOKEN
# Jobs inherit this process's environment: the token must not reach them.
Remove-Item Env:RUNNER_ACCESS_TOKEN

$headers = @{
    Authorization          = "Bearer $pat"
    Accept                 = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$token = (Invoke-RestMethod -Method Post -Headers $headers "https://api.github.com/repos/$repo/actions/runners/registration-token").token
Remove-Variable pat

# The container keeps its files across restarts: drop the previous job's registration.
Remove-Item .runner, .credentials, .credentials_rsaparams -Force -ErrorAction SilentlyContinue

# The hostname is the container ID, so a restarted container replaces its own registration.
$name = "$env:RUNNER_NAME_PREFIX-$env:COMPUTERNAME".ToLower()
& .\config.cmd --unattended --url "https://github.com/$repo" --token $token --name $name `
    --labels $env:RUNNER_LABELS --work C:\_work --ephemeral --replace
if ($LASTEXITCODE) { exit $LASTEXITCODE }

& .\run.cmd
exit $LASTEXITCODE
