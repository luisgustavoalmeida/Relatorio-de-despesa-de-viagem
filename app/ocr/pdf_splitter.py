from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


@dataclass
class SplitPiece:
    """Uma nota separada a partir de um PDF."""

    path: Path
    pagina: int  # 1-based
    parte: int  # 1-based dentro da página
    total_paginas: int
    total_partes_pagina: int


def render_pdf_pages(pdf_path: Path, scale: float = 2.0) -> list[Image.Image]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pages: list[Image.Image] = []
        for i in range(len(pdf)):
            page = pdf[i]
            pil = page.render(scale=scale).to_pil()
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            pages.append(pil)
        return pages
    finally:
        pdf.close()


def _find_gap_splits(
    projection: np.ndarray,
    min_gap_ratio: float,
    min_seg_ratio: float,
    min_gap_px: int = 20,
) -> list[tuple[int, int]]:
    """Divide um eixo em segmentos de conteúdo separados por faixas em branco."""
    length = len(projection)
    if length == 0:
        return [(0, 0)]

    threshold = max(float(projection.max()) * 0.06, 1.0)
    content = projection > threshold

    # Suaviza ruído leve, sem preencher buracos grandes
    kernel = max(3, min(11, length // 250))
    if kernel % 2 == 0:
        kernel += 1
    padded = np.pad(content.astype(np.uint8), kernel // 2, mode="edge")
    smooth = np.convolve(padded, np.ones(kernel, dtype=np.float32) / kernel, mode="valid") > 0.4

    segments: list[tuple[int, int]] = []
    i = 0
    while i < length:
        while i < length and not smooth[i]:
            i += 1
        if i >= length:
            break
        start = i
        while i < length and smooth[i]:
            i += 1
        end = i
        if (end - start) / length >= min_seg_ratio:
            segments.append((start, end))

    if len(segments) <= 1:
        return [(0, length)]

    kept = [segments[0]]
    for seg in segments[1:]:
        gap = seg[0] - kept[-1][1]
        if gap >= min_gap_px and gap / length >= min_gap_ratio:
            kept.append(seg)
        else:
            kept[-1] = (kept[-1][0], seg[1])

    if len(kept) <= 1:
        return [(0, length)]
    return kept


def detect_receipt_regions(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Detecta uma ou mais regiões de notinha na página.

    Retorna lista de boxes (left, top, right, bottom).
    Preferência: notas empilhadas (separação horizontal); se não houver,
    tenta lado a lado (vertical).
    """
    arr = np.array(img.convert("L"))
    h, w = arr.shape

    # Conteúdo escuro em fundo claro (recibo típico)
    ink = 255 - arr
    row_proj = ink.mean(axis=1)
    col_proj = ink.mean(axis=0)

    horizontal = _find_gap_splits(row_proj, min_gap_ratio=0.035, min_seg_ratio=0.12, min_gap_px=24)
    boxes: list[tuple[int, int, int, int]] = []

    def _box_from_y(y0: int, y1: int) -> tuple[int, int, int, int] | None:
        pad = max(4, int((y1 - y0) * 0.02))
        top = max(0, y0 - pad)
        bottom = min(h, y1 + pad)
        band = ink[top:bottom, :]
        col_b = band.mean(axis=0)
        thr = max(float(col_b.max()) * 0.1, 1.0)
        xs = np.where(col_b > thr)[0]
        if len(xs) == 0:
            left, right = 0, w
        else:
            left = max(0, int(xs[0]) - 8)
            right = min(w, int(xs[-1]) + 8)
        if (bottom - top) < 0.12 * h:
            return None
        if (bottom - top) * (right - left) < 0.08 * h * w:
            return None
        return (left, top, right, bottom)

    if len(horizontal) > 1:
        for y0, y1 in horizontal:
            box = _box_from_y(y0, y1)
            if box:
                boxes.append(box)
        # Se sobrou só 1 região válida, ou nenhuma, usa página inteira
        if len(boxes) <= 1:
            boxes = [(0, 0, w, h)]
    else:
        vertical = _find_gap_splits(col_proj, min_gap_ratio=0.04, min_seg_ratio=0.15, min_gap_px=24)
        if len(vertical) > 1:
            for x0, x1 in vertical:
                pad = max(4, int((x1 - x0) * 0.02))
                left = max(0, x0 - pad)
                right = min(w, x1 + pad)
                band = ink[:, left:right]
                row_b = band.mean(axis=1)
                thr = max(float(row_b.max()) * 0.1, 1.0)
                ys = np.where(row_b > thr)[0]
                if len(ys) == 0:
                    top, bottom = 0, h
                else:
                    top = max(0, int(ys[0]) - 8)
                    bottom = min(h, int(ys[-1]) + 8)
                if (bottom - top) * (right - left) >= 0.08 * h * w and (right - left) >= 0.12 * w:
                    boxes.append((left, top, right, bottom))

    if len(boxes) <= 1:
        return [(0, 0, w, h)]

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def split_pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    *,
    detectar_multiplas_na_pagina: bool = True,
    scale: float = 2.0,
) -> list[SplitPiece]:
    """Separa um PDF em imagens de notas individuais.

    - Cada página vira ao menos uma nota
    - Se houver várias notinhas na mesma página, tenta recortar cada uma
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    pieces: list[SplitPiece] = []
    try:
        total_paginas = len(pdf)
        for page_idx in range(1, total_paginas + 1):
            page = pdf[page_idx - 1]
            page_img = page.render(scale=scale).to_pil()
            if page_img.mode != "RGB":
                page_img = page_img.convert("RGB")
            if detectar_multiplas_na_pagina:
                regions = detect_receipt_regions(page_img)
            else:
                regions = [(0, 0, page_img.width, page_img.height)]

            total_partes = len(regions)
            for parte_idx, (left, top, right, bottom) in enumerate(regions, start=1):
                crop = page_img.crop((left, top, right, bottom))
                if total_partes == 1:
                    name = f"p{page_idx:02d}.png"
                else:
                    name = f"p{page_idx:02d}_n{parte_idx:02d}.png"
                out_path = output_dir / name
                crop.save(out_path, format="PNG", optimize=True)
                pieces.append(
                    SplitPiece(
                        path=out_path,
                        pagina=page_idx,
                        parte=parte_idx,
                        total_paginas=total_paginas,
                        total_partes_pagina=total_partes,
                    )
                )
            del page_img
    finally:
        pdf.close()

    # Marcador para não reprocessar sem necessidade
    marker = output_dir / ".split_ok"
    marker.write_text(f"{pdf_path.resolve()}\npages={total_paginas}\npieces={len(pieces)}\n", encoding="utf-8")
    return pieces


def pdf_ja_separado(output_dir: Path, pdf_path: Path) -> bool:
    marker = Path(output_dir) / ".split_ok"
    if not marker.exists():
        return False
    try:
        first = marker.read_text(encoding="utf-8").splitlines()[0].strip()
        if first != str(Path(pdf_path).resolve()):
            return False
        # Reprocessa se o PDF foi alterado depois da separação
        if Path(pdf_path).stat().st_mtime > marker.stat().st_mtime:
            return False
        # Precisa ter ao menos uma imagem gerada
        if not any(Path(output_dir).glob("p*.png")):
            return False
        return True
    except Exception:
        return False
