# -*- mode: python ; coding: utf-8 -*-
"""Empacota o Gerador de Relatórios de Despesa de Viagem (pasta onedir)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent if SPEC_DIR.name.lower() == "packaging" else SPEC_DIR

datas: list = [
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "assets" / "tema_aplicacao.json"), "assets"),
    (str(ROOT / "assets" / "modelo_cabecalho.json"), "assets"),
    (str(ROOT / "assets" / "manual.json"), "assets"),
    (str(ROOT / "assets" / "sobre.json"), "assets"),
    (str(ROOT / "assets" / "rdv" / "RDV-PADRÃO.xlsx"), "assets/rdv"),
    (str(ROOT / "assets" / "rdv" / "mapeamento_celulas.json"), "assets/rdv"),
]
binaries: list = []
hiddenimports: list = collect_submodules("app")

hiddenimports += [
    "PIL._tkinter_finder",
    "PIL.ImageTk",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "customtkinter",
    "yaml",
    "dotenv",
    "openpyxl",
    "reportlab",
    "img2pdf",
    "pypdfium2",
    "cv2",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "pillow_heif",
    "certifi",
    "requests",
]

for pkg in (
    "customtkinter",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "pypdfium2",
    "certifi",
    "pillow_heif",
    "cv2",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

for pkg in (
    "google.genai",
    "google.api_core",
    "google.auth",
    "google.protobuf",
    "httpx",
    "pydantic",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += [
    "google.genai",
    "google.genai.client",
    "google.genai.types",
]

# Remove duplicatas preservando ordem
hiddenimports = list(dict.fromkeys(hiddenimports))

block_cipher = None

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "IPython",
        "notebook",
        "jupyter",
        "matplotlib",
        "scipy",
        "pandas",
        "tkinter.test",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RelatorioDespesaViagem",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RelatorioDespesaViagem",
)
