@echo off
rem Docker credential helper that stores nothing. runners.sh uses it over SSH, where the
rem Windows Credential Manager behind the default helpers is not available; the build only
rem pulls public images.
if "%1"=="list" (
    echo {}
    exit /b 0
)
if "%1"=="get" (
    echo credentials not found in native keychain
    exit /b 1
)
exit /b 0
