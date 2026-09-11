"""Geração de pacotes de relatório em pastas curtas sob output/.

A pasta output/ contém só o que o usuário envia à empresa
(Excel, PDF e comprovantes). Metadados internos ficam em meta/relatorios/.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.atomic_io import atomic_write_json
from app.models.receipt import Receipt
from app.models.trip import Trip
from app.reports.excel_export import export_excel
from app.reports.pdf_receipts import export_receipt_pdfs
from app.reports.pdf_report import export_pdf_report
from app.reports.period import EscopoRelatorio, pasta_envio_slug, periodo_das_notas, selecionar_receipts
from app.storage import format_brl


def pasta_relatorios(pasta_saida: Path) -> Path:
    """Raiz das pastas enviáveis (a própria pasta de saída)."""
    return Path(pasta_saida)


def pasta_meta_relatorios(pasta_saida: Path) -> Path:
    """Metadados internos do relatório (fora de output/).

    Projetos padrão: ``projetos/<slug>/meta/relatorios/``
    (irmão de ``output/``, não entra no pacote enviado à empresa).
    """
    saida = Path(pasta_saida).resolve()
    # .../projeto/output → .../projeto/meta/relatorios
    if saida.name.lower() == "output":
        return saida.parent / "meta" / "relatorios"
    return saida / "_meta" / "relatorios"


def _slug_nome_arquivo(texto: str, max_len: int = 60) -> str:
    """Espaços → _; mantém acentos; remove caracteres inválidos no Windows."""
    partes: list[str] = []
    for ch in (texto or "").strip():
        if ch.isalnum() or ch in "-_":
            partes.append(ch)
        elif ch.isspace() or ch in "./\\":
            partes.append("_")
        elif ch in '<>:"|?*':
            continue
        else:
            partes.append("_")
    s = re.sub(r"_+", "_", "".join(partes)).strip("._")
    return (s[:max_len] or "Sem_Nome")


def _prefixo_periodo(escopo: EscopoRelatorio, receipts: list[Receipt]) -> str:
    """Prefixo do arquivo: 2026-04-04_a_2026-04-10 (sempre pelo período das notas)."""
    periodo = periodo_das_notas(receipts)
    if periodo:
        return f"{periodo[0]}_a_{periodo[1]}"
    if escopo.modo == "mes" and escopo.ano_mes:
        di, df = escopo.datas_cabecalho()
        if di and df:
            return f"{di}_a_{df}"
    if escopo.modo == "intervalo" and escopo.data_inicio and escopo.data_fim:
        return f"{escopo.data_inicio}_a_{escopo.data_fim}"
    return "completo"


def nome_base_rdv(trip: Trip, escopo: EscopoRelatorio, receipts: list[Receipt]) -> str:
    """Padrão: 2026-08_RDV_Natureza_do_Serviço_Luís_Gustavo_de_Almeida."""
    periodo = _prefixo_periodo(escopo, receipts)
    natureza = _slug_nome_arquivo(trip.natureza_servico or "Sem_Natureza", max_len=50)
    nome = _slug_nome_arquivo(trip.nome_funcionario or "Sem_Nome", max_len=50)
    base = f"{periodo}_RDV_{natureza}_{nome}"
    return base[:180]


def trip_para_escopo(trip: Trip, receipts: list[Receipt], escopo: EscopoRelatorio) -> Trip:
    """Cópia do projeto só com as notas do escopo (nunca persistida)."""
    di, df = escopo.datas_cabecalho()
    # Adiantamento só no consolidado completo — evita rateio errado por mês
    adiant = trip.adiantamento if escopo.modo == "completo" else None
    obs_extra = ""
    if escopo.modo != "completo" and trip.adiantamento is not None:
        obs_extra = (
            f"\n[Escopo: {escopo.rotulo()}] Adiantamento do projeto "
            f"({format_brl(trip.adiantamento)}) não rateado neste recorte — "
            "use o relatório completo para o cálculo de reembolso com adiantamento."
        )
    observacoes = ((trip.observacoes or "").rstrip() + obs_extra).strip()
    return replace(
        trip,
        receipts=list(receipts),
        inicio_contratual=di or trip.inicio_contratual,
        termino_contratual=df or trip.termino_contratual,
        adiantamento=adiant,
        observacoes=observacoes,
    )


def exportar_pacote(
    trip: Trip,
    pasta_saida: Path,
    escopo: EscopoRelatorio,
    config: dict | None = None,
) -> Path:
    """Gera o pacote enviável e grava manifesto em meta/.

    Enviável (output):
      output/RDV_2026-04-04_a_2026-04-10/
        2026-04-04_a_2026-04-10_RDV_Natureza_Nome.xlsx
        2026-04-04_a_2026-04-10_RDV_Natureza_Nome.pdf
        comprovantes/...

    Interno (não enviar):
      meta/relatorios/RDV_2026-04-04_a_2026-04-10.json
    """
    selecionados = selecionar_receipts(trip.receipts, escopo)
    if not selecionados:
        raise ValueError(f"Nenhuma nota no escopo «{escopo.rotulo()}».")

    slug = pasta_envio_slug(selecionados)
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    base = pasta_saida / slug
    tmp = pasta_saida / f".tmp_{slug}"
    backup: Path | None = None

    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    trip_exp = trip_para_escopo(trip, selecionados, escopo)
    periodo = periodo_das_notas(selecionados)
    if periodo:
        trip_exp = replace(
            trip_exp,
            inicio_contratual=periodo[0],
            termino_contratual=periodo[1],
        )
    nome_base = nome_base_rdv(trip, escopo, selecionados)

    try:
        excel_path = export_excel(trip_exp, tmp / f"{nome_base}.xlsx")
        pdf_path = export_pdf_report(
            trip_exp,
            tmp / f"{nome_base}.pdf",
            escopo_rotulo=escopo.rotulo(),
        )
        comps = export_receipt_pdfs(trip_exp, tmp / "comprovantes", config)

        if base.exists():
            backup = pasta_saida / f".bak_{slug}"
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            base.rename(backup)
        tmp.rename(base)
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
            backup = None
    except Exception:
        if backup is not None and backup.exists() and not base.exists():
            try:
                backup.rename(base)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    manifesto = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "escopo": escopo.rotulo(),
        "modo": escopo.modo,
        "ano_mes": escopo.ano_mes,
        "data_inicio": escopo.data_inicio,
        "data_fim": escopo.data_fim,
        "qtd_notas": len(selecionados),
        "total": trip_exp.total_geral(),
        "adiantamento_incluido": trip_exp.adiantamento is not None,
        "pasta_envio": str(base.name),
        "arquivos": {
            "excel": excel_path.name,
            "pdf": pdf_path.name,
            "comprovantes": str(comps.name if hasattr(comps, "name") else "comprovantes"),
        },
        "notas": [
            {
                "id": r.id,
                "data": r.data,
                "valor": r.valor,
                "categoria": r.categoria,
                "estabelecimento": r.estabelecimento,
            }
            for r in selecionados
        ],
    }
    meta_dir = pasta_meta_relatorios(pasta_saida)
    meta_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(meta_dir / f"{slug}.json", manifesto)

    return base
