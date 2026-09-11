"""Filtros e rótulos de período para relatórios e visualização."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Literal

from app.models.receipt import Receipt, ReceiptStatus

ModoPeriodo = Literal["completo", "mes", "intervalo"]

_MESES_PT = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def parse_data_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def ano_mes_de(value: str | None) -> str | None:
    d = parse_data_iso(value)
    if not d:
        return None
    return f"{d.year:04d}-{d.month:02d}"


def rotulo_mes(ano_mes: str) -> str:
    """Ex.: '2026-05' → 'Maio/2026'."""
    try:
        y, m = ano_mes.split("-", 1)
        return f"{_MESES_PT[int(m)]}/{y}"
    except (ValueError, IndexError, KeyError):
        return ano_mes


def limites_mes(ano_mes: str) -> tuple[str, str]:
    y, m = map(int, ano_mes.split("-", 1))
    ultimo = monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{ultimo:02d}"


def receipts_ativos(receipts: Iterable[Receipt]) -> list[Receipt]:
    return [r for r in receipts if r.status != ReceiptStatus.EXCLUIDA]


def resumo_por_mes(receipts: Iterable[Receipt]) -> list[tuple[str, int, float]]:
    """[(ano_mes, qtd, total), ...] ordenado."""
    qtd: dict[str, int] = defaultdict(int)
    tot: dict[str, float] = defaultdict(float)
    for r in receipts_ativos(receipts):
        am = ano_mes_de(r.data)
        if not am:
            continue
        qtd[am] += 1
        if r.valor is not None:
            try:
                tot[am] += float(r.valor)
            except (TypeError, ValueError):
                pass
    return [(am, qtd[am], tot[am]) for am in sorted(qtd.keys())]


def notas_sem_data(receipts: Iterable[Receipt]) -> list[Receipt]:
    return [r for r in receipts_ativos(receipts) if not parse_data_iso(r.data)]


def filtrar_por_mes(receipts: Iterable[Receipt], ano_mes: str) -> list[Receipt]:
    return [r for r in receipts_ativos(receipts) if ano_mes_de(r.data) == ano_mes]


def filtrar_por_intervalo(
    receipts: Iterable[Receipt],
    data_inicio: str,
    data_fim: str,
) -> list[Receipt]:
    di = parse_data_iso(data_inicio)
    df = parse_data_iso(data_fim)
    if not di or not df:
        raise ValueError("Datas do intervalo inválidas. Use AAAA-MM-DD.")
    if di > df:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    out: list[Receipt] = []
    for r in receipts_ativos(receipts):
        d = parse_data_iso(r.data)
        if d is None:
            continue
        if di <= d <= df:
            out.append(r)
    return out


def periodo_das_notas(receipts: Iterable[Receipt]) -> tuple[str, str] | None:
    """Menor e maior data ISO das notas; None se nenhuma tiver data válida."""
    datas = sorted(d for r in receipts if (d := parse_data_iso(getattr(r, "data", None))))
    if not datas:
        return None
    return datas[0].isoformat(), datas[-1].isoformat()


def pasta_envio_slug(receipts: Iterable[Receipt]) -> str:
    """Sempre pelo período das notas: RDV_2026-04-04_a_2026-04-10."""
    periodo = periodo_das_notas(receipts)
    if not periodo:
        return "RDV_sem_data"
    return f"RDV_{periodo[0]}_a_{periodo[1]}"


@dataclass(frozen=True)
class EscopoRelatorio:
    modo: ModoPeriodo
    ano_mes: str | None = None
    data_inicio: str | None = None
    data_fim: str | None = None

    def rotulo(self) -> str:
        if self.modo == "completo":
            return "Completo (todas as notas)"
        if self.modo == "mes" and self.ano_mes:
            return f"Mês {rotulo_mes(self.ano_mes)}"
        if self.modo == "intervalo" and self.data_inicio and self.data_fim:
            return f"Período {self.data_inicio} a {self.data_fim}"
        return "Escopo"

    def datas_cabecalho(self) -> tuple[str | None, str | None]:
        if self.modo == "mes" and self.ano_mes:
            return limites_mes(self.ano_mes)
        if self.modo == "intervalo":
            return self.data_inicio, self.data_fim
        return None, None


def selecionar_receipts(receipts: Iterable[Receipt], escopo: EscopoRelatorio) -> list[Receipt]:
    if escopo.modo == "completo":
        return receipts_ativos(receipts)
    if escopo.modo == "mes":
        if not escopo.ano_mes:
            raise ValueError("Mês não informado.")
        return filtrar_por_mes(receipts, escopo.ano_mes)
    if escopo.modo == "intervalo":
        if not escopo.data_inicio or not escopo.data_fim:
            raise ValueError("Informe data inicial e final.")
        return filtrar_por_intervalo(receipts, escopo.data_inicio, escopo.data_fim)
    raise ValueError(f"Modo desconhecido: {escopo.modo}")
