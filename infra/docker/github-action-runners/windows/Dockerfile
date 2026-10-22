# escape=`
# Windows runner for the Installers workflow's Windows build: the GitHub runner, PowerShell 7
# (the workflow's default shell), Git and Inno Setup 6. uv and Python come from the workflow.
# Needs a Windows host with Docker in Windows-container mode; see ../README.md.
ARG WINDOWS_TAG=ltsc2022
FROM mcr.microsoft.com/windows/servercore:${WINDOWS_TAG}

SHELL ["powershell", "-NoLogo", "-NoProfile", "-Command", "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue';"]

# Versions and SHA-256 digests of the release assets on GitHub.
ARG RUNNER_VERSION=2.338.0
ARG RUNNER_SHA256=f48e0750a21812bca5f82de5f7f5aeae71abee647fab5a582f1742d07eba455f
ARG PWSH_VERSION=7.6.7
ARG PWSH_SHA256=d2f952ebdbf24386cb8923553aa58e41a846db35a6155ea04c58142a4b16dbb7
ARG GIT_TAG=v2.56.0.windows.2
ARG MINGIT_VERSION=2.56.0.2
ARG MINGIT_SHA256=da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be
# build.py and the workflow look for Inno Setup 6, not 7.
ARG INNOSETUP_VERSION=6.7.3
ARG INNOSETUP_TAG=is-6_7_3
ARG INNOSETUP_SHA256=9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732

# Site-packages and PyInstaller's build tree get deeper than 260 characters.
RUN Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled 1

RUN Invoke-WebRequest "https://github.com/PowerShell/PowerShell/releases/download/v$env:PWSH_VERSION/PowerShell-$env:PWSH_VERSION-win-x64.zip" -OutFile pwsh.zip; `
    if ((Get-FileHash pwsh.zip -Algorithm SHA256).Hash -ne $env:PWSH_SHA256) { throw 'PowerShell: checksum mismatch' }; `
    Expand-Archive pwsh.zip 'C:\Program Files\PowerShell\7'; `
    Remove-Item pwsh.zip

RUN Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/$env:GIT_TAG/MinGit-$env:MINGIT_VERSION-64-bit.zip" -OutFile mingit.zip; `
    if ((Get-FileHash mingit.zip -Algorithm SHA256).Hash -ne $env:MINGIT_SHA256) { throw 'MinGit: checksum mismatch' }; `
    Expand-Archive mingit.zip 'C:\Program Files\Git'; `
    Remove-Item mingit.zip

RUN Invoke-WebRequest "https://github.com/jrsoftware/issrc/releases/download/$env:INNOSETUP_TAG/innosetup-$env:INNOSETUP_VERSION.exe" -OutFile innosetup.exe; `
    if ((Get-FileHash innosetup.exe -Algorithm SHA256).Hash -ne $env:INNOSETUP_SHA256) { throw 'Inno Setup: checksum mismatch' }; `
    Start-Process innosetup.exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/ALLUSERS' -Wait; `
    if (-not (Test-Path "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")) { throw 'Inno Setup: ISCC.exe missing' }; `
    Remove-Item innosetup.exe

RUN $path = [Environment]::GetEnvironmentVariable('PATH', 'Machine'); `
    [Environment]::SetEnvironmentVariable('PATH', "$path;C:\Program Files\PowerShell\7;C:\Program Files\Git\cmd", 'Machine')

WORKDIR C:\actions-runner
RUN Invoke-WebRequest "https://github.com/actions/runner/releases/download/v$env:RUNNER_VERSION/actions-runner-win-x64-$env:RUNNER_VERSION.zip" -OutFile runner.zip; `
    if ((Get-FileHash runner.zip -Algorithm SHA256).Hash -ne $env:RUNNER_SHA256) { throw 'Runner: checksum mismatch' }; `
    Expand-Archive runner.zip .; `
    Remove-Item runner.zip

COPY entrypoint.ps1 .
ENTRYPOINT ["C:\\Program Files\\PowerShell\\7\\pwsh.exe", "-NoLogo", "-NoProfile", "-File", "C:\\actions-runner\\entrypoint.ps1"]
