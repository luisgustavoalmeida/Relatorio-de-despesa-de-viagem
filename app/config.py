from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from app.paths import bundle_dir, data_dir, user_dir

# Pasta gravável (ao lado do .exe ou raiz do repositório)
ROOT = user_dir()
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
# Estado da aplicação (settings, registry, cache) — distinto do output/ de cada projeto
DATA_DIR = data_dir()
_LEGACY_OUTPUT_DIR = ROOT / "output"
_DATA_MIGRATED = False


def ensure_data_dir() -> Path:
    """Garante data/ e migra arquivos antigos de output/ (raiz) se existirem."""
    global _DATA_MIGRATED
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _DATA_MIGRATED:
        _DATA_MIGRATED = True
        for name in ("user_settings.json", "projetos_registry.json", "cnpj_cache.json"):
            dest = DATA_DIR / name
            src = _LEGACY_OUTPUT_DIR / name
            if not dest.exists() and src.is_file():
                try:
                    shutil.copy2(src, dest)
                except OSError:
                    pass
    return DATA_DIR


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        bundled = bundle_dir() / "config.yaml"
        if bundled.is_file():
            cfg_path = bundled
    if not cfg_path.is_file():
        data: dict[str, Any] = {}
    else:
        try:
            with open(cfg_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            loaded = {}
        data = loaded if isinstance(loaded, dict) else {}
    data.setdefault("pastas", {})
    # Relativos a cada projetos/<slug>/ (não à raiz do repositório)
    data["pastas"].setdefault("notas", "notas")
    data["pastas"].setdefault("saida", "output")
    data["pastas"].setdefault("excluidas", "notas/excluidas")
    data.setdefault("moeda", "BRL")
    data.setdefault("confianca_minima", 0.7)
    data.setdefault(
        "categorias",
        [
            "Café da manhã",
            "Alimentação",
            "Frigobar",
            "Lavanderia",
            "Estacionamento / garagem",
            "Táxi / aplicativo",
            "Pedágio",
            "Bagagem",
            "Locação de veículo",
            "Combustível",
            "Hospedagem",
            "Passagem",
            "Outros",
        ],
    )
    data.setdefault("extensoes_imagem", [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"])
    data.setdefault("extensoes_pdf", [".pdf"])
    data.setdefault("ocr", {"motor": "local"})
    data.setdefault("pdf", {"detectar_multiplas_na_pagina": True, "escala_render": 2.0})
    data.setdefault("visao", {"provedor": "auto", "modelo_openai": "gpt-4o-mini", "modelo_gemini": "automatico"})
    return data


def resolve_path(relative: str, base: Path | None = None) -> Path:
    base = base or ROOT
    p = Path(relative)
    if p.is_absolute():
        return p
    return (base / p).resolve()
