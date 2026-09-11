from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from app.config import ROOT, load_config, resolve_path
from app.models.receipt import Receipt, ReceiptStatus
from app.models.trip import Trip
from app.ocr.pdf_splitter import pdf_ja_separado, split_pdf_to_images


def save_trip(trip: Trip) -> Path:
    """Persiste o projeto na pasta do projeto (projetos/<slug>/projeto.json)."""
    if not trip.pasta_raiz:
        raise ValueError("Projeto sem pasta_raiz — crie ou abra um projeto antes de salvar.")
    trip.atualizado_em = datetime.now().isoformat(timespec="seconds")
    from app.projects import salvar_projeto

    return salvar_projeto(trip)


def _to_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


_SKIP_DIRS_NOTAS = {"excluidas", "_pre_recorte"}


def _walk_files(pasta: Path, skip_dirs: set[str]) -> list[Path]:
    """Lista ficheiros sem descer a pastas internas irrelevantes."""
    if not pasta.exists():
        return []
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(pasta):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            files.append(Path(dirpath) / name)
    files.sort()
    return files


def list_receipt_files(pasta_notas: str | Path, config: dict | None = None) -> list[Path]:
    """Lista imagens elegíveis (incluindo separadas de PDF). Ignora pasta excluidas/."""
    cfg = config or load_config()
    pasta = resolve_path(str(pasta_notas))
    if not pasta.exists():
        pasta.mkdir(parents=True, exist_ok=True)
        return []

    img_exts = {e.lower() for e in cfg.get("extensoes_imagem", [])}
    files: list[Path] = []
    for p in _walk_files(pasta, _SKIP_DIRS_NOTAS):
        if p.suffix.lower() in img_exts:
            files.append(p)
    return files


def excluded_dir(config: dict | None = None, pasta_notas: str | Path | None = None) -> Path:
    cfg = config or load_config()
    if pasta_notas:
        return resolve_path(str(pasta_notas)) / "excluidas"
    rel = cfg.get("pastas", {}).get("excluidas")
    if rel:
        return resolve_path(rel)
    base = resolve_path(cfg["pastas"]["notas"])
    return base / "excluidas"


def _unique_dest(dest_dir: Path, filename: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 1
    while n < 10_000:
        candidate = dest_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
    raise OSError(f"Não foi possível criar um nome único em {dest_dir}")


def _resolve_arquivo(arquivo: str) -> Path:
    p = Path(arquivo)
    if p.is_absolute():
        return p
    return (ROOT / p).resolve()


def move_receipt_to_excluded(receipt: Receipt, config: dict | None = None, pasta_notas: str | Path | None = None) -> Path:
    """Move o arquivo da nota para a pasta excluidas/ e atualiza o receipt."""
    import shutil

    cfg = config or load_config()
    src = _resolve_arquivo(receipt.arquivo)
    dest_dir = excluded_dir(cfg, pasta_notas=pasta_notas)

    if not receipt.arquivo_original:
        receipt.arquivo_original = receipt.arquivo

    if not src.exists():
        # Arquivo já sumiu; só marca como excluída mantendo caminho
        receipt.status = ReceiptStatus.EXCLUIDA
        receipt.atualizado_em = datetime.now().isoformat(timespec="seconds")
        return src

    # Já está na pasta de excluídas?
    try:
        if "excluidas" in src.parts:
            receipt.status = ReceiptStatus.EXCLUIDA
            receipt.atualizado_em = datetime.now().isoformat(timespec="seconds")
            return src
    except Exception:
        pass

    dest = _unique_dest(dest_dir, src.name)
    shutil.move(str(src), str(dest))
    receipt.arquivo = _to_rel(dest)
    receipt.status = ReceiptStatus.EXCLUIDA
    receipt.atualizado_em = datetime.now().isoformat(timespec="seconds")
    return dest


def restore_receipt_from_excluded(
    receipt: Receipt,
    config: dict | None = None,
    pasta_notas: str | Path | None = None,
) -> Path:
    """Restaura o arquivo da pasta excluidas/ para o local original (ou notas/)."""
    import shutil

    cfg = config or load_config()
    src = _resolve_arquivo(receipt.arquivo)

    if receipt.arquivo_original:
        dest = _resolve_arquivo(receipt.arquivo_original)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.resolve() != src.resolve():
            dest = _unique_dest(dest.parent, dest.name)
    else:
        base_notas = resolve_path(str(pasta_notas or cfg["pastas"]["notas"]))
        dest = _unique_dest(base_notas, src.name)

    if src.exists() and src.resolve() != dest.resolve():
        shutil.move(str(src), str(dest))
        receipt.arquivo = _to_rel(dest)
    elif dest.exists():
        receipt.arquivo = _to_rel(dest)

    receipt.arquivo_original = ""
    receipt.status = ReceiptStatus.PENDENTE
    receipt.atualizado_em = datetime.now().isoformat(timespec="seconds")
    return _resolve_arquivo(receipt.arquivo)


def list_source_pdfs(pasta_notas: str | Path, config: dict | None = None) -> list[Path]:
    """Lista PDFs de origem na pasta de notas (ignora pasta _separados)."""
    cfg = config or load_config()
    pasta = resolve_path(str(pasta_notas))
    if not pasta.exists():
        return []
    pdf_exts = {e.lower() for e in cfg.get("extensoes_pdf", [".pdf"])}
    pdfs: list[Path] = []
    for p in _walk_files(pasta, _SKIP_DIRS_NOTAS | {"_separados"}):
        if p.suffix.lower() in pdf_exts:
            pdfs.append(p)
    return pdfs


def separated_dir_for(pdf_path: Path, pasta_notas: Path) -> Path:
    return pasta_notas / "_separados" / pdf_path.stem


def process_pdfs_in_folder(trip: Trip, config: dict | None = None) -> tuple[list[Receipt], int]:
    """Separa PDFs em notas individuais e cria receipts correspondentes."""
    cfg = config or load_config()
    pasta = resolve_path(trip.pasta_notas)
    detectar = bool(cfg.get("pdf", {}).get("detectar_multiplas_na_pagina", True))
    scale = float(cfg.get("pdf", {}).get("escala_render", 2.0))

    existentes_arquivos = {_resolve_arquivo(r.arquivo) for r in trip.receipts if r.arquivo}
    origens_ja = {_resolve_arquivo(r.origem_pdf) for r in trip.receipts if r.origem_pdf}


    novos: list[Receipt] = []
    pdfs_processados = 0

    for pdf in list_source_pdfs(pasta, cfg):
        pdf_res = pdf.resolve()
        out_dir = separated_dir_for(pdf, pasta)

        # Já processado neste projeto?
        if pdf_res in origens_ja and pdf_ja_separado(out_dir, pdf):
            continue

        # Se pasta de separados existe e está ok, só registra imagens faltantes
        if pdf_ja_separado(out_dir, pdf):
            pieces_paths = sorted(out_dir.glob("p*.png"))
            pieces_meta = None
        else:
            pieces = split_pdf_to_images(
                pdf,
                out_dir,
                detectar_multiplas_na_pagina=detectar,
                scale=scale,
            )
            pieces_paths = [p.path for p in pieces]
            pieces_meta = pieces
            pdfs_processados += 1

        for idx, img_path in enumerate(pieces_paths):
            resolved = img_path.resolve()
            if resolved in existentes_arquivos:
                continue
            pagina = None
            parte = None
            if pieces_meta is not None:
                pagina = pieces_meta[idx].pagina
                parte = pieces_meta[idx].parte
            else:
                # Recupera pXX ou pXX_nYY do nome
                stem = img_path.stem
                try:
                    if "_n" in stem:
                        p_part, n_part = stem.split("_n", 1)
                        pagina = int(p_part.lstrip("p"))
                        parte = int(n_part)
                    else:
                        pagina = int(stem.lstrip("p"))
                        parte = 1
                except ValueError:
                    pass

            obs = ""
            if pagina is not None:
                obs = f"Separado de {pdf.name} (página {pagina}"
                multi = False
                if pieces_meta is not None and pieces_meta[idx].total_partes_pagina > 1:
                    multi = True
                elif parte is not None and parte > 1:
                    multi = True
                if multi and parte is not None:
                    obs += f", nota {parte}"
                obs += ")"

            receipt = Receipt(
                id=str(uuid.uuid4()),
                arquivo=_to_rel(resolved),
                categoria="Outros",
                moeda=cfg.get("moeda", "BRL"),
                status=ReceiptStatus.PENDENTE,
                origem_pdf=_to_rel(pdf_res),
                pagina=pagina,
                parte=parte,
                observacoes=obs,
            )
            trip.receipts.append(receipt)
            novos.append(receipt)
            existentes_arquivos.add(resolved)
            origens_ja.add(pdf_res)

    return novos, pdfs_processados


def _dedupe_receipts_by_arquivo(trip: Trip) -> int:
    """Remove receipts duplicados do mesmo arquivo (mantém o mais completo)."""
    melhores: dict[Path, Receipt] = {}
    ordem: list[Receipt] = []
    removidos = 0

    def _score(r: Receipt) -> tuple:
        return (
            1 if (r.fonte_extracao and r.fonte_extracao != "falha") else 0,
            1 if r.valor is not None else 0,
            1 if r.data else 0,
            1 if r.hora else 0,
            float(r.confianca or 0),
            1 if r.status == ReceiptStatus.CONFERIDA else 0,
        )

    for r in trip.receipts:
        if not r.arquivo:
            ordem.append(r)
            continue
        try:
            key = _resolve_arquivo(r.arquivo)
        except Exception:
            ordem.append(r)
            continue
        prev = melhores.get(key)
        if prev is None:
            melhores[key] = r
            ordem.append(r)
            continue
        if _score(r) > _score(prev):
            # substitui o anterior na ordem
            try:
                idx = ordem.index(prev)
                ordem[idx] = r
            except ValueError:
                ordem.append(r)
            melhores[key] = r
        removidos += 1

    if removidos:
        # Reconstrói lista preservando ordem, sem duplicatas de path
        vistos: set[Path] = set()
        limpos: list[Receipt] = []
        for r in ordem:
            if not r.arquivo:
                limpos.append(r)
                continue
            try:
                key = _resolve_arquivo(r.arquivo)
            except Exception:
                limpos.append(r)
                continue
            if key in vistos:
                continue
            # garante o melhor para este path
            limpos.append(melhores.get(key, r))
            vistos.add(key)
        trip.receipts = limpos
    return removidos


def sync_receipts_from_folder(trip: Trip, config: dict | None = None) -> tuple[list[Receipt], list[str]]:
    """Processa PDFs (separando notas) e adiciona novas imagens da pasta."""
    cfg = config or load_config()

    _dedupe_receipts_by_arquivo(trip)

    novos_pdf, _ = process_pdfs_in_folder(trip, cfg)

    files = list_receipt_files(trip.pasta_notas, cfg)
    # Sempre resolver via ROOT — Path(arquivo).resolve() usa o CWD e
    # pode falhar, re-cadastrando notas antigas como se fossem novas.
    existentes = {_resolve_arquivo(r.arquivo) for r in trip.receipts if r.arquivo}
    novos: list[Receipt] = list(novos_pdf)

    for f in files:
        resolved = f.resolve()
        if resolved in existentes:
            continue
        # Imagens geradas de PDF já entram via process_pdfs; se sobrar alguma solta, adiciona
        receipt = Receipt(
            id=str(uuid.uuid4()),
            arquivo=_to_rel(resolved),
            categoria="Outros",
            moeda=cfg.get("moeda", "BRL"),
            status=ReceiptStatus.PENDENTE,
        )
        # Se está dentro de _separados, tenta ligar ao PDF
        if "_separados" in resolved.parts:
            try:
                # .../notas/_separados/<stem>/arquivo.png
                stem_dir = resolved.parent
                pdf_candidato = resolve_path(trip.pasta_notas) / f"{stem_dir.name}.pdf"
                if pdf_candidato.exists():
                    receipt.origem_pdf = _to_rel(pdf_candidato)
            except Exception:
                pass

        trip.receipts.append(receipt)
        novos.append(receipt)
        existentes.add(resolved)

    return novos, [r.arquivo for r in novos]


def format_brl(valor: float | None) -> str:
    if valor is None:
        return "—"
    s = f"{valor:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")
