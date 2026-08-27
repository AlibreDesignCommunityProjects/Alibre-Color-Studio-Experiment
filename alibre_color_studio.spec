# PyInstaller spec for Alibre Color Studio.
#   .venv/Scripts/pyinstaller.exe alibre_color_studio.spec
#
# AlibreX.dll is deliberately NOT bundled: alibrex locates it at runtime from
# the registry or Program Files, and the license does not permit redistributing
# it. The build therefore still requires Alibre Design installed on the target
# machine -- which it does anyway, since the app drives a running instance.

block_cipher = None

a = Analysis(
    ["run_color_studio.py"],
    pathex=[],
    binaries=[],
    datas=[],
    # pythonnet resolves the CLR through clr_loader at import time; its
    # runtime shims are loaded dynamically, so PyInstaller cannot see them.
    hiddenimports=[
        "clr",
        "clr_loader",
        "clr_loader.netfx",
        "clr_loader.util",
        "pythonnet",
        "alibrex",
        "alibrex._com_bridge",
        "alibrex._discover",
        "alibrex._helpers",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AlibreColorStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="AlibreColorStudio",
)
