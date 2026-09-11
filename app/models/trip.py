from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.schema import ALIASES_LEGADO, CHAVE_JSON_CONTRATANTE, CHAVE_JSON_NATUREZA_SERVICO

from .receipt import Receipt


@dataclass
class Trip:
    """Cabeçalho RDV e estado de um projeto de viagem."""

    # Cabeçalho padronizado (Gerar_Relatorio + extras RDV)
    natureza_servico: str = ""
    empreendimento: str = ""
    contratante: str = ""
    endereco: str = ""
    cidade: str = ""
    estado: str = ""
    inicio_contratual: Optional[str] = None  # YYYY-MM-DD
    termino_contratual: Optional[str] = None  # YYYY-MM-DD
    contratada: str = ""
    nome_funcionario: str = ""
    telefone: str = ""
    area: str = ""
    numero_os: str = ""
    numero_rdv: str = ""
    centro_custo: str = ""
    adiantamento: Optional[float] = None
    observacoes: str = ""
    # Path relativo a ROOT (ex.: assets/assinaturas/luis_gustavo.png)
    assinatura_arquivo: str = ""
    pasta_raiz: str = ""
    pasta_notas: str = "notas"
    pasta_saida: str = "output"
    receipts: list[Receipt] = field(default_factory=list)
    atualizado_em: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def chave_projeto(self) -> dict[str, str]:
        return {
            CHAVE_JSON_CONTRATANTE: (self.contratante or "").strip(),
            CHAVE_JSON_NATUREZA_SERVICO: (self.natureza_servico or "").strip(),
        }

    def receipts_ativos(self) -> list[Receipt]:
        from .receipt import ReceiptStatus

        return [r for r in self.receipts if r.status != ReceiptStatus.EXCLUIDA]

    def totais_por_categoria(self) -> dict[str, float]:
        totais: dict[str, float] = {}
        for r in self.receipts_ativos():
            if r.valor is None:
                continue
            try:
                valor = float(r.valor)
            except (TypeError, ValueError):
                continue
            cat = (r.categoria or "").strip() or "Outros"
            totais[cat] = totais.get(cat, 0.0) + valor
        return dict(sorted(totais.items(), key=lambda x: (-x[1], x[0])))

    def total_geral(self) -> float:
        return float(sum(self.totais_por_categoria().values()))

    def valor_a_reembolsar(self) -> float:
        """Total das despesas menos adiantamento (mínimo 0)."""
        total = self.total_geral()
        adiant = float(self.adiantamento or 0)
        return max(0.0, total - adiant)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["receipts"] = [r.to_dict() for r in self.receipts]
        data["chave"] = self.chave_projeto()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trip:
        raw = dict(data or {})
        # Compatibilidade com projetos antigos
        for antigo, novo in ALIASES_LEGADO.items():
            if not str(raw.get(novo) or "").strip() and raw.get(antigo) not in (None, ""):
                raw[novo] = raw[antigo]

        chave = raw.get("chave") if isinstance(raw.get("chave"), dict) else {}
        if not str(raw.get("contratante") or "").strip():
            raw["contratante"] = str(chave.get(CHAVE_JSON_CONTRATANTE) or "")
        if not str(raw.get("natureza_servico") or "").strip():
            raw["natureza_servico"] = str(chave.get(CHAVE_JSON_NATUREZA_SERVICO) or "")

        receipts = [Receipt.from_dict(r) for r in raw.get("receipts", [])]
        adiantamento = raw.get("adiantamento")
        if adiantamento is not None and adiantamento != "":
            try:
                adiantamento = float(adiantamento)
            except (TypeError, ValueError):
                adiantamento = None
        else:
            adiantamento = None

        return cls(
            natureza_servico=str(raw.get("natureza_servico") or ""),
            empreendimento=str(raw.get("empreendimento") or ""),
            contratante=str(raw.get("contratante") or ""),
            endereco=str(raw.get("endereco") or ""),
            cidade=str(raw.get("cidade") or ""),
            estado=str(raw.get("estado") or ""),
            inicio_contratual=raw.get("inicio_contratual") or None,
            termino_contratual=raw.get("termino_contratual") or None,
            contratada=str(raw.get("contratada") or ""),
            nome_funcionario=str(raw.get("nome_funcionario") or ""),
            telefone=str(raw.get("telefone") or ""),
            area=str(raw.get("area") or ""),
            numero_os=str(raw.get("numero_os") or ""),
            numero_rdv=str(raw.get("numero_rdv") or ""),
            centro_custo=str(raw.get("centro_custo") or ""),
            adiantamento=adiantamento,
            observacoes=str(raw.get("observacoes") or ""),
            assinatura_arquivo=str(raw.get("assinatura_arquivo") or ""),
            pasta_raiz=str(raw.get("pasta_raiz") or ""),
            pasta_notas=str(raw.get("pasta_notas") or "notas"),
            pasta_saida=str(raw.get("pasta_saida") or "output"),
            receipts=receipts,
            atualizado_em=raw.get("atualizado_em")
            or datetime.now().isoformat(timespec="seconds"),
        )
