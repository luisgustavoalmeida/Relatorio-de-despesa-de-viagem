from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional


class ReceiptStatus(str, Enum):
    PENDENTE = "pendente"
    CONFERIDA = "conferida"
    EXCLUIDA = "excluida"


@dataclass
class Receipt:
    """Nota/comprovante de despesa extraído de uma imagem ou PDF."""

    id: str
    arquivo: str
    valor: Optional[float] = None
    data: Optional[str] = None  # YYYY-MM-DD
    hora: str = ""  # HH:MM (emissão/compra)
    estabelecimento: str = ""
    cnpj: str = ""  # CNPJ formatado (consulta Receita / OCR)
    categoria: str = "Outros"
    moeda: str = "BRL"
    confianca: float = 0.0
    status: ReceiptStatus = ReceiptStatus.PENDENTE
    observacoes: str = ""
    rotacao: int = 0  # graus: 0, 90, 180, 270
    fonte_extracao: str = ""  # rapidocr | tesseract | visao | manual
    origem_pdf: str = ""  # PDF de origem, se a nota veio de separação
    pagina: Optional[int] = None
    parte: Optional[int] = None
    arquivo_original: str = ""  # caminho antes de ir para excluidas/
    arquivo_pre_recorte: str = ""  # backup da imagem antes do 1º recorte
    # Fingerprint SHA-256 (data+hora+valor+comercio), não da imagem
    hash_conteudo: str = ""
    # Metadados da última conversa com IA (UI «Ver conversa»)
    ia_prompt: str = ""
    ia_resposta: str = ""
    ia_provedor: str = ""
    ia_modelo: str = ""
    ia_em: str = ""  # ISO da última extração com IA
    atualizado_em: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def data_ordenacao(self) -> date:
        if self.data:
            try:
                return date.fromisoformat(self.data)
            except ValueError:
                pass
        return date.max

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Receipt:
        status = data.get("status", ReceiptStatus.PENDENTE.value)
        if isinstance(status, ReceiptStatus):
            status_enum = status
        else:
            try:
                status_enum = ReceiptStatus(status)
            except ValueError:
                status_enum = ReceiptStatus.PENDENTE
        try:
            confianca = float(data.get("confianca", 0.0) or 0.0)
        except (TypeError, ValueError):
            confianca = 0.0
        try:
            rotacao = int(data.get("rotacao", 0) or 0)
        except (TypeError, ValueError):
            rotacao = 0
        return cls(
            id=str(data.get("id") or ""),
            arquivo=str(data.get("arquivo") or ""),
            valor=data.get("valor"),
            data=data.get("data"),
            hora=data.get("hora", "") or "",
            estabelecimento=data.get("estabelecimento", ""),
            cnpj=data.get("cnpj", "") or "",
            categoria=data.get("categoria", "Outros"),
            moeda=data.get("moeda", "BRL"),
            confianca=confianca,
            status=status_enum,
            observacoes=data.get("observacoes", ""),
            rotacao=rotacao,
            fonte_extracao=data.get("fonte_extracao", ""),
            origem_pdf=data.get("origem_pdf", ""),
            pagina=data.get("pagina"),
            parte=data.get("parte"),
            arquivo_original=data.get("arquivo_original", ""),
            arquivo_pre_recorte=data.get("arquivo_pre_recorte", "") or "",
            hash_conteudo=data.get("hash_conteudo", "") or "",
            ia_prompt=data.get("ia_prompt", "") or "",
            ia_resposta=data.get("ia_resposta", "") or "",
            ia_provedor=data.get("ia_provedor", "") or "",
            ia_modelo=data.get("ia_modelo", "") or "",
            ia_em=data.get("ia_em", "") or "",
            atualizado_em=data.get("atualizado_em", datetime.now().isoformat(timespec="seconds")),
        )
