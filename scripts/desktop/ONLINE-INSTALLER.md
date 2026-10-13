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

## Uninstallation

After applying the code update, the online wizard runs a small maintenance setup
with the same AppId, user scope and x64 mode as the runtime installer. It appends
to the existing uninstall log, preserving the original runtime file list while
updating the uninstaller and the version shown in Windows Installed Apps.
No second application entry is created.

Uninstall removes application-owned `resources` and `locales` folders, including
files added by later updates, and the downloaded update cache. It does not use
a wildcard over the installation root or delete exported files elsewhere.
Interactive uninstall asks whether to also delete `%APPDATA%/editor-desktop`
(projects, settings and API configuration) and
`%LOCALAPPDATA%/LwentEditor/GeminiWeb` (the app's Gemini login).
The default answer is No; silent uninstall always retains personal data.
Close the app before uninstalling, and close its Gemini browser before choosing
to delete the login profile. A custom `EDITOR_USER_DATA` directory is retained.

Previously installed copies need to run the new online installer once to receive
this uninstall policy; a code-only update does not replace their uninstaller.
