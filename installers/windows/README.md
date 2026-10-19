# Windows installer

The Windows installer (`Budget-<version>-Windows-Setup.exe`) is built by the
[Installers workflow](../../.github/workflows/installers.yml) on GitHub's Windows runners and
published with each [release](https://github.com/sxpersxnic/budget/releases).

To build it yourself, on a Windows 10/11 PC (PyInstaller cannot cross-compile from macOS):

1. Install the prerequisites (once):

   ```powershell
   winget install --id astral-sh.uv -e
   winget install --id JRSoftware.InnoSetup -e
   ```

2. Double-click `packaging\windows\build.bat`.

The finished installer and its `SHA256SUMS.txt` are written to this folder.
