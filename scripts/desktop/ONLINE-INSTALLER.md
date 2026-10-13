# Online installation

Distribute `Lwent-Editor-Online-Setup.exe` from the latest GitHub Release.
Users run this file directly; no ZIP extraction or separate BIN files are needed.
The wizard downloads the verified runtime files from `installer-runtime-1.3.0`,
installs them, and applies the code release pinned when the wizard was built.
Downloads require Internet and currently total approximately 6.6 GB.
The runtime still includes all AI models; downloading individual models on first
use is a separate feature. Allow disk space for both downloaded and installed data.

GitHub Actions builds a new small installer after publishing each code update.
The checked-in runtime hashes refer to the verified, fixed runtime release;
changing the Python dependencies/runtime requires publishing a new runtime and
updating the URL and hashes together.

For a local build with full verification:

```powershell
python scripts/desktop/build-online.py --runtime release/Editor-Windows-x64 --manifest release/update/update.json --compiler "$env:LOCALAPPDATA/EditorBuild/tools/inno/ISCC.exe"
```

The normal installed application retains its automatic update mechanism.
AI keys, browser login, project data and user settings are not included in the installer.
