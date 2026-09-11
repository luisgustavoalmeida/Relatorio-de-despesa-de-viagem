from __future__ import annotations

import io
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageEnhance, ImageOps

from app.config import ROOT, load_config
from app.paths import env_path
from app.ocr.categorizer import infer_categoria_from_text, normalize_categoria

load_dotenv(env_path())

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

_RAPIDOCR_ENGINE = None
_RAPIDOCR_LOCK = threading.Lock()


def _open_image(path: Path) -> Image.Image:
    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Melhora contraste/legibilidade para OCR local."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Limita tamanho para performance
    max_side = 2000
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    gray = ImageEnhance.Sharpness(gray).enhance(1.2)
    return gray.convert("RGB")


def _image_to_jpeg_bytes(path: Path, max_side: int = 2200) -> bytes:
    img = _open_image(path)
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _pdf_first_page_to_image(path: Path, max_side: int = 2000) -> Image.Image:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        pil = page.render(scale=2).to_pil()
    finally:
        pdf.close()
    w, h = pil.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    return pil


def _pdf_first_page_to_jpeg(path: Path, max_side: int = 1600) -> bytes:
    pil = _pdf_first_page_to_image(path, max_side=max_side)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def file_to_jpeg_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".pdf":
        return _pdf_first_page_to_jpeg(path)
    return _image_to_jpeg_bytes(path)


def load_image_for_ocr(path: Path) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        return _pdf_first_page_to_image(path)
    return _open_image(path)


def _normalize_result(raw: dict[str, Any], categorias: list[str], fonte: str) -> dict[str, Any]:
    valor = raw.get("valor")
    if isinstance(valor, str):
        valor = _parse_br_money(valor)
    elif valor is not None:
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            valor = None
    if valor is not None and (valor <= 0 or valor >= 1_000_000):
        valor = None

    data = raw.get("data")
    if data:
        data = _normalize_date(str(data))

    hora = _normalize_hora(raw.get("hora"))
    if not hora and raw.get("data"):
        # Às vezes a IA junta data+hora no mesmo campo
        hora = _normalize_hora(str(raw.get("data")))

    estabelecimento = limpar_estabelecimento(raw.get("estabelecimento"))
    categoria = normalize_categoria(raw.get("categoria"), categorias)
    conf = raw.get("confianca", 0.5)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    if valor is None:
        conf = min(conf, 0.55)
    if estabelecimento_ruim(estabelecimento):
        conf = min(conf, 0.65)

    return {
        "valor": valor,
        "data": data,
        "hora": hora,
        "estabelecimento": estabelecimento,
        "categoria": categoria,
        "moeda": raw.get("moeda") or "BRL",
        "confianca": conf,
        "fonte_extracao": fonte,
        "cnpj": _extrair_cnpj(str(raw.get("cnpj") or "")) or _extrair_cnpj(str(estabelecimento or "")),
        "ia_prompt": raw.get("ia_prompt") or "",
        "ia_resposta": raw.get("ia_resposta") or "",
        "ia_provedor": raw.get("ia_provedor") or "",
        "ia_modelo": raw.get("ia_modelo") or "",
    }


def merge_vision_local(vision: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    """Completa/corrige resultado da IA com OCR local (valor e estabelecimento)."""
    out = dict(vision)
    extras: list[str] = []

    v_ai = out.get("valor")
    v_ocr = local.get("valor")
    if v_ai is None and v_ocr is not None:
        out["valor"] = v_ocr
        extras.append("valor")
    elif v_ai is not None and v_ocr is not None:
        # Se divergirem bastante e a confiança da IA for baixa, prefere OCR de TOTAL
        try:
            diff = abs(float(v_ai) - float(v_ocr))
            if diff >= 0.5 and float(out.get("confianca") or 0) < 0.75:
                # Se OCR veio de padrão TOTAL/PAGO, costuma ser mais confiável
                out["valor"] = v_ocr
                extras.append("valor~ocr")
        except (TypeError, ValueError):
            pass

    est_ai = out.get("estabelecimento") or ""
    est_ocr = local.get("estabelecimento") or ""
    # Preserva fallback CNPJ do OCR
    if est_ocr and not est_ocr.upper().startswith("CNPJ "):
        est_ocr = limpar_estabelecimento(est_ocr)
    if estabelecimento_ruim(est_ai) and est_ocr:
        out["estabelecimento"] = est_ocr
        extras.append("estab")
    elif est_ai and est_ocr:
        if len(est_ai) < 5 and len(est_ocr) >= 5:
            out["estabelecimento"] = est_ocr
            extras.append("estab")
        # IA devolveu e-mail/fragmento; OCR tem Loja:/nome real
        elif estabelecimento_ruim(est_ai) or "gmail" in est_ai.lower():
            out["estabelecimento"] = est_ocr
            extras.append("estab")
    elif not est_ai and est_ocr:
        out["estabelecimento"] = est_ocr
        extras.append("estab")

    if not out.get("data") and local.get("data"):
        out["data"] = local["data"]
        extras.append("data")

    if not out.get("hora") and local.get("hora"):
        out["hora"] = local["hora"]
        extras.append("hora")

    if not out.get("cnpj") and local.get("cnpj"):
        out["cnpj"] = local["cnpj"]
        extras.append("cnpj")

    if (out.get("categoria") in (None, "", "Outros")) and local.get("categoria") not in (None, "", "Outros"):
        out["categoria"] = local["categoria"]
        extras.append("cat")

    # Recalcula confiança mínima se ainda faltam campos críticos
    conf = float(out.get("confianca") or 0.5)
    if out.get("valor") is not None:
        conf = max(conf, 0.6)
    if out.get("data"):
        conf = max(conf, 0.55)
    if not estabelecimento_ruim(out.get("estabelecimento")):
        conf = max(conf, 0.55)
    if out.get("valor") is None:
        conf = min(conf, 0.5)
    out["confianca"] = min(conf, 0.95)

    fonte = out.get("fonte_extracao") or "visao"
    if extras:
        out["fonte_extracao"] = f"{fonte}+ocr({','.join(extras)})"
    return out


def _parse_br_money(text: str) -> float | None:
    t = text.strip()
    t = re.sub(r"[R$\s]", "", t, flags=re.IGNORECASE)
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _normalize_date(text: str) -> str | None:
    text = text.strip()

    def _valid(y: int, mo: int, d: int) -> str | None:
        if not (2000 <= y <= 2100):
            return None
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return _valid(y, mo, d)
    return None


def _normalize_hora(text: str | None) -> str | None:
    """Normaliza para HH:MM (descarta segundos). Aceita HH:MM ou HH:MM:SS."""
    if not text:
        return None
    # OCR: O→0 em dígitos de hora
    t = str(text).strip().upper().replace("O", "0")
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])[:hH]([0-5]\d)(?::([0-5]\d))?(?!\d)", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


def _find_date_in_text(text: str) -> str | None:
    """Prioriza data de emissão; aceita OCR com ruído (Emissao/Emissa0)."""
    text = text or ""
    # 1) Linhas com emissão / data / compra
    priority = re.compile(
        r"(?:emiss[aãa0o]{1,3}|data\s*(?:da\s*)?(?:compra|emiss|venda)?|dt\s*emiss|"
        r"via\s*consumidor)\b.{0,40}?"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.IGNORECASE | re.DOTALL,
    )
    for m in priority.finditer(text):
        data = _normalize_date(m.group(1))
        if data:
            return data

    # 2) Qualquer dd/mm/aaaa (ou com -)
    for m in re.finditer(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text):
        data = _normalize_date(m.group(1))
        if data:
            return data

    # 3) yyyy-mm-dd
    for m in re.finditer(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text):
        data = _normalize_date(f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
        if data:
            return data

    return None


def _find_hora_in_text(text: str) -> str | None:
    """Prioriza hora na mesma linha da emissão/data; senão primeira hora plausível."""
    text = text or ""
    # 1) Data + hora juntos (Emissão: 27/04/2026 18:48:50)
    juntos = re.compile(
        r"(?:emiss[aãa0o]{1,3}|data\s*(?:da\s*)?(?:compra|emiss|venda)?|dt\s*emiss)?"
        r".{0,20}?"
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
        r"[,\sT]+"
        r"([01]?\d|2[0-3])[:hH]([0-5]\d)(?::([0-5]\d))?",
        re.IGNORECASE | re.DOTALL,
    )
    for m in juntos.finditer(text):
        hora = _normalize_hora(f"{m.group(1)}:{m.group(2)}")
        if hora:
            return hora

    # 2) Rótulo explícito de hora
    rotulo = re.compile(
        r"(?:hora|hor[aá]rio|às|as)\s*[:.]?\s*([01]?\d|2[0-3])[:hH]([0-5]\d)",
        re.IGNORECASE,
    )
    for m in rotulo.finditer(text):
        hora = _normalize_hora(f"{m.group(1)}:{m.group(2)}")
        if hora:
            return hora

    # 3) Qualquer HH:MM no texto (evita faixas muito cedo sem contexto se houver várias)
    candidatas: list[str] = []
    for m in re.finditer(r"(?<!\d)([01]?\d|2[0-3])[:hH]([0-5]\d)(?::([0-5]\d))?(?!\d)", text):
        hora = _normalize_hora(f"{m.group(1)}:{m.group(2)}")
        if hora:
            candidatas.append(hora)
    if len(candidatas) == 1:
        return candidatas[0]
    if candidatas:
        # Prefere a primeira ocorrência (emissão costuma vir antes de horários de validade)
        return candidatas[0]
    return None


def _binarize_for_ocr(pil: Image.Image, scale: float = 2.5) -> Image.Image:
    """Upscale + limiar para linhas finas de cupom térmico (emissão/chave)."""
    if scale != 1.0:
        w, h = pil.size
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    g = ImageOps.grayscale(pil)
    g = ImageOps.autocontrast(g)
    g = ImageEnhance.Contrast(g).enhance(1.5)
    arr = np.array(g)
    thr = max(100, min(200, int(arr.mean() - 8)))
    bw = ((arr < thr) * 255).astype(np.uint8)
    return Image.fromarray(bw).convert("RGB")


def _find_emission_via_image_bands(img: Image.Image) -> dict[str, str]:
    """Segunda passagem OCR em faixas onde o cupom coloca data/hora de emissão."""
    found: dict[str, str] = {}
    try:
        engine = _get_rapidocr()
    except Exception:
        return found
    w, h = img.size
    bands = [
        (0, int(h * 0.48), w, int(h * 0.78)),
        (0, int(h * 0.55), w, int(h * 0.85)),
        (0, int(h * 0.35), w, int(h * 0.65)),
    ]
    for box in bands:
        if box[3] - box[1] < 40:
            continue
        try:
            crop = img.crop(box)
            prepared = _binarize_for_ocr(crop, scale=2.8)
            with _RAPIDOCR_LOCK:
                result, _ = engine(np.array(prepared))
            if not result:
                continue
            text = "\n".join(str(item[1]) for item in result)
            if "data" not in found:
                data = _find_date_in_text(text)
                if data:
                    found["data"] = data
            if "hora" not in found:
                hora = _find_hora_in_text(text)
                if hora:
                    found["hora"] = hora
            if len(found) >= 2:
                break
        except Exception:
            continue
    return found


def _find_date_via_image_bands(img: Image.Image) -> str | None:
    """Compat: só a data das faixas de emissão."""
    return _find_emission_via_image_bands(img).get("data")


_TOTAL_LABEL_RE = re.compile(
    r"(?:valor\s*total|u?alor\s*total|valor\s*(?:pago|a\s*pagar)|total\s*(?:a\s*pagar|geral|liquido|líquido|pago)|"
    r"sub\s*total|subtotal|vl\.?\s*total|tot\.?\s*liq)",
    re.IGNORECASE,
)
# Valores monetários BR/US com exatamente 2 casas (evita litros 34,732 e CNPJ)
_MONEY_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,6}\.\d{2}|\d{1,6},\d{2})(?!\d)"
)


def _ocr_fix_typos(text: str) -> str:
    """Normaliza erros comuns de OCR em cupons térmicos."""
    t = text or ""
    t = t.replace("Rs", "R$").replace("RS", "R$").replace("R§", "R$")
    t = re.sub(r"\bUalor\b", "Valor", t, flags=re.IGNORECASE)
    t = re.sub(r"\bUALOR\b", "VALOR", t)
    return t


def _find_total_in_text(text: str) -> float | None:
    """Extrai total mesmo quando rótulo e valor estão em linhas diferentes (NFC-e)."""
    text = _ocr_fix_typos(text)
    lower = text.lower()
    money = _MONEY_RE.pattern

    # 1) Rótulo + valor (espaços/quebras no meio — comum em NFC-e)
    same_line = [
        rf"(?:valor\s*total|u?alor\s*total|valor\s*(?:pago|a\s*pagar)|total\s*(?:a\s*pagar|geral|liquido|líquido|pago)|"
        rf"sub\s*total|subtotal|vl\.?\s*total)\s*[:\-]?\s*r?\$?\s*{money}",
        rf"(?:^|\n)\s*total\s*(?:pago|geral|da\s+nota)?\s*[:\-]?\s*r?\$?\s*{money}",
        rf"(?:cartao|cartão|dinheiro|pix|debito|débito|credito|crédito|mastercard|visa|elo)\b[^\n]{{0,60}}{money}",
    ]
    for pat in same_line:
        candidates: list[float] = []
        for m in re.finditer(pat, lower, flags=re.IGNORECASE | re.MULTILINE):
            v = _parse_br_money(m.group(1))
            if v is not None and 0.5 < v < 1_000_000:
                candidates.append(v)
        if candidates:
            # Prefere o valor que mais se repete; senão o maior entre totais
            from collections import Counter

            counted = Counter(round(v, 2) for v in candidates)
            best, n = counted.most_common(1)[0]
            if n >= 2:
                return float(best)
            return max(candidates)

    # 2) Rótulo numa linha, valor na próxima (gap horizontal do cupom)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    label_prefix = re.compile(
        r"^(?:valor\s*total|u?alor\s*total|sub\s*total|subtotal|valor\s*pago|"
        r"total\s*(?:pago|geral|a\s*pagar)?)\b",
        re.IGNORECASE,
    )
    for i, ln in enumerate(lines):
        ln_n = _ocr_fix_typos(ln)
        has_money = _MONEY_RE.search(ln_n)
        if label_prefix.search(ln_n) and not has_money:
            for j in range(i + 1, min(i + 4, len(lines))):
                m = _MONEY_RE.search(lines[j])
                if m:
                    v = _parse_br_money(m.group(1))
                    if v is not None and 0.5 < v < 1_000_000:
                        return v

    # 3) Fallback: valor que mais se repete quando há rótulo de total
    if _TOTAL_LABEL_RE.search(lower):
        moneys = []
        for m in _MONEY_RE.finditer(text):
            v = _parse_br_money(m.group(1))
            if v is not None and 1.0 < v < 1_000_000:
                moneys.append(round(v, 2))
        if moneys:
            from collections import Counter

            best, n = Counter(moneys).most_common(1)[0]
            if n >= 2:
                return float(best)

    return None


def _find_total_from_ocr_boxes(items: list) -> float | None:
    """Usa posição (Y) do RapidOCR: valor à direita do rótulo Valor Total / Subtotal."""
    if not items:
        return None

    rows: list[tuple[float, float, float, str]] = []
    for item in items:
        if len(item) < 2:
            continue
        box, text = item[0], str(item[1]).strip()
        if not text:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except (TypeError, ValueError, IndexError):
            continue
        cy = (min(ys) + max(ys)) / 2.0
        rows.append((cy, min(xs), max(xs), _ocr_fix_typos(text)))

    # Não usar "Total" isolado (cabeçalho de coluna) nem "Qtde.total"
    label_re = re.compile(
        r"(?:valor\s*total|u?alor\s*total|sub\s*total|subtotal|valor\s*pago|"
        r"total\s+(?:pago|geral|a\s*pagar))",
        re.IGNORECASE,
    )
    labels: list[tuple[float, float, str]] = []
    amounts: list[tuple[float, float, float]] = []  # cy, x0, value

    for cy, x0, x1, text in rows:
        if label_re.search(text) and not _MONEY_RE.search(text):
            labels.append((cy, x1, text))
        for m in _MONEY_RE.finditer(text):
            v = _parse_br_money(m.group(1))
            if v is not None and 0.5 < v < 1_000_000:
                amounts.append((cy, x0, v))

    def _prio(label: str) -> int:
        l = label.lower()
        if "valor total" in l or "ualor total" in l:
            return 0
        if "pago" in l:
            return 1
        if "sub" in l:
            return 2
        return 3

    labels.sort(key=lambda t: (_prio(t[2]), t[0]))
    for lcy, lx1, _lab in labels:
        best: tuple[float, float] | None = None  # (dx, value)
        for acy, ax0, val in amounts:
            if abs(acy - lcy) > 30:
                continue
            if ax0 + 5 < lx1:
                continue
            dx = ax0 - lx1
            if best is None or dx < best[0]:
                best = (dx, val)
        if best is not None:
            return best[1]
    return None


def _parece_data_hora(text: str) -> bool:
    """True se o texto é (ou é só) data/hora — não pode ser estabelecimento."""
    t = (text or "").strip()
    if not t:
        return False
    # Normaliza O→0 só para teste de data (OCR inverso)
    probe = t.upper().replace("O", "0").replace(" ", "")
    if re.search(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", probe):
        return True
    if re.search(r"\d{1,2}:\d{2}(:\d{2})?", probe):
        return True
    # Quase só dígitos, barras e dois-pontos (ex.: 27/O4/2O26,12:O8)
    compact = re.sub(r"[\dO/.:\-,;\s]", "", t.upper())
    if len(t) >= 8 and len(compact) <= 1 and re.search(r"[/:\-]", t):
        return True
    return False


def _fix_ocr_company_name(text: str) -> str:
    """Corrige OCR típico em razão social (0↔O, nomes colados)."""
    t = (text or "").strip()
    if not t:
        return ""
    # Nunca "corrigir" data/hora virando O (27/04 → 27/O4)
    if _parece_data_hora(t):
        return t.strip()
    t = t.upper()
    # Em nomes, zero costuma ser O (P0ST0 → POSTO, OLEGARI0 → OLEGARIO)
    t = re.sub(r"0", "O", t)
    # A↔9 entre letras (UIS9O → UISAO)
    t = re.sub(r"(?<=[A-Z])9(?=[A-Z])", "A", t)
    # Separar sufixos societários colados
    t = re.sub(r"([A-ZÁÉÍÓÚÃÕÇ])(LTDA|EIRELI|S\.?A\.?|ME|EPP)\b", r"\1 \2", t)
    # POSTOECO → POSTO ECO ; COMERCIOE → COMERCIO E
    t = re.sub(r"\b(POSTO|COMERCIO|COMÉRCIO|SERVICOS|SERVIÇOS)(?=[A-ZÁÉÍÓÚ])", r"\1 ", t)
    t = re.sub(r"\s+", " ", t).strip(" -|,;.")
    # Nomes de posto muito comuns com OCR ruim
    t = re.sub(r"\b[UV][I1L]S[AÁ]O\b", "VISAO", t)
    t = re.sub(r"\b[I1]P[I1]R[AÁ]NG[AÁ]\b", "IPIRANGA", t)
    return t


def _extrair_cnpj(text: str) -> str | None:
    """Retorna CNPJ formatado se encontrar no texto."""
    m = re.search(
        r"(\d{2})[.\s]?(\d{3})[.\s]?(\d{3})\s*/?\s*(\d{4})\s*-?\s*(\d{2})",
        text or "",
    )
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}/{m.group(4)}-{m.group(5)}"


def _nome_apos_loja(line: str) -> str | None:
    """Cupons de loja: 'Loja: LAVUP ARARAQUARA - JARDIM DO CARMO / SP'."""
    m = re.search(r"\bloja\s*[:\-]\s*(.+)$", line, flags=re.IGNORECASE)
    if not m:
        return None
    nome = m.group(1).strip()
    # Remove bairro/cidade após separador (mantém só o nome da loja)
    nome = re.split(
        r"\s*[\-–—▪•|]+\s*(?:JARDIM|BAIRRO|CENTRO|JD\.?|CIDADE)\b|"
        r"\s*/\s*SP\b|"
        r"\s*[\-–—▪•|]+\s*",
        nome,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    nome = re.sub(r"\s*/\s*$", "", nome).strip(" -|,;▪•")
    return nome or None


def _nome_apos_cnpj(line: str) -> str | None:
    """NFC-e: 'CNPJ: xx.xxx.xxx/xxxx-xx NOME DA EMPRESA'."""
    m = re.search(
        r"(?:cnpj|npj)\s*[:.]?\s*"
        r"\d{2}[.\s]?\d{3}[.\s]?\d{3}\s*/?\s*\d{4}\s*-?\s*\d{0,2}\s*"
        r"(.+)$",
        line,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # Sem rótulo CNPJ, só o número + nome
    m = re.search(
        r"\d{2}[.\s]?\d{3}[.\s]?\d{3}\s*/\s*\d{4}\s*-?\s*\d{0,2}\s+(.+)$",
        line,
    )
    if m:
        return m.group(1).strip()
    return None


def _guess_estabelecimento(lines: list[str]) -> str:
    skip = re.compile(
        r"(cpf|\bie\b|\bim\b|tel|telefone|fone|www\.|http|rua |av\.|avenida|cep|"
        r"@|gmail|hotmail|outlook|yahoo|email\b|"
        r"presidente |rodovia|km\s*\d|nf-|nfc-e|cupom|consumidor|extrato|"
        r"segunda.?via|documento auxiliar|documento aux|danfe|eletronica|eletrônica|"
        r"protocolo|chave de acesso|consulta|codigo|descri|quantidade|qtde|"
        r"valor\s*total|valor\s*pago|forma\s*de\s*paga|gasolina|etanol|diesel|"
        r"bomba|tanque|bico|obrigado|tributos|federal|estadual|"
        r"posto/bombas|ul\s*unit|vl\s*unit|vl\s*item)",
        re.IGNORECASE,
    )
    addressy = re.compile(
        r"^(?:rua|av\.|avenida|av\b|rod\.|rodovia|bairro|jardim|jd\.|cep)\b|"
        r"\b(rua|av\.|avenida|rodovia|km\b|cep\b|fone|tel)\b",
        re.IGNORECASE,
    )
    producty = re.compile(
        r"^\s*\d{1,4}\s+\d?\s*(gasolina|etanol|diesel|alcool|álcool)|"
        r"^\s*[o0]{1,2}\d\s+\d\s+|"  # OO1 2 / 001 2
        r"l\s*x\s*\d|hidrata",
        re.IGNORECASE,
    )
    company_hint = re.compile(
        r"\b(ltda|eireli|s\.?a\.?|me\b|epp|comercio|comércio|servicos|serviços|"
        r"posto|market|padaria|restaurante|lavup|lava|lavanderia)\b",
        re.IGNORECASE,
    )

    candidatos: list[tuple[int, str]] = []  # (prioridade, nome) — menor = melhor

    for i, ln in enumerate(lines[:20]):
        raw = ln.strip()
        if not raw:
            continue
        if _parece_data_hora(raw):
            continue

        # 0) Campo explícito "Loja: NOME"
        loja = _nome_apos_loja(raw)
        if loja:
            nome = _fix_ocr_company_name(loja)
            nome = limpar_estabelecimento(nome) or nome
            if nome and not estabelecimento_ruim(nome):
                candidatos.append((0, nome[:80]))
                continue

        # 1) Nome após CNPJ na mesma linha (NFC-e)
        after = _nome_apos_cnpj(raw)
        if after:
            if _parece_data_hora(after):
                continue
            nome = _fix_ocr_company_name(after)
            nome = limpar_estabelecimento(nome) or nome
            if nome and not estabelecimento_ruim(nome) and not addressy.search(nome) and not skip.search(nome):
                prio = 1 if company_hint.search(nome) else 2
                candidatos.append((prio, nome[:80]))
                continue

        clean = raw
        clean = re.sub(r"\bCNPJ\b|\bNPJ\b", " ", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{0,2}\b", " ", clean)
        clean = re.sub(r"^\s*loja\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip(" -|,;")
        if _parece_data_hora(clean):
            continue
        clean = _fix_ocr_company_name(clean)

        if len(clean) < 4 or len(clean) > 80:
            continue
        if skip.search(clean) or addressy.search(clean) or producty.search(clean):
            continue
        if re.fullmatch(r"[\d\s./\-]+", clean):
            continue
        if re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}", clean):
            continue
        if estabelecimento_ruim(clean):
            continue

        prio = 3 if company_hint.search(clean) else 6
        prio += min(i, 5)
        candidatos.append((prio, clean[:80]))

    if candidatos:
        candidatos.sort(key=lambda x: (x[0], -len(x[1])))
        return candidatos[0][1]

    # Fallback: CNPJ quando o nome não for legível
    joined = "\n".join(lines)
    cnpj = _extrair_cnpj(joined)
    return f"CNPJ {cnpj}" if cnpj else ""


def estabelecimento_ruim(nome: str | None) -> bool:
    """Detecta nomes que não devem ser usados como estabelecimento."""
    if not nome or not str(nome).strip():
        return True
    t = str(nome).strip()
    if len(t) < 3:
        return True
    # Fallback válido
    if re.match(r"^CNPJ\s+\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$", t, flags=re.IGNORECASE):
        return False
    if _parece_data_hora(t):
        return True
    lower = t.lower()
    ruins = (
        "consumidor",
        "nao identificado",
        "não identificado",
        "nfc-e",
        "cupom",
        "extrato",
        "total",
        "segunda via",
        "documento auxiliar",
        "chave de acesso",
        "consulta",
        "protocolo",
        "valor pago",
        "lida",
        "venc",
        "eletronica",
        "eletrônica",
        "gasolina",
        "etanol",
        "diesel",
        "qtde",
        "quantidade",
        "codigo",
        "descri",
        "posto/bombas",
        "bomba",
        "tanque",
        "obrigado",
        "emissao",
        "emissão",
        "gmail",
        "hotmail",
        "outlook",
        "yahoo",
        "email",
    )
    if any(x in lower for x in ruins):
        return True
    # CNPJ/CPF isolados sem ser o fallback formatado
    if re.fullmatch(r"cnpj|cpf", lower):
        return True
    if "@" in t or re.search(r"\.(com|br|net|org)\b", lower):
        return True
    # Linha de item: "001 2 GASOLINA..." ou só cabeçalho de tabela
    if re.match(r"^[\do]{1,4}\s+\d\s+", lower):
        return True
    if re.fullmatch(r"[\d\s./\-]+", t):
        return True
    if re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}", t) and not t.upper().startswith("CNPJ "):
        return True
    if lower in {"de", "da", "do", "e", "ltda", "me", "sa", "s.a.", "qtdeun", "un"}:
        return True
    return False


def limpar_estabelecimento(nome: str | None) -> str:
    if not nome:
        return ""
    t = str(nome).strip()
    # Fallback CNPJ já formatado — preservar
    if re.match(r"^CNPJ\s+\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$", t, flags=re.IGNORECASE):
        return t
    if _parece_data_hora(t):
        return ""
    t = _fix_ocr_company_name(t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\bCNPJ[:\s]*[\d./\-]+\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{0,2}\b", "", t)
    t = t.strip(" -|,;")
    if estabelecimento_ruim(t):
        return ""
    return t[:80]


def parse_receipt_text(text: str, categorias: list[str], fonte: str, ocr_conf: float | None = None) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    valor = _find_total_in_text(text)
    data = _find_date_in_text(text)
    hora = _find_hora_in_text(text)
    cnpj = _extrair_cnpj(text)
    estabelecimento = limpar_estabelecimento(_guess_estabelecimento(lines))
    if not estabelecimento and cnpj:
        estabelecimento = f"CNPJ {cnpj}"
    cat = infer_categoria_from_text(text, categorias) or "Outros"

    conf = 0.35
    if ocr_conf is not None:
        conf = max(conf, min(0.85, float(ocr_conf)))
    if valor is not None:
        conf += 0.2
    if data:
        conf += 0.15
    if hora:
        conf += 0.05
    if estabelecimento:
        conf += 0.05
    if cat != "Outros":
        conf += 0.05

    return {
        "valor": valor,
        "data": data,
        "hora": hora,
        "estabelecimento": estabelecimento,
        "categoria": normalize_categoria(cat, categorias),
        "moeda": "BRL",
        "confianca": min(conf, 0.92),
        "fonte_extracao": fonte,
        "cnpj": cnpj,
    }


# ---------- RapidOCR (gratuito, local, sem instalar Tesseract) ----------


def _get_rapidocr():
    global _RAPIDOCR_ENGINE
    if _RAPIDOCR_ENGINE is None:
        with _RAPIDOCR_LOCK:
            if _RAPIDOCR_ENGINE is None:
                from rapidocr_onnxruntime import RapidOCR

                _RAPIDOCR_ENGINE = RapidOCR()
    return _RAPIDOCR_ENGINE


def extract_with_rapidocr(path: Path, categorias: list[str]) -> dict[str, Any]:
    engine = _get_rapidocr()
    raw_img = load_image_for_ocr(path)
    img = _preprocess_for_ocr(raw_img)
    arr = np.array(img)
    with _RAPIDOCR_LOCK:
        result, _ = engine(arr)
    if not result:
        # Ainda tenta data/hora/chave em faixas na imagem original
        empty = empty_result("rapidocr")
        faixa = _find_emission_via_image_bands(raw_img)
        if faixa:
            empty.update(faixa)
            empty["confianca"] = 0.55
            empty["fonte_extracao"] = "rapidocr+faixa-data"
        return empty

    texts: list[str] = []
    scores: list[float] = []
    for item in result:
        # item: [box, text, score]
        if len(item) >= 3:
            texts.append(str(item[1]))
            try:
                scores.append(float(item[2]))
            except (TypeError, ValueError):
                pass
        elif len(item) == 2:
            texts.append(str(item[1]))

    text = "\n".join(texts)
    avg_score = sum(scores) / len(scores) if scores else None
    parsed = parse_receipt_text(text, categorias, "rapidocr", ocr_conf=avg_score)

    # NFC-e: rótulo e valor em caixas separadas na mesma linha visual
    if parsed.get("valor") is None:
        spatial = _find_total_from_ocr_boxes(result)
        if spatial is not None:
            parsed["valor"] = spatial
            parsed["confianca"] = min(0.92, float(parsed.get("confianca") or 0.5) + 0.2)
            parsed["fonte_extracao"] = "rapidocr+espacial"

    # Linha de emissão (data/hora) costuma falhar no OCR da página inteira
    if not parsed.get("data") or not parsed.get("hora"):
        faixa = _find_emission_via_image_bands(raw_img)
        extras: list[str] = []
        if not parsed.get("data") and faixa.get("data"):
            parsed["data"] = faixa["data"]
            extras.append("data")
        if not parsed.get("hora") and faixa.get("hora"):
            parsed["hora"] = faixa["hora"]
            extras.append("hora")
        if extras:
            parsed["confianca"] = min(0.92, float(parsed.get("confianca") or 0.5) + 0.15)
            fonte = parsed.get("fonte_extracao") or "rapidocr"
            parsed["fonte_extracao"] = f"{fonte}+faixa({','.join(extras)})"
    return parsed


# ---------- Tesseract (opcional) ----------


def extract_with_tesseract(path: Path, categorias: list[str]) -> dict[str, Any]:
    import pytesseract

    img = _preprocess_for_ocr(load_image_for_ocr(path))
    try:
        text = pytesseract.image_to_string(img, lang="por+eng")
    except Exception:
        text = pytesseract.image_to_string(img)
    return parse_receipt_text(text, categorias, "tesseract")


# ---------- Visão (provedores extensíveis) ----------


def empty_result(fonte: str = "manual") -> dict[str, Any]:
    return {
        "valor": None,
        "data": None,
        "hora": None,
        "estabelecimento": "",
        "categoria": "Outros",
        "moeda": "BRL",
        "confianca": 0.0,
        "fonte_extracao": fonte,
        "cnpj": None,
    }


def extract_local(path: Path, categorias: list[str]) -> dict[str, Any]:
    """OCR 100% local e gratuito. RapidOCR primeiro; Tesseract se disponível."""
    errors: list[str] = []
    try:
        result = extract_with_rapidocr(path, categorias)
        if result.get("valor") is not None or result.get("data") or (result.get("estabelecimento") or "").strip():
            return result
    except Exception as e:
        errors.append(f"rapidocr: {e}")

    try:
        return extract_with_tesseract(path, categorias)
    except Exception as e:
        errors.append(f"tesseract: {e}")

    try:
        return extract_with_rapidocr(path, categorias)
    except Exception:
        r = empty_result("falha")
        r["estabelecimento"] = path.stem
        r["observacoes"] = "; ".join(errors) if errors else ""
        return r


def _vision_provider_order(motor: str) -> list[str]:
    from app.ocr.providers import PROVIDER_REGISTRY

    if motor in PROVIDER_REGISTRY:
        return [motor]
    if motor in {"auto", "visao", "vision"}:
        # Preferência: gemini → openai → anthropic (quem tiver chave)
        order = []
        for pid in ("gemini", "openai", "anthropic"):
            prov = PROVIDER_REGISTRY.get(pid)
            if prov and prov.is_available():
                order.append(pid)
        return order
    return []


def extract_with_vision(
    jpeg_bytes: bytes,
    categorias: list[str],
    motor: str,
    visao_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    from app.ocr.providers import get_provider

    model_map = {
        "gemini": visao_cfg.get("modelo_gemini"),
        "openai": visao_cfg.get("modelo_openai"),
        "anthropic": visao_cfg.get("modelo_anthropic"),
    }
    last_error: Exception | None = None
    for pid in _vision_provider_order(motor):
        provider = get_provider(pid)
        if not provider or not provider.is_available():
            continue
        try:
            raw = provider.extract(jpeg_bytes, categorias, model=model_map.get(pid))
            return _normalize_result(raw, categorias, f"visao:{pid}")
        except Exception as e:
            last_error = e
            continue
    if last_error:
        # propaga só para log interno; caller trata None
        pass
    return None


def _cnpj_de_resultado(result: dict[str, Any], *textos_extra: str) -> str | None:
    """Obtém CNPJ do campo dedicado, do estabelecimento ou de textos OCR/IA."""
    from app.ocr.cnpj_lookup import cnpj_somente_digitos, formatar_cnpj

    candidatos: list[str] = []
    if result.get("cnpj"):
        candidatos.append(str(result["cnpj"]))
    est = str(result.get("estabelecimento") or "")
    if est:
        candidatos.append(est)
    for t in textos_extra:
        if t:
            candidatos.append(t)

    for raw in candidatos:
        # Já formatado
        digits = cnpj_somente_digitos(raw)
        if digits:
            return formatar_cnpj(digits)
        found = _extrair_cnpj(raw)
        if found:
            return found
    return None


def enriquecer_estabelecimento_via_cnpj(
    result: dict[str, Any],
    *,
    textos_ocr: str | None = None,
    habilitado: bool = True,
) -> dict[str, Any]:
    """Consulta APIs de CNPJ e usa o nome cadastral como estabelecimento padrão."""
    if not habilitado or not result:
        return result

    from app.ocr.cnpj_lookup import (
        consultar_nome_por_cnpj,
        formatar_cnpj,
    )

    cnpj = _cnpj_de_resultado(result, textos_ocr or "")
    if not cnpj:
        return result

    result["cnpj"] = cnpj
    consulta = consultar_nome_por_cnpj(cnpj)
    if not consulta or not consulta.get("nome"):
        atual = (result.get("estabelecimento") or "").strip()
        if not atual or estabelecimento_ruim(atual):
            result["estabelecimento"] = f"CNPJ {formatar_cnpj(cnpj)}"
        return result

    nome_api = consulta["nome"].strip()
    # Padrão: sempre o nome cadastral (fantasia/razão) da consulta CNPJ
    result["estabelecimento"] = nome_api
    fonte = result.get("fonte_extracao") or ""
    tag = f"cnpj:{consulta.get('fonte', 'api')}"
    if tag not in fonte:
        result["fonte_extracao"] = f"{fonte}+{tag}" if fonte else tag
    try:
        result["confianca"] = min(0.95, float(result.get("confianca") or 0.5) + 0.08)
    except (TypeError, ValueError):
        pass
    return result


def extract_receipt(path: Path | str, config: dict | None = None) -> dict[str, Any]:
    """Extrai dados de uma notinha conforme o motor escolhido pelo usuário.

    Com IA: visão + OCR local; em seguida enriquece estabelecimento via CNPJ (APIs).
    """
    from app.categorias_usuario import categorias_completas
    from app.settings import apply_settings_to_config

    cfg = apply_settings_to_config(config or load_config())
    categorias = categorias_completas(cfg)
    path = Path(path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()

    ocr_cfg = cfg.get("ocr", {})
    visao = cfg.get("visao", {})
    # Preferência do usuário (JSON via apply_settings_to_config); .env só como fallback
    motor = (ocr_cfg.get("motor") or os.getenv("OCR_MOTOR") or "local").lower()
    cnpj_lookup_on = bool(cfg.get("cnpj_lookup", ocr_cfg.get("cnpj_lookup", True)))

    def _final(result: dict[str, Any], texto_hint: str | None = None) -> dict[str, Any]:
        return enriquecer_estabelecimento_via_cnpj(
            result,
            textos_ocr=texto_hint,
            habilitado=cnpj_lookup_on,
        )

    if motor == "local":
        local = extract_local(path, categorias)
        return _final(local)

    jpeg: bytes | None = None
    try:
        jpeg = file_to_jpeg_bytes(path)
    except Exception:
        jpeg = None

    vision_result: dict[str, Any] | None = None
    if jpeg is not None:
        vision_result = extract_with_vision(jpeg, categorias, motor, visao)

    def _visao_suficiente(result: dict[str, Any] | None) -> bool:
        if not result:
            return False
        if result.get("valor") is None or not result.get("data"):
            return False
        if result.get("cnpj"):
            return True
        est = result.get("estabelecimento") or ""
        return bool(est) and not estabelecimento_ruim(est)

    # OCR local só se a IA falhar campos críticos (valor/data/estabelecimento)
    if vision_result and _visao_suficiente(vision_result):
        return _final(vision_result)

    local_result: dict[str, Any] | None = None
    try:
        local_result = extract_local(path, categorias)
    except Exception:
        local_result = None

    if vision_result and local_result:
        if local_result.get("cnpj") and not vision_result.get("cnpj"):
            vision_result = dict(vision_result)
            vision_result["cnpj"] = local_result["cnpj"]
        merged = merge_vision_local(vision_result, local_result)
        if local_result.get("cnpj"):
            merged["cnpj"] = local_result["cnpj"]
        return _final(merged)

    if vision_result:
        return _final(vision_result)
    if local_result and local_result.get("fonte_extracao") != "falha":
        local_result["fonte_extracao"] = f"local(fallback:{motor})"
        return _final(local_result)

    r = empty_result("falha")
    r["estabelecimento"] = path.stem
    return r
