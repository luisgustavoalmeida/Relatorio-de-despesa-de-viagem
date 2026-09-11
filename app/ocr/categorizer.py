from __future__ import annotations

import unicodedata
from typing import Iterable

# Mapeamento de palavras-chave (minúsculas, sem acento preferencial) -> categoria canônica
_KEYWORD_MAP: list[tuple[str, str]] = [
    # Combustível antes de transporte genérico (evita falso positivo em cupom de posto)
    ("combustivel", "Combustível"),
    ("gasolina", "Combustível"),
    ("etanol", "Combustível"),
    ("diesel", "Combustível"),
    ("abastec", "Combustível"),
    ("posto e serv", "Combustível"),
    ("posto ", "Combustível"),
    ("shell", "Combustível"),
    ("ipiranga", "Combustível"),
    ("bico", "Combustível"),
    ("cafe da manha", "Café da manhã"),
    ("breakfast", "Café da manhã"),
    ("padaria", "Café da manhã"),
    ("restaurante", "Alimentação"),
    ("almoco", "Alimentação"),
    ("jantar", "Alimentação"),
    ("lanche", "Alimentação"),
    ("hamburguer", "Alimentação"),
    ("pizzaria", "Alimentação"),
    ("food", "Alimentação"),
    ("frigobar", "Frigobar"),
    ("minibar", "Frigobar"),
    ("lavanderia", "Lavanderia"),
    ("laundry", "Lavanderia"),
    ("estacionamento", "Estacionamento / garagem"),
    ("garagem", "Estacionamento / garagem"),
    ("parking", "Estacionamento / garagem"),
    ("uber", "Táxi / aplicativo"),
    ("99app", "Táxi / aplicativo"),
    ("cabify", "Táxi / aplicativo"),
    ("taxi", "Táxi / aplicativo"),
    ("pedagio", "Pedágio"),
    ("sem parar", "Pedágio"),
    ("bagagem", "Bagagem"),
    ("excesso de bagagem", "Bagagem"),
    ("locacao", "Locação de veículo"),
    ("aluguel de carro", "Locação de veículo"),
    ("rent a car", "Locação de veículo"),
    ("localiza", "Locação de veículo"),
    ("movida", "Locação de veículo"),
    ("hotel", "Hospedagem"),
    ("pousada", "Hospedagem"),
    ("hospedagem", "Hospedagem"),
    ("airbnb", "Hospedagem"),
    ("passagem aerea", "Passagem"),
    ("passagem", "Passagem"),
    ("aereo", "Passagem"),
    ("gol linhas", "Passagem"),
    ("latam", "Passagem"),
]


def _fold(text: str) -> str:
    """Normaliza para comparação (minúsculas, sem acentos)."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().strip()


def normalize_categoria(categoria: str | None, categorias: Iterable[str]) -> str:
    cats = list(categorias)
    if not categoria:
        return "Outros" if "Outros" in cats else (cats[-1] if cats else "Outros")
    folded = _fold(categoria)
    for cat in cats:
        if folded == _fold(cat):
            return cat
    for cat in cats:
        cf = _fold(cat)
        if folded in cf or cf in folded:
            return cat
    return "Outros" if "Outros" in cats else (cats[-1] if cats else "Outros")


def infer_categoria_from_text(texto: str, categorias: Iterable[str]) -> str | None:
    if not texto:
        return None
    folded = _fold(texto)
    for keyword, cat in _KEYWORD_MAP:
        if _fold(keyword) in folded:
            return normalize_categoria(cat, categorias)
    return None


def slugify_filename(text: str, max_len: int = 40) -> str:
    keep = []
    for ch in text.strip():
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    s = "".join(keep).strip("_")
    return s[:max_len] or "comprovante"
