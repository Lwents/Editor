# Online installer downloads

The installer embeds the official aria2 1.37.0 Windows x64 executable. The build
checks the archive SHA-256 before extracting it. It uses two concurrent files,
four connections per file, HTTPS certificate verification and per-file SHA-256.
It writes directly into `%LOCALAPPDATA%\LwentEditor\InstallerCache` and retains
partial files with their `.aria2` control files when stopped or on an error.
A successful installation removes this cache.

The progress display counts received bytes for every file, including retained
parts. It stays below 100% until all checksum checks pass. The displayed remaining
time is an estimate based on current download speed; checksum verification and
installation take additional time. The downloader binds its temporary RPC server
to loopback with a random token. A mutex prevents two new installers from writing
the same cache simultaneously. The helper stops the downloader when cancelled or
when the installer exits.

Third-party notices are included in the installer and copied to
`resources/app/aria2-COPYING.txt` and `resources/app/aria2-LICENSE.OpenSSL.txt`.
The uninstaller removes these with the other application files.

- Project and documentation: https://github.com/aria2/aria2
- Corresponding source: https://github.com/aria2/aria2/releases/tag/release-1.37.0
- Source archive: https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0.tar.xz
- Build archive: https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip
- License: GPL-2.0-or-later, plus the bundled OpenSSL notice.
