"""Extract the last client zip, inject the three fixes, re-zip."""
from __future__ import annotations

import io
import os
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
from PyInstaller.building.utils import get_code_object
from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_NSPKG, PYZ_ITEM_PKG

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parents[1]
OLD_ZIP = ROOT / "حسابداری-صرافی-نسخه-جدید.zip"
OUT_ZIP = ROOT / "جدید ترین نسخه.zip"

REPLACE_MODULES = {
    "ledger.views": SRC / "ledger" / "views.py",
    "ledger.forms": SRC / "ledger" / "forms.py",
    "ledger.services": SRC / "ledger" / "services.py",
    "ledger.balances": SRC / "ledger" / "balances.py",
    "reports.views": SRC / "reports" / "views.py",
}

TEMPLATE_FILES = [
    "ledger/party_transfer_form.html",
    "ledger/expense_income_form.html",
    "ledger/statement.html",
    "ledger/voucher_list.html",
]


def _cookie(exe: Path, reader: CArchiveReader):
    with open(exe, "rb") as fp:
        fp.seek(reader._end_offset - reader._COOKIE_LENGTH)
        data = fp.read(reader._COOKIE_LENGTH)
    magic, archive_length, toc_offset, toc_length, pyvers, pylib = struct.unpack(
        reader._COOKIE_FORMAT, data
    )
    pylib = pylib.split(b"\0", 1)[0].decode("ascii")
    return magic, archive_length, toc_offset, toc_length, pyvers, pylib


def _toc_entries(exe: Path, reader: CArchiveReader):
    _magic, _alen, toc_offset, toc_length, _pyvers, _pylib = _cookie(exe, reader)
    with open(exe, "rb") as fp:
        fp.seek(reader._start_offset + toc_offset)
        data = fp.read(toc_length)
    entries = []
    cur = 0
    header_len = reader._TOC_ENTRY_LENGTH
    while cur < len(data):
        entry_length, entry_offset, data_length, uncompressed_length, compression_flag, typecode = (
            struct.unpack(reader._TOC_ENTRY_FORMAT, data[cur:cur + header_len])
        )
        cur += header_len
        name_length = entry_length - header_len
        name = data[cur:cur + name_length].split(b"\0", 1)[0].decode("utf-8")
        cur += name_length
        entries.append((
            name,
            entry_offset,
            data_length,
            uncompressed_length,
            int(compression_flag),
            typecode.decode("ascii"),
        ))
    return entries


def patch_exe(exe: Path) -> None:
    reader = CArchiveReader(str(exe))
    pyz_name = next(name for name, entry in reader.toc.items() if entry[-1] == "z")
    pyz = reader.open_embedded_archive(pyz_name)

    missing = [name for name in REPLACE_MODULES if name not in pyz.toc]
    if missing:
        raise SystemExit(f"PYZ missing modules: {missing}")

    code_dict = {}
    pyz_entries = []
    for name, (typecode, _off, _length) in pyz.toc.items():
        if typecode == PYZ_ITEM_NSPKG:
            pyz_entries.append((name, "-", "PYMODULE"))
            continue
        code_dict[name] = pyz.extract(name)
        if typecode == PYZ_ITEM_PKG:
            src_path = os.path.join(*name.split("."), "__init__.py")
        else:
            src_path = os.path.join(*name.split(".")) + ".py"
        pyz_entries.append((name, src_path, "PYMODULE"))

    for name, src in REPLACE_MODULES.items():
        code_dict[name] = get_code_object(name, str(src), optimize=0)
        print(f"patched module {name}")

    work = Path(tempfile.mkdtemp(prefix="sarrafi-patch-"))
    try:
        new_pyz = work / "new.pyz"
        ZlibArchiveWriter(str(new_pyz), pyz_entries, code_dict=code_dict)
        pyz_bytes = new_pyz.read_bytes()

        magic, _alen, _toc, _tlen, pyvers, pylib = _cookie(exe, reader)
        pkg = io.BytesIO()
        toc = []
        with open(exe, "rb") as fp:
            for name, offset, data_length, ulen, compress, typecode in _toc_entries(exe, reader):
                if typecode == "z":
                    payload = zlib.compress(pyz_bytes, 9) if compress else pyz_bytes
                    ulen = len(pyz_bytes)
                elif typecode == "o":
                    payload = b""
                    ulen = 0
                else:
                    fp.seek(reader._start_offset + offset)
                    payload = fp.read(data_length)
                data_offset = pkg.tell()
                pkg.write(payload)
                toc.append((data_offset, len(payload), ulen, compress, typecode, name))

        toc_offset = pkg.tell()
        toc_data = CArchiveWriter._serialize_toc(toc)
        pkg.write(toc_data)
        archive_length = toc_offset + len(toc_data) + CArchiveWriter._COOKIE_LENGTH
        pkg.write(struct.pack(
            CArchiveWriter._COOKIE_FORMAT,
            magic,
            archive_length,
            toc_offset,
            len(toc_data),
            pyvers,
            pylib.encode("ascii"),
        ))
        bootloader = exe.read_bytes()[: reader._start_offset]
        exe.write_bytes(bootloader + pkg.getvalue())
        print(f"rewrote {exe.name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def copy_templates(app_dir: Path) -> None:
    dest_root = app_dir / "_internal" / "templates"
    src_root = SRC / "templates"
    for rel in TEMPLATE_FILES:
        src = src_root / rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"copied template {rel}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if not OLD_ZIP.exists():
        raise SystemExit(f"zip not found: {OLD_ZIP}")

    work = ROOT / "_pack_new"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    print("extracting", OLD_ZIP.name)
    with zipfile.ZipFile(OLD_ZIP) as zf:
        zf.extractall(work)

    app_dir = work / "حسابداری صرافی"
    if not app_dir.is_dir():
        raise SystemExit(f"expected folder missing: {app_dir}")

    copy_templates(app_dir)
    patch_exe(app_dir / "حسابداری صرافی.exe")

    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    print("writing", OUT_ZIP.name)
    with zipfile.ZipFile(OUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in app_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(work).as_posix())

    shutil.rmtree(work, ignore_errors=True)
    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"done: {OUT_ZIP} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
