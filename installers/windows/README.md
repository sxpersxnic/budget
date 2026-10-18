# Windows installer

The Windows installer (`Budget-<version>-Windows-Setup.exe`) must be built on a Windows 10/11
PC, because PyInstaller cannot cross-compile from macOS. On a Windows PC:

1. Install the prerequisites (once):

   ```powershell
   winget install --id astral-sh.uv -e
   winget install --id JRSoftware.InnoSetup -e
   ```

2. Double-click `packaging\windows\build.bat`.

The finished installer and its `SHA256SUMS.txt` are written to this folder. Commit them so they
can be downloaded from the repository.
