"""Detecção profissional de notas duplicadas por fingerprint de conteúdo.

Não usa hash da imagem: fotos diferentes do mesmo cupom geram o mesmo
fingerprint quando data + hora + valor + estabelecimento/CNPJ coincidem.

Sem hora → não marca duplicata (evita falso positivo no mesmo dia/local/valor).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Iterable, Optional, Protocol

from app.ocr.cnpj_lookup import cnpj_somente_digitos

# Sufixos jurídicos / ruído comum em OCR de razão social
_SUFIXOS_EMPRESA = re.compile(
    r"\b("
    r"ltda|l\.?t\.?d\.?a\.?|me|eireli|e\.?i\.?r\.?e\.?l\.?i\.?|"
    r"s\.?a\.?|sa|ss|epp|epp?|mei|"
    r"comercio|com\.?|industria|ind\.?|"
    r"servicos|servicos?|serv\.?"
    r")\b",
    re.IGNORECASE,
)
_NAO_ALNUM = re.compile(r"[^a-z0-9\s]+")
_ESPACOS = re.compile(r"\s+")
_PREFIXO_CNPJ_NOME = re.compile(r"^cnpj\s*\d+\s*", re.IGNORECASE)


class _ReceiptLike(Protocol):
    id: str
    valor: Optional[float]
    data: Optional[str]
    hora: str
    estabelecimento: str
    cnpj: str
    status: object
    hash_conteudo: str


def _fold(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().strip()


def normalizar_estabelecimento(nome: str | None) -> str:
    """Nome canônico para comparação (acentos, pontuação e sufixos jurídicos)."""
    t = _fold(nome or "")
    t = _PREFIXO_CNPJ_NOME.sub("", t)
    t = _SUFIXOS_EMPRESA.sub(" ", t)
    t = _NAO_ALNUM.sub(" ", t)
    t = _ESPACOS.sub(" ", t).strip()
    return t


def normalizar_valor_centavos(valor: float | int | None) -> int | None:
    if valor is None:
        return None
    try:
        return int(round(float(valor) * 100))
    except (TypeError, ValueError):
        return None


def normalizar_hora(hora: str | None) -> str | None:
    """Aceita HH:MM (com ou sem zero à esquerda na hora)."""
    if not hora:
        return None
    t = str(hora).strip()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$", t)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def chave_comercio(estabelecimento: str | None, cnpj: str | None) -> str | None:
    """Identidade do estabelecimento: CNPJ (preferido) ou nome normalizado."""
    digits = cnpj_somente_digitos(cnpj)
    if digits and len(digits) == 14:
        return f"cnpj:{digits}"
    nome = normalizar_estabelecimento(estabelecimento)
    if len(nome) >= 3:
        return f"nome:{nome}"
    return None


def montar_payload_fingerprint(
    *,
    data: str | None,
    valor: float | None,
    hora: str | None = None,
    estabelecimento: str | None = None,
    cnpj: str | None = None,
) -> str | None:
    """String canônica data|hora|centavos|comercio — None se incompleta (exige hora)."""
    if not data or not str(data).strip():
        return None
    hora_n = normalizar_hora(hora)
    if not hora_n:
        return None
    centavos = normalizar_valor_centavos(valor)
    if centavos is None:
        return None
    comercio = chave_comercio(estabelecimento, cnpj)
    if not comercio:
        return None
    return f"{str(data).strip()}|{hora_n}|{centavos}|{comercio}"


def hash_conteudo_de_campos(
    *,
    data: str | None,
    valor: float | None,
    hora: str | None = None,
    estabelecimento: str | None = None,
    cnpj: str | None = None,
) -> str:
    """SHA-256 truncado do fingerprint (data+hora+valor+comercio)."""
    payload = montar_payload_fingerprint(
        data=data,
        valor=valor,
        hora=hora,
        estabelecimento=estabelecimento,
        cnpj=cnpj,
    )
    if not payload:
        return ""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:16]


def hash_conteudo_receipt(r: _ReceiptLike) -> str:
    return hash_conteudo_de_campos(
        data=r.data,
        valor=r.valor,
        hora=getattr(r, "hora", "") or "",
        estabelecimento=getattr(r, "estabelecimento", "") or "",
        cnpj=getattr(r, "cnpj", "") or "",
    )


def atualizar_hash_conteudo(r: _ReceiptLike) -> str:
    """Recalcula e grava ``hash_conteudo`` na nota; devolve o valor."""
    h = hash_conteudo_receipt(r)
    r.hash_conteudo = h
    return h


def _status_excluida(r: _ReceiptLike) -> bool:
    st = getattr(r, "status", None)
    if st is None:
        return False
    valor = getattr(st, "value", st)
    return str(valor).lower() == "excluida"


def indice_duplicatas(
    receipts: Iterable[_ReceiptLike],
    *,
    incluir_excluidas: bool = False,
) -> dict[str, list[str]]:
    """Agrupa IDs por fingerprint. Só retorna grupos com 2+ notas."""
    por_hash: dict[str, list[str]] = defaultdict(list)
    for r in receipts:
        if not incluir_excluidas and _status_excluida(r):
            continue
        h = atualizar_hash_conteudo(r)
        if not h:
            continue
        por_hash[h].append(r.id)
    return {h: ids for h, ids in por_hash.items() if len(ids) >= 2}


def ids_duplicados(
    receipts: Iterable[_ReceiptLike],
    *,
    incluir_excluidas: bool = False,
) -> set[str]:
    """Conjunto de IDs que fazem parte de algum grupo duplicado."""
    ids: set[str] = set()
    for grupo in indice_duplicatas(receipts, incluir_excluidas=incluir_excluidas).values():
        ids.update(grupo)
    return ids


def _indice_adicao(receipts: list[_ReceiptLike]) -> dict[str, int]:
    """Posição na lista do projeto (notas novas entram no final ao sincronizar)."""
    return {r.id: i for i, r in enumerate(receipts)}


def escolher_duplicata_recente(
    grupo_ids: list[str],
    *,
    ordem_adicao: dict[str, int],
    por_id: dict[str, _ReceiptLike],
) -> str:
    """
    Entre as notas do grupo, devolve a adicionada por último.

    Critério: maior índice em ``trip.receipts`` (sync sempre dá append).
    Empate: ``atualizado_em`` mais recente.
    """

    def chave(rid: str) -> tuple:
        r = por_id.get(rid)
        atualizado = getattr(r, "atualizado_em", "") or "" if r else ""
        return (ordem_adicao.get(rid, -1), atualizado)

    return max(grupo_ids, key=chave)


def ids_marcacao_duplicata(
    receipts: Iterable[_ReceiptLike],
    *,
    incluir_excluidas: bool = False,
) -> set[str]:
    """
    IDs a destacar em roxo: só a nota mais recente de cada grupo duplicado
    (a que foi adicionada por último ao projeto).
    """
    lista = list(receipts)
    ordem = _indice_adicao(lista)
    por_id = {r.id: r for r in lista}
    marcados: set[str] = set()
    for grupo in indice_duplicatas(lista, incluir_excluidas=incluir_excluidas).values():
        marcados.add(
            escolher_duplicata_recente(grupo, ordem_adicao=ordem, por_id=por_id)
        )
    return marcados


def pares_duplicados(
    receipts: Iterable[_ReceiptLike],
    *,
    incluir_excluidas: bool = False,
) -> list[list[_ReceiptLike]]:
    """Listas de notas (objetos) agrupadas por fingerprint duplicado."""
    lista = [r for r in receipts if incluir_excluidas or not _status_excluida(r)]
    por_id = {r.id: r for r in lista}
    grupos: list[list[_ReceiptLike]] = []
    for ids in indice_duplicatas(lista, incluir_excluidas=True).values():
        grupo = [por_id[i] for i in ids if i in por_id]
        if len(grupo) >= 2:
            grupos.append(grupo)
    return grupos


def motivo_duplicata(_r: _ReceiptLike) -> str:
    """Texto curto do critério (para UI)."""
    return "mesma data, hora, valor e estabelecimento (adicionada por último)"


def resumo_duplicatas(receipts: Iterable[_ReceiptLike]) -> str:
    """Texto curto para alerta na UI."""
    lista = list(receipts)
    grupos = indice_duplicatas(lista)
    if not grupos:
        return ""
    n_marcadas = len(ids_marcacao_duplicata(lista))
    n_grupos = len(grupos)
    return f"{n_marcadas} duplicata(s) recentes em {n_grupos} grupo(s)"
