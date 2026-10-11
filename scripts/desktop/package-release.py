"""Create one transferable archive containing the complete offline installer."""
import hashlib
from pathlib import Path
import sys
import shutil
import zipfile

release = Path(sys.argv[1]).resolve()
name = "Editor-Setup-Windows-x64"
files = [release / f"{name}.exe", *sorted(release.glob(f"{name}-*.bin"))]
if not files[0].is_file() or len(files) < 2:
    raise SystemExit("Complete installer .exe and data files are required")

def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

checksums = release / f"{name}.sha256"
checksums.write_text("".join(f"{digest(file)}  {file.name}\n" for file in files), encoding="ascii")
destination = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else release
destination.mkdir(parents=True, exist_ok=True)
archive = destination / "Editor-Windows-x64.zip"
temporary = release / "Editor-Windows-x64.zip.tmp"
with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
    for file in [*files, checksums]:
        output.write(file, file.name)
    output.writestr("CAI_DAT.txt", """EDITOR - WINDOWS 10/11 x64

1. Giai nen TOAN BO file ZIP vao cung mot thu muc.
2. Chay Editor-Setup-Windows-x64.exe de cai dat.
3. Giu cac file .bin ben canh bo cai .exe trong khi cai.
4. Mo Editor tu Desktop hoac Start Menu.

Khong can cai Python, Node.js hay trinh duyet rieng.
VieNeu va ZeroTTS co mo hinh offline; Edge can Internet.
Dich AI can dia chi dich vu va khoa API cua chinh ban.
Mo menu Editor > Cau hinh AI / phan cung de thiet lap.
Ung dung tu kiem tra GPU/CPU. Toc do phu thuoc phan cung va driver.
Bo cai khong chua tai khoan, mat khau hay khoa API ca nhan.
""")
if destination == release:
    temporary.replace(archive)
else:
    # The build volume holds temporary data so the project drive does not need
    # space for two complete installer archives during an update.
    shutil.move(str(temporary), str(archive))
archive.with_suffix(".zip.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
print(f"Complete installer archive: {archive} ({archive.stat().st_size / 1e9:.2f} GB)")
