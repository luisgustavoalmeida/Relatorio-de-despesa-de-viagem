from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.receipt import ReceiptStatus
from app.models.trip import Trip
from app.storage import format_brl


def export_pdf_report(
    trip: Trip,
    output_path: Path,
    *,
    escopo_rotulo: str | None = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleBR", parent=styles["Heading1"], fontSize=16, spaceAfter=12)
    normal = styles["Normal"]

    story = []
    story.append(Paragraph("Relatório de Despesas de Viagem", title_style))
    if escopo_rotulo:
        story.append(Paragraph(f"<b>Escopo deste relatório:</b> {escopo_rotulo}", normal))
    if trip.natureza_servico:
        story.append(Paragraph(f"<b>Natureza do serviço:</b> {trip.natureza_servico}", normal))
    if trip.empreendimento:
        story.append(Paragraph(f"<b>Empreendimento:</b> {trip.empreendimento}", normal))
    if trip.contratante:
        story.append(Paragraph(f"<b>Contratante:</b> {trip.contratante}", normal))
    story.append(Paragraph(f"<b>Nome funcionário:</b> {trip.nome_funcionario or '—'}", normal))
    if trip.endereco:
        story.append(Paragraph(f"<b>Endereço:</b> {trip.endereco}", normal))
    if trip.cidade or trip.estado:
        loc = ", ".join(p for p in (trip.cidade, trip.estado) if p)
        story.append(Paragraph(f"<b>Cidade/Estado:</b> {loc}", normal))
    if trip.area:
        story.append(Paragraph(f"<b>Área:</b> {trip.area}", normal))
    if trip.numero_os:
        story.append(Paragraph(f"<b>Nº O.S.:</b> {trip.numero_os}", normal))
    if trip.numero_rdv:
        story.append(Paragraph(f"<b>Nº RDV:</b> {trip.numero_rdv}", normal))
    story.append(
        Paragraph(
            f"<b>Período (notas deste relatório):</b> "
            f"{trip.inicio_contratual or '—'} a {trip.termino_contratual or '—'}",
            normal,
        )
    )
    if trip.contratada:
        story.append(Paragraph(f"<b>Contratada:</b> {trip.contratada}", normal))
    if trip.centro_custo:
        story.append(Paragraph(f"<b>Centro de custo:</b> {trip.centro_custo}", normal))
    if trip.adiantamento is not None:
        story.append(Paragraph(f"<b>Adiantamento:</b> {format_brl(trip.adiantamento)}", normal))
    story.append(Paragraph(f"<b>Total das despesas:</b> {format_brl(trip.total_geral())}", normal))
    story.append(
        Paragraph(
            f"<b>A reembolsar (total − adiantamento):</b> {format_brl(trip.valor_a_reembolsar())}",
            normal,
        )
    )
    if trip.observacoes:
        # Escapa tags básicas para não quebrar o Paragraph
        obs = (
            str(trip.observacoes)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        story.append(Paragraph(f"<b>Observações:</b> {obs}", normal))
    story.append(Spacer(1, 0.4 * cm))

    # Resumo por categoria
    story.append(Paragraph("<b>Totais por categoria</b>", styles["Heading2"]))
    totais = trip.totais_por_categoria()
    resumo_data = [["Categoria", "Total"]]
    for cat, total in totais.items():
        resumo_data.append([cat, format_brl(total)])
    resumo_data.append(["TOTAL GERAL", format_brl(trip.total_geral())])
    resumo_data.append(
        [
            "Adiantamento",
            format_brl(trip.adiantamento) if trip.adiantamento is not None else "R$ 0,00",
        ]
    )
    resumo_data.append(["A REEMBOLSAR", format_brl(trip.valor_a_reembolsar())])

    t1 = Table(resumo_data, colWidths=[10 * cm, 5 * cm])
    t1.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#d9e2f3")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f2f2f2")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t1)
    story.append(Spacer(1, 0.6 * cm))

    # Lançamentos cronológicos
    story.append(Paragraph("<b>Lançamentos (ordem cronológica)</b>", styles["Heading2"]))
    ativos = [r for r in trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
    ativos.sort(key=lambda r: (r.data_ordenacao(), r.estabelecimento or ""))

    lanc_data = [["Data", "Categoria", "Estabelecimento", "Valor"]]
    for r in ativos:
        lanc_data.append(
            [
                r.data or "—",
                r.categoria,
                (r.estabelecimento or Path(r.arquivo).name)[:40],
                format_brl(r.valor),
            ]
        )

    if len(lanc_data) == 1:
        lanc_data.append(["—", "—", "Nenhuma despesa", "—"])

    t2 = Table(lanc_data, colWidths=[2.5 * cm, 4.2 * cm, 6.3 * cm, 2.5 * cm])
    t2.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t2)

    doc.build(story)
    return output_path
