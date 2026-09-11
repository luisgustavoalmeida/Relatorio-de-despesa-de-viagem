"""Campos de cabeçalho e chave do projeto — alinhados ao Gerar_Relatorio."""

from __future__ import annotations

# Chave do projeto (nome da pasta e lista «Projeto (contratante + natureza)»)
CHAVE_JSON_CONTRATANTE = "contratante"
CHAVE_JSON_NATUREZA_SERVICO = "natureza_servico"

# Ordem canónica do formulário (como na aba Cabeçalhos do RDO) + extras do RDV
CAMPOS_CABECALHO: tuple[str, ...] = (
    "natureza_servico",
    "empreendimento",
    "contratante",
    "endereco",
    "cidade",
    "estado",
    "inicio_contratual",
    "termino_contratual",
    "contratada",
    "nome_funcionario",
    "telefone",
    "area",
    "numero_os",
    "numero_rdv",
    "centro_custo",
    "adiantamento",
)

ROTULOS_CABECALHO: dict[str, str] = {
    "natureza_servico": "Natureza do serviço",
    "empreendimento": "Empreendimento",
    "contratante": "Contratante",
    "endereco": "Endereço",
    "cidade": "Cidade",
    "estado": "Estado",
    "inicio_contratual": "Início contratual",
    "termino_contratual": "Término contratual",
    "contratada": "Contratada",
    "nome_funcionario": "Nome funcionário",
    "telefone": "Telefone",
    "area": "Área",
    "numero_os": "Nº O.S.",
    "numero_rdv": "Nº RDV",
    "centro_custo": "Centro de custo",
    "adiantamento": "Adiantamento (R$)",
}

# Campos incluídos no modelo de cabeçalho (sem chave de pastas)
CAMPOS_MODELO_CABECALHO: tuple[str, ...] = CAMPOS_CABECALHO + ("observacoes", "assinatura_arquivo")

# Mapeamento de chaves antigas → novas (compatibilidade)
ALIASES_LEGADO: dict[str, str] = {
    "nome": "nome_funcionario",
    "cliente": "contratante",
    "projeto": "empreendimento",
    "destino": "endereco",
    "empresa": "contratada",
    "data_inicio": "inicio_contratual",
    "data_fim": "termino_contratual",
}
