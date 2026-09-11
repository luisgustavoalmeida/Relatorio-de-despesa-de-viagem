from __future__ import annotations

import io
import shutil
from pathlib import Path

import img2pdf
from PIL import Image

from app.config import ROOT
from app.models.receipt import Receipt, ReceiptStatus
from app.models.trip import Trip
from app.ocr.categorizer import slugify_filename

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


def _resolve_arquivo(arquivo: str) -> Path:
    p = Path(arquivo)
    if p.is_absolute():
        return p
    return (ROOT / p).resolve()


def _image_to_pdf_bytes(img: Image.Image) -> bytes:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    tmp = io.BytesIO()
    img.save(tmp, format="JPEG", quality=92)
    tmp.seek(0)
    return img2pdf.convert(tmp.read())


def _carregar_imagem_trabalho(path: Path, rotacao: int = 0) -> Image.Image:
    """Abre imagem (ou 1ª página de PDF) e aplica rotação da nota."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            page = pdf[0]
            img = page.render(scale=2.0).to_pil()
        finally:
            pdf.close()
    else:
        img = Image.open(path)
        img.load()
    if rotacao:
        img = img.rotate(-rotacao, expand=True)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    return img.copy()


def caminho_para_comprovante(receipt: Receipt) -> Path:
    """Arquivo usado na saída: sempre a versão atual de trabalho.

    Após um recorte, ``receipt.arquivo`` é a imagem recortada.
    O backup em ``arquivo_pre_recorte`` (imagem completa) NUNCA é usado aqui.
    """
    path = _resolve_arquivo(receipt.arquivo)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    # Proteção: se por engano o caminho apontar para o backup, tenta achar a imagem atual
    if receipt.arquivo_pre_recorte:
        try:
            backup = _resolve_arquivo(receipt.arquivo_pre_recorte)
            if path.resolve() == backup.resolve():
                raise FileNotFoundError(
                    "Caminho da nota aponta para o backup pré-recorte; "
                    "use a imagem recortada em arquivo."
                )
        except FileNotFoundError:
            raise
        except Exception:
            pass

    # Se o registro ainda for PDF, preferir imagem irmã (página separada / recortada)
    if path.suffix.lower() == ".pdf":
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            alt = path.with_suffix(ext)
            if alt.exists():
                return alt
    return path


def receipt_to_pdf_bytes(receipt: Receipt) -> bytes:
    """Converte a versão atual da nota (recortada, se houver) em PDF."""
    path = caminho_para_comprovante(receipt)
    img = _carregar_imagem_trabalho(path, receipt.rotacao)
    return _image_to_pdf_bytes(img)


def export_receipt_pdfs(trip: Trip, output_dir: Path, config: dict | None = None) -> Path:
    """Gera PDFs das notas em pastas com o nome da categoria.

    Usa sempre a imagem atual da nota (versão recortada quando o usuário recortou).
    Nome do arquivo: data_valor_estabelecimento.pdf
    """
    base = Path(output_dir)
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    ativos = [r for r in trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
    ativos.sort(key=lambda r: (r.data_ordenacao(), r.estabelecimento or ""))

    for r in ativos:
        pasta = base / _nome_pasta_categoria(r.categoria)
        pasta.mkdir(parents=True, exist_ok=True)

        data_part = (r.data or "sem-data").replace("/", "-")
        if r.valor is not None:
            valor_part = f"{float(r.valor):.2f}".replace(".", "-")
        else:
            valor_part = "sem-valor"
        est = slugify_filename(r.estabelecimento or Path(r.arquivo).stem, max_len=50)
        base_nome = f"{data_part}_{valor_part}_{est}"
        dest = pasta / f"{base_nome}.pdf"
        n = 1
        while dest.exists():
            dest = pasta / f"{base_nome}_{n}.pdf"
            n += 1

        try:
            pdf_bytes = receipt_to_pdf_bytes(r)
            dest.write_bytes(pdf_bytes)
        except Exception as e:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            c = canvas.Canvas(str(dest), pagesize=A4)
            c.drawString(50, 800, f"Falha ao converter: {Path(r.arquivo).name}")
            c.drawString(50, 780, str(e)[:100])
            c.save()

    return base


def _nome_pasta_categoria(categoria: str | None) -> str:
    """Nome de pasta legível a partir da categoria (seguro no Windows)."""
    nome = (categoria or "Outros").strip() or "Outros"
    nome = nome.replace("/", "-").replace("\\", "-")
    for ch in '<>:"|?*':
        nome = nome.replace(ch, "")
    nome = " ".join(nome.split())
    return nome[:100] or "Outros"
