"""Ponto de entrada do Gerador de Relatórios de Despesa de Viagem."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    if getattr(sys, "frozen", False):
        return
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_bootstrap_sys_path()


def _instalar_log_erros() -> None:
    """No .exe, grava exceções não tratadas em data/erro.log."""
    from app.paths import data_dir, is_frozen

    if not is_frozen():
        return

    def _hook(exc_type, exc, tb) -> None:
        import traceback

        try:
            log = data_dir() / "erro.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                "".join(traceback.format_exception(exc_type, exc, tb)),
                encoding="utf-8",
            )
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def main() -> None:
    from app.config import ensure_data_dir
    from app.gui import run
    from app.paths import prepare_runtime

    prepare_runtime()
    _instalar_log_erros()
    ensure_data_dir()
    run()


if __name__ == "__main__":
    main()
