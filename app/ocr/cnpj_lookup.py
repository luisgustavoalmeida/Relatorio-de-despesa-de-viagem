"""Consulta pública de CNPJ → razão social / nome fantasia (com redundância)."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from app.atomic_io import atomic_write_json
from app.config import DATA_DIR, ensure_data_dir

CACHE_PATH = DATA_DIR / "cnpj_cache.json"
_CACHE_LOCK = threading.RLock()

# Ordem de tentativa (sem autenticação)
_API_ORDER = ("brasilapi", "minhareceita", "receitaws")


def cnpj_somente_digitos(valor: str | None) -> str | None:
    if not valor:
        return None
    digits = re.sub(r"\D", "", str(valor))
    if len(digits) != 14:
        return None
    if digits == "0" * 14:
        return None
    return digits


def formatar_cnpj(digits: str) -> str:
    d = cnpj_somente_digitos(digits) or digits
    if len(d) != 14:
        return d
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _load_cache() -> dict[str, Any]:
    ensure_data_dir()
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        ensure_data_dir()
        atomic_write_json(CACHE_PATH, cache)
    except Exception:
        pass


def _nome_de_payload(dados: dict[str, Any]) -> str | None:
    """Preferência: nome fantasia útil → razão social."""
    fantasia = (dados.get("nome_fantasia") or dados.get("fantasia") or "").strip()
    razao = (dados.get("razao_social") or dados.get("nome") or "").strip()
    # Fantasia genérica / vazia / igual à razão
    if fantasia and fantasia.upper() not in {"", "*", "NULL", "NONE"} and len(fantasia) >= 3:
        if fantasia.upper() != razao.upper():
            return fantasia[:80]
        return fantasia[:80]
    if razao and len(razao) >= 3:
        return razao[:80]
    return None


def _get_json(url: str, timeout: float = 10.0) -> dict[str, Any] | None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RelatorioDespesaViagem/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _lookup_brasilapi(cnpj: str) -> tuple[str | None, str]:
    data = _get_json(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
    if not data:
        return None, "brasilapi"
    nome = _nome_de_payload(data)
    return nome, "brasilapi"


def _lookup_minhareceita(cnpj: str) -> tuple[str | None, str]:
    data = _get_json(f"https://minhareceita.org/{cnpj}")
    if not data:
        return None, "minhareceita"
    nome = _nome_de_payload(data)
    return nome, "minhareceita"


def _lookup_receitaws(cnpj: str) -> tuple[str | None, str]:
    # Limite gratuito baixo — só como última opção
    data = _get_json(f"https://www.receitaws.com.br/v1/cnpj/{cnpj}", timeout=12.0)
    if not data:
        return None, "receitaws"
    if str(data.get("status", "")).upper() == "ERROR":
        return None, "receitaws"
    nome = _nome_de_payload(data)
    return nome, "receitaws"


_LOOKUPERS = {
    "brasilapi": _lookup_brasilapi,
    "minhareceita": _lookup_minhareceita,
    "receitaws": _lookup_receitaws,
}


def consultar_nome_por_cnpj(
    cnpj: str | None,
    *,
    use_cache: bool = True,
    apis: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Consulta CNPJ com redundância entre APIs públicas.

    Retorna dict com nome, fonte, cnpj — ou None se todas falharem.
    """
    digits = cnpj_somente_digitos(cnpj)
    if not digits:
        return None

    with _CACHE_LOCK:
        cache = _load_cache() if use_cache else {}
        cached = cache.get(digits)
        if isinstance(cached, dict) and cached.get("nome"):
            return {
                "nome": cached["nome"],
                "fonte": cached.get("fonte", "cache"),
                "cnpj": formatar_cnpj(digits),
                "cnpj_digits": digits,
                "cache": True,
            }

    order = apis or _API_ORDER
    last_err_fonte = ""
    for api_id in order:
        fn = _LOOKUPERS.get(api_id)
        if not fn:
            continue
        try:
            nome, fonte = fn(digits)
        except Exception:
            last_err_fonte = api_id
            continue
        if nome:
            result = {
                "nome": nome.strip()[:80],
                "fonte": fonte,
                "cnpj": formatar_cnpj(digits),
                "cnpj_digits": digits,
                "cache": False,
            }
            if use_cache:
                with _CACHE_LOCK:
                    cache = _load_cache()
                    cache[digits] = {"nome": result["nome"], "fonte": fonte, "ts": time.time()}
                    _save_cache(cache)
            return result
        last_err_fonte = api_id
        time.sleep(0.15)

    return None if not last_err_fonte else None
