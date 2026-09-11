"""Categorias extras criadas pelo usuário (regiões Diversas / Outros do RDV)."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.atomic_io import atomic_write_json
from app.config import load_config
from app.paths import user_asset

PASTA_RDV = user_asset("rdv")
ARQUIVO_CATEGORIAS_CUSTOM = PASTA_RDV / "categorias_customizadas.json"

COLUNA_ROTULO_PADRAO = "D"
COLUNA_VALOR_PADRAO = "G"

# Preferir linhas vazias; depois as de exemplo do template
REGIOES: dict[str, dict[str, Any]] = {
    "diversas": {
        "rotulo": "Diversas",
        "linhas": [37, 38, 39, 40, 41, 42, 35, 36],
    },
    "outros": {
        "rotulo": "Outros",
        "linhas": [44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56],
    },
}

# Compatibilidade com código antigo
LINHAS_DIVERSAS_PADRAO: list[int] = list(REGIOES["diversas"]["linhas"])


def _padrao_arquivo() -> dict[str, Any]:
    return {
        "_comentario": (
            "Categorias criadas pelo usuário. Escolha a região Diversas ou Outros; "
            "cada categoria usa uma linha (rótulo na coluna D, valor na coluna G)."
        ),
        "coluna_rotulo": COLUNA_ROTULO_PADRAO,
        "coluna_valor": COLUNA_VALOR_PADRAO,
        "regioes": {
            chave: {"linhas": list(info["linhas"])}
            for chave, info in REGIOES.items()
        },
        # legado (mantido para leitura antiga)
        "linhas_disponiveis": list(REGIOES["diversas"]["linhas"]),
        "categorias": [],
    }


def _normalizar_dados(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("coluna_rotulo", COLUNA_ROTULO_PADRAO)
    data.setdefault("coluna_valor", COLUNA_VALOR_PADRAO)
    data.setdefault("categorias", [])

    regioes = data.get("regioes")
    if not isinstance(regioes, dict):
        regioes = {}
    for chave, info in REGIOES.items():
        bloco = regioes.get(chave) if isinstance(regioes.get(chave), dict) else {}
        linhas = bloco.get("linhas") if isinstance(bloco, dict) else None
        if not linhas and chave == "diversas" and data.get("linhas_disponiveis"):
            linhas = data.get("linhas_disponiveis")
        regioes[chave] = {"linhas": list(linhas or info["linhas"])}
    data["regioes"] = regioes

    # Migra categorias antigas sem campo regiao
    linhas_diversas = set(int(x) for x in regioes["diversas"]["linhas"])
    linhas_outros = set(int(x) for x in regioes["outros"]["linhas"])
    cats = []
    for c in data.get("categorias") or []:
        if not isinstance(c, dict):
            continue
        item = dict(c)
        if not str(item.get("regiao") or "").strip():
            try:
                linha = int(item.get("linha_excel"))
            except (TypeError, ValueError):
                linha = None
            if linha in linhas_outros:
                item["regiao"] = "outros"
            else:
                item["regiao"] = "diversas"
        cats.append(item)
    data["categorias"] = cats
    return data


@lru_cache(maxsize=1)
def load_categorias_custom() -> dict[str, Any]:
    if ARQUIVO_CATEGORIAS_CUSTOM.is_file():
        try:
            with open(ARQUIVO_CATEGORIAS_CUSTOM, encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return _normalizar_dados(data)
        except (OSError, json.JSONDecodeError):
            pass
    return _padrao_arquivo()


def reload_categorias_custom() -> dict[str, Any]:
    load_categorias_custom.cache_clear()
    return load_categorias_custom()


def salvar_categorias_custom(data: dict[str, Any]) -> Path:
    PASTA_RDV.mkdir(parents=True, exist_ok=True)
    payload = _normalizar_dados(deepcopy(data))
    payload.setdefault("_comentario", _padrao_arquivo()["_comentario"])
    atomic_write_json(ARQUIVO_CATEGORIAS_CUSTOM, payload)
    reload_categorias_custom()
    return ARQUIVO_CATEGORIAS_CUSTOM


def normalizar_regiao(regiao: str | None) -> str:
    chave = (regiao or "diversas").strip().lower()
    if chave in ("outra", "outras", "outro", "outros"):
        return "outros"
    if chave in ("diversa", "diversas"):
        return "diversas"
    if chave not in REGIOES:
        return "diversas"
    return chave


def rotulo_regiao(regiao: str | None) -> str:
    chave = normalizar_regiao(regiao)
    return str(REGIOES[chave]["rotulo"])


def linhas_da_regiao(regiao: str | None) -> list[int]:
    chave = normalizar_regiao(regiao)
    data = load_categorias_custom()
    pool = (data.get("regioes") or {}).get(chave, {}).get("linhas")
    if not pool:
        pool = REGIOES[chave]["linhas"]
    out: list[int] = []
    for linha in pool:
        try:
            out.append(int(linha))
        except (TypeError, ValueError):
            continue
    return out


def listar_custom() -> list[dict[str, Any]]:
    cats = load_categorias_custom().get("categorias") or []
    return [c for c in cats if isinstance(c, dict) and str(c.get("nome") or "").strip()]


def nomes_custom() -> list[str]:
    return [str(c["nome"]).strip() for c in listar_custom()]


def mapa_linha_custom() -> dict[str, int]:
    out: dict[str, int] = {}
    for c in listar_custom():
        nome = str(c.get("nome") or "").strip()
        try:
            linha = int(c.get("linha_excel"))
        except (TypeError, ValueError):
            continue
        if nome:
            out[nome] = linha
    return out


def mapa_rotulo_custom() -> dict[str, str]:
    """categoria -> célula do rótulo (ex.: D37)."""
    data = load_categorias_custom()
    col = str(data.get("coluna_rotulo") or COLUNA_ROTULO_PADRAO).strip() or "D"
    out: dict[str, str] = {}
    for c in listar_custom():
        nome = str(c.get("nome") or "").strip()
        try:
            linha = int(c.get("linha_excel"))
        except (TypeError, ValueError):
            continue
        if nome:
            cel = str(c.get("celula_rotulo") or "").strip()
            out[nome] = cel or f"{col}{linha}"
    return out


def categorias_base(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config if config is not None else load_config()
    cats = list(cfg.get("categorias") or [])
    if not cats:
        cats = ["Outros"]
    return cats


def categorias_completas(config: dict[str, Any] | None = None) -> list[str]:
    """Lista para o combo: padrões + custom (Outros sempre no fim)."""
    base = [c for c in categorias_base(config) if c != "Outros"]
    extras = [n for n in nomes_custom() if n not in base and n != "Outros"]
    resultado = base + extras
    if "Outros" not in resultado:
        resultado.append("Outros")
    else:
        resultado = [c for c in resultado if c != "Outros"] + ["Outros"]
    return resultado


def linhas_ocupadas(regiao: str | None = None) -> set[int]:
    ocupadas: set[int] = set()
    chave = normalizar_regiao(regiao) if regiao else None
    for c in listar_custom():
        if chave and normalizar_regiao(c.get("regiao")) != chave:
            continue
        try:
            ocupadas.add(int(c.get("linha_excel")))
        except (TypeError, ValueError):
            continue
    return ocupadas


def proxima_linha_livre(regiao: str | None = "diversas") -> int | None:
    chave = normalizar_regiao(regiao)
    ocupadas = linhas_ocupadas()  # global: linha não pode ser reutilizada
    for linha in linhas_da_regiao(chave):
        if linha not in ocupadas:
            return linha
    return None


def resumo_vagas(regiao: str | None = None) -> dict[str, Any]:
    chave = normalizar_regiao(regiao) if regiao else None
    if chave:
        regioes = [chave]
    else:
        regioes = list(REGIOES.keys())
    out: dict[str, Any] = {}
    for r in regioes:
        pool = linhas_da_regiao(r)
        usadas = len([c for c in listar_custom() if normalizar_regiao(c.get("regiao")) == r])
        livre = proxima_linha_livre(r)
        out[r] = {
            "rotulo": rotulo_regiao(r),
            "usadas": usadas,
            "total": len(pool),
            "proxima": livre,
        }
    return out


def adicionar_categoria(nome: str, regiao: str = "diversas") -> dict[str, Any]:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome da categoria.")
    if nome == "Outros":
        raise ValueError("«Outros» já existe como categoria padrão.")

    chave = normalizar_regiao(regiao)
    base = set(categorias_base())
    if nome in base:
        raise ValueError(f"«{nome}» já existe nas categorias padrão.")

    data = deepcopy(load_categorias_custom())
    atuais = [c for c in (data.get("categorias") or []) if isinstance(c, dict)]
    for c in atuais:
        if str(c.get("nome") or "").strip().casefold() == nome.casefold():
            raise ValueError(f"«{nome}» já está cadastrada.")

    linha = proxima_linha_livre(chave)
    if linha is None:
        raise ValueError(
            f"Não há mais linhas livres na região «{rotulo_regiao(chave)}» do Excel "
            f"(máximo {len(linhas_da_regiao(chave))} categorias).\n"
            "Remova uma categoria dessa região, escolha a outra região ou edite o modelo RDV."
        )

    col = str(data.get("coluna_rotulo") or "D").strip() or "D"
    item = {
        "nome": nome,
        "regiao": chave,
        "linha_excel": linha,
        "celula_rotulo": f"{col}{linha}",
    }
    atuais.append(item)
    data["categorias"] = atuais
    salvar_categorias_custom(data)
    return item


def remover_categoria(nome: str) -> None:
    nome = (nome or "").strip()
    data = deepcopy(load_categorias_custom())
    atuais = [
        c
        for c in (data.get("categorias") or [])
        if isinstance(c, dict) and str(c.get("nome") or "").strip().casefold() != nome.casefold()
    ]
    data["categorias"] = atuais
    salvar_categorias_custom(data)


def mesclar_linhas_excel(linhas_base: dict[str, int] | None) -> dict[str, int]:
    """Junta mapeamento padrão com categorias custom para o export Excel."""
    out: dict[str, int] = dict(linhas_base or {})
    out.update(mapa_linha_custom())
    return out
