"""Compila o aplicativo em pasta distribuível (PyInstaller onedir)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = Path(__file__).resolve().parent
DIST_NAME = "RelatorioDespesaViagem"
DIST_DIR = ROOT / "dist" / DIST_NAME
ZIP_PATH = ROOT / "dist" / f"{DIST_NAME}-Windows"


def _python() -> Path:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return venv
    return Path(sys.executable)


def _ocultar(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    subprocess.run(["attrib", "+H", str(path)], check=False, capture_output=True)


def _preparar_dist() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGING / "LEIA-ME.txt", DIST_DIR / "LEIA-ME.txt")
    (DIST_DIR / "projetos").mkdir(parents=True, exist_ok=True)

    # Oculta o motor do PyInstaller; o app oculta data/assets no primeiro uso
    _ocultar(DIST_DIR / "_internal")

    readme_projetos = DIST_DIR / "projetos" / "COLOQUE_AS_NOTAS_AQUI.txt"
    if not readme_projetos.exists():
        readme_projetos.write_text(
            "Esta pasta é criada automaticamente pelo aplicativo.\n"
            "Prefira criar o projeto pela interface e usar «Abrir pasta de notas».\n",
            encoding="utf-8",
        )


def main() -> int:
    py = _python()
    print(f"Python: {py}")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-build.txt")],
        cwd=str(ROOT),
    )
    spec = PACKAGING / "RelatorioDespesaViagem.spec"
    subprocess.check_call(
        [
            str(py),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(spec),
        ],
        cwd=str(ROOT),
    )
    _preparar_dist()

    if ZIP_PATH.with_suffix(".zip").exists():
        ZIP_PATH.with_suffix(".zip").unlink()
    shutil.make_archive(str(ZIP_PATH), "zip", root_dir=DIST_DIR.parent, base_dir=DIST_NAME)
    print()
    print("Pronto para distribuir:")
    print(f"  Pasta: {DIST_DIR}")
    print(f"  ZIP:   {ZIP_PATH.with_suffix('.zip')}")
    print("Envie a pasta inteira (ou o ZIP). Não envie só o .exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
