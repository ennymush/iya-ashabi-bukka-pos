# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for the Iya Ashabi Bukka POS (Windows).

Build a distributable folder with:

    pyinstaller --clean --noconfirm IyaAshabiBukkaPOS.spec

Result:  dist\\IyaAshabiBukkaPOS\\IyaAshabiBukkaPOS.exe

Design notes
------------
* One-folder build on purpose. It starts faster than one-file and nothing is
  unpacked into %TEMP% on every launch, which antivirus tools tend to block.
* Business data is never bundled. The database, logs and backups live in
  %PROGRAMDATA%\\IyaAshabiBukkaPOS, so replacing the program folder on upgrade
  keeps every sale, product and staff account intact.
* Templates and static assets are data files. Without them the executable
  starts but every page raises TemplateNotFound.
"""
import os

ROOT = SPECPATH  # noqa: F821 - injected by PyInstaller

datas = [
    (os.path.join(ROOT, "app", "templates"), "app/templates"),
    (os.path.join(ROOT, "app", "static"), "app/static"),
]

hiddenimports = [
    "win32print",  # receipt printer + cash drawer through pywin32
    "alembic",  # flask-migrate imports it lazily
    "sqlalchemy.dialects.sqlite",
    # The price-list importer imports these inside functions, so the static
    # analysis in PyInstaller cannot see them. Without them the packaged app
    # raises "needs one extra library" for PDF and Excel uploads.
    "pypdf",
    "openpyxl",
]

excludes = [
    "tkinter",
    "unittest",
    "pytest",
    "numpy",
    "pandas",
    "matplotlib",
]

a = Analysis(  # noqa: F821 - PyInstaller globals
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IyaAshabiBukkaPOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # the POS runs in the browser; logs go to the log files
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # To brand the executable, drop a 256x256 .ico in app/static/images and
    # point this at it, e.g. icon=os.path.join(ROOT, "app", "static", "images", "pos.ico"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="IyaAshabiBukkaPOS",
)
