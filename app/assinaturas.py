"""Assinaturas digitais do funcionário — reutilizáveis e ligadas ao nome."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import ROOT
from app.paths import user_asset
from app.projects import slugify

PASTA_ASSINATURAS = user_asset("assinaturas")
EXTENSOES_ASSINATURA = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def slug_funcionario(nome: str) -> str:
    return slugify(nome or "", max_len=60) or "funcionario"


def garantir_pasta_assinaturas() -> Path:
    PASTA_ASSINATURAS.mkdir(parents=True, exist_ok=True)
    return PASTA_ASSINATURAS


def caminho_absoluto(relativo: str | None) -> Path | None:
    if not relativo or not str(relativo).strip():
        return None
    p = Path(str(relativo).strip())
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p if p.is_file() else None


def caminho_relativo(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def arquivo_padrao_para_nome(nome_funcionario: str) -> Path | None:
    """Procura assinatura já salva para o nome do funcionário."""
    slug = slug_funcionario(nome_funcionario)
    if slug == "funcionario" and not (nome_funcionario or "").strip():
        return None
    pasta = garantir_pasta_assinaturas()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        candidato = pasta / f"{slug}{ext}"
        if candidato.is_file():
            return candidato
    # Fallback: qualquer arquivo cujo stem comece com o slug
    for arq in sorted(pasta.glob(f"{slug}.*")):
        if arq.suffix.lower() in EXTENSOES_ASSINATURA and arq.is_file():
            return arq
    return None


def resolver_assinatura(nome_funcionario: str, assinatura_arquivo: str | None) -> Path | None:
    """Resolve path absoluto: caminho do projeto ou arquivo padrão do nome."""
    abs_path = caminho_absoluto(assinatura_arquivo)
    if abs_path:
        return abs_path
    return arquivo_padrao_para_nome(nome_funcionario)


def salvar_assinatura_para_funcionario(origem: Path, nome_funcionario: str) -> str:
    """
    Copia a imagem para assets/assinaturas/{slug}.ext e devolve o path relativo a ROOT.
    """
    origem = Path(origem)
    if not origem.is_file():
        raise FileNotFoundError(f"Arquivo de assinatura não encontrado:\n{origem}")
    ext = origem.suffix.lower()
    if ext not in EXTENSOES_ASSINATURA:
        raise ValueError(
            "Formato não suportado. Use PNG, JPG, WEBP, GIF ou BMP."
        )
    nome = (nome_funcionario or "").strip()
    if not nome:
        raise ValueError("Informe o nome do funcionário antes de adicionar a assinatura.")

    pasta = garantir_pasta_assinaturas()
    slug = slug_funcionario(nome)
    destino = pasta / f"{slug}{ext}"

    # Remove outras extensões antigas do mesmo funcionário
    for antigo in pasta.glob(f"{slug}.*"):
        if antigo.resolve() != destino.resolve() and antigo.suffix.lower() in EXTENSOES_ASSINATURA:
            try:
                antigo.unlink()
            except OSError:
                pass

    shutil.copy2(origem, destino)
    return caminho_relativo(destino)


def remover_assinatura_arquivo(relativo: str | None) -> None:
    """Remove o arquivo se estiver dentro de assets/assinaturas (não apaga fora)."""
    path = caminho_absoluto(relativo)
    if not path:
        return
    try:
        path.relative_to(PASTA_ASSINATURAS.resolve())
    except ValueError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
