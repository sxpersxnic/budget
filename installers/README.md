# Installers

Ready-to-install builds of the Budget app. They need no Python, uv or other tools.

| System | File | Checksum |
| --- | --- | --- |
| macOS 11+ (Apple Silicon) | [`macos/Budget-0.1.0-macOS-arm64.dmg`](macos/Budget-0.1.0-macOS-arm64.dmg) | [`macos/SHA256SUMS.txt`](macos/SHA256SUMS.txt) |
| Windows 10/11 (64-bit) | not built yet; see [`windows/`](windows/README.md) | – |

**Download on GitHub:** open the file, then click **Download raw file** (the ↓ icon). Cloning
the repository also downloads the installers.

How to install: see [Install on macOS](../README.md#install-on-macos) and
[Install on Windows](../README.md#install-on-windows) in the main README.

**Verify a download (optional):**

```bash
# macOS
shasum -a 256 -c SHA256SUMS.txt          # run inside installers/macos
```

```powershell
# Windows
Get-FileHash Budget-*-Windows-Setup.exe -Algorithm SHA256   # compare with SHA256SUMS.txt
```

**Updating these files:** the build scripts in [`packaging/`](../packaging/README.md) copy each
finished installer here automatically. They replace the previous version and refresh
`SHA256SUMS.txt`. Commit the result.
