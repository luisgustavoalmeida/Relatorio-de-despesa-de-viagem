"""Gestão de projetos de viagem — chave = contratante + natureza do serviço."""

from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.atomic_io import atomic_write_json
from app.config import DATA_DIR, ROOT, ensure_data_dir, resolve_path
from app.models.trip import Trip
from app.paths import bundled_asset, projetos_dir, user_asset

PASTA_PROJETOS = projetos_dir()
ARQUIVO_REGISTRY = DATA_DIR / "projetos_registry.json"
ARQUIVO_MODELO_CABECALHO = user_asset("modelo_cabecalho.json")
NOME_PROJETO_JSON = "projeto.json"
_SAVE_LOCK = threading.Lock()


def slugify(nome: str, max_len: int = 80) -> str:
    texto = (nome or "").strip()
    texto = re.sub(r'[<>:"/\\|?*]', "", texto)
    texto = texto.replace(" ", "_")
    texto = re.sub(r"_+", "_", texto).strip("_")
    return (texto or "projeto")[:max_len]


def gerar_slug_chave(contratante: str, natureza_servico: str) -> str:
    """Mesmo padrão do Gerar_Relatorio: «Contratante - Natureza» → ficheiro/pasta."""
    combinado = f"{(contratante or '').strip()} - {(natureza_servico or '').strip()}"
    return slugify(combinado)


def garantir_pasta_projetos() -> Path:
    PASTA_PROJETOS.mkdir(parents=True, exist_ok=True)
    return PASTA_PROJETOS


def _registry_default() -> dict[str, Any]:
    return {"ultimo_projeto": "", "projetos": []}


def load_registry() -> dict[str, Any]:
    ensure_data_dir()
    if not ARQUIVO_REGISTRY.exists():
        return _registry_default()
    try:
        with open(ARQUIVO_REGISTRY, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return _registry_default()
    data.setdefault("ultimo_projeto", "")
    data.setdefault("projetos", [])
    return data


def save_registry(data: dict[str, Any]) -> None:
    ensure_data_dir()
    atomic_write_json(ARQUIVO_REGISTRY, data)


def pasta_projeto(slug: str) -> Path:
    return PASTA_PROJETOS / slug


def caminho_projeto_json(slug: str) -> Path:
    return pasta_projeto(slug) / NOME_PROJETO_JSON


def criar_estrutura_pastas(raiz: Path) -> None:
    (raiz / "notas").mkdir(parents=True, exist_ok=True)
    (raiz / "notas" / "excluidas").mkdir(parents=True, exist_ok=True)
    (raiz / "output").mkdir(parents=True, exist_ok=True)
    (raiz / "meta" / "relatorios").mkdir(parents=True, exist_ok=True)


def rotulo_projeto(trip: Trip) -> str:
    c = (trip.contratante or "").strip()
    n = (trip.natureza_servico or "").strip()
    if c and n:
        return f"{c} — {n}"
    if c or n:
        return c or n
    # Fallback para projetos antigos ainda sem chave preenchida
    nome = (trip.nome_funcionario or "").strip()
    end = (trip.endereco or "").strip()
    if nome and end:
        return f"{nome} — {end}"
    return nome or end or Path(trip.pasta_raiz or "projeto").name


def listar_projetos() -> list[tuple[str, Path, Trip | None]]:
    """Retorna (rótulo, pasta, trip_ou_None) ordenado."""
    garantir_pasta_projetos()
    itens: list[tuple[str, Path, Trip | None]] = []
    for pasta in sorted(PASTA_PROJETOS.iterdir()):
        if not pasta.is_dir() or pasta.name.startswith("."):
            continue
        json_path = pasta / NOME_PROJETO_JSON
        trip = None
        if json_path.exists():
            try:
                with open(json_path, encoding="utf-8") as f:
                    trip = Trip.from_dict(json.load(f))
            except Exception:
                trip = None
        if trip:
            rotulo = rotulo_projeto(trip)
        else:
            rotulo = pasta.name.replace("_", " ")
        itens.append((rotulo, pasta, trip))
    return itens


def encontrar_projeto_por_chave(
    contratante: str,
    natureza_servico: str,
    *,
    excluir_pasta: Path | None = None,
) -> Path | None:
    """Localiza pasta cujo trip tem a mesma chave (contratante + natureza)."""
    c = (contratante or "").strip().casefold()
    n = (natureza_servico or "").strip().casefold()
    if not c or not n:
        return None
    excluir = excluir_pasta.resolve() if excluir_pasta else None
    for _, pasta, trip in listar_projetos():
        if trip is None:
            continue
        if excluir is not None and pasta.resolve() == excluir:
            continue
        if (
            (trip.contratante or "").strip().casefold() == c
            and (trip.natureza_servico or "").strip().casefold() == n
        ):
            return pasta
    return None


def _rel_from_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _pasta_unica_para_slug(base_slug: str) -> Path:
    pasta = PASTA_PROJETOS / base_slug
    n = 1
    while pasta.exists():
        pasta = PASTA_PROJETOS / f"{base_slug}_{n}"
        n += 1
    return pasta


def criar_projeto(
    *,
    contratante: str,
    natureza_servico: str,
    empreendimento: str = "",
    endereco: str = "",
    cidade: str = "",
    estado: str = "",
    inicio_contratual: str | None = None,
    termino_contratual: str | None = None,
    contratada: str = "",
    nome_funcionario: str = "",
    telefone: str = "",
    area: str = "",
    numero_os: str = "",
    numero_rdv: str = "",
    centro_custo: str = "",
    adiantamento: float | None = None,
    slug: str | None = None,
) -> tuple[Trip, Path]:
    """Cria pasta do projeto (chave contratante + natureza) + projeto.json."""
    c = (contratante or "").strip()
    n = (natureza_servico or "").strip()
    if not c or not n:
        raise ValueError("Informe Contratante e Natureza do serviço.")

    garantir_pasta_projetos()
    existente = encontrar_projeto_por_chave(c, n)
    if existente is not None:
        raise ValueError(
            f"Já existe um projeto com esta chave:\n{existente.name}"
        )

    base_slug = slugify(slug) if slug else gerar_slug_chave(c, n)
    pasta = _pasta_unica_para_slug(base_slug)

    criar_estrutura_pastas(pasta)
    rel_raiz = _rel_from_root(pasta)
    trip = Trip(
        contratante=c,
        natureza_servico=n,
        empreendimento=(empreendimento or "").strip(),
        endereco=(endereco or "").strip(),
        cidade=(cidade or "").strip(),
        estado=(estado or "").strip(),
        inicio_contratual=inicio_contratual or None,
        termino_contratual=termino_contratual or None,
        contratada=(contratada or "").strip(),
        nome_funcionario=(nome_funcionario or "").strip(),
        telefone=(telefone or "").strip(),
        area=(area or "").strip(),
        numero_os=(numero_os or "").strip(),
        numero_rdv=(numero_rdv or "").strip(),
        centro_custo=(centro_custo or "").strip(),
        adiantamento=adiantamento,
        pasta_raiz=rel_raiz,
        pasta_notas=str(Path(rel_raiz) / "notas"),
        pasta_saida=str(Path(rel_raiz) / "output"),
    )
    salvar_projeto(trip)
    reg = load_registry()
    reg["ultimo_projeto"] = pasta.name
    if pasta.name not in reg["projetos"]:
        reg["projetos"].append(pasta.name)
    save_registry(reg)
    return trip, pasta


def _atualizar_registry_renomeacao(antigo: str, novo: str) -> None:
    reg = load_registry()
    if antigo in reg.get("projetos", []):
        reg["projetos"] = [p if p != antigo else novo for p in reg["projetos"]]
    if reg.get("ultimo_projeto") == antigo:
        reg["ultimo_projeto"] = novo
    if novo not in reg["projetos"]:
        reg["projetos"].append(novo)
    # Remove entradas órfãs do nome antigo
    reg["projetos"] = [p for p in reg["projetos"] if p != antigo or p == novo]
    save_registry(reg)


def _reatribuir_caminhos_apos_mover(
    trip: Trip,
    pasta_antiga: Path,
    pasta_nova: Path,
) -> None:
    """Atualiza paths relativos dos receipts após renomear/mover a pasta."""
    antigo_rel = _rel_from_root(pasta_antiga).replace("\\", "/")
    novo_rel = _rel_from_root(pasta_nova).replace("\\", "/")
    antigo_abs = str(pasta_antiga.resolve()).replace("\\", "/")
    novo_abs = str(pasta_nova.resolve()).replace("\\", "/")
    antigo_nome = pasta_antiga.name

    def _trocar(caminho: str) -> str:
        if not caminho:
            return caminho
        original = str(caminho)
        s = original.replace("\\", "/")
        if antigo_rel and antigo_rel in s:
            s = s.replace(antigo_rel, novo_rel, 1)
        elif antigo_abs and antigo_abs in s:
            s = s.replace(antigo_abs, novo_abs, 1)
        else:
            partes = list(Path(s).parts)
            if antigo_nome in partes:
                partes[partes.index(antigo_nome)] = pasta_nova.name
                s = "/".join(partes) if "/" in s else str(Path(*partes))
            else:
                return original
        # Preserva estilo de separador do caminho original
        if "\\" in original and "/" not in original.replace("://", ""):
            return s.replace("/", "\\")
        return s

    for r in trip.receipts:
        if r.arquivo:
            r.arquivo = _trocar(r.arquivo)
        if getattr(r, "arquivo_original", None):
            r.arquivo_original = _trocar(r.arquivo_original)


def _remover_pasta_com_retries(pasta: Path, tentativas: int = 8) -> None:
    import time

    ultimo: Exception | None = None
    for i in range(tentativas):
        try:
            shutil.rmtree(pasta)
            return
        except Exception as e:
            ultimo = e
            time.sleep(0.15 * (i + 1))
    if ultimo is not None:
        raise ultimo


def mover_pasta_projeto(origem: Path, destino: Path) -> tuple[Path, str | None]:
    """
    Renomeia a pasta do projeto.

    No Windows, se houver ficheiros bloqueados (preview, Explorer…),
    faz cópia e tenta apagar a origem. Devolve (destino, aviso_opcional).
    """
    origem = Path(origem).resolve()
    destino = Path(destino).resolve()
    if origem == destino:
        return destino, None
    if destino.exists():
        raise FileExistsError(f"Já existe a pasta de destino:\n{destino}")

    try:
        origem.rename(destino)
        return destino, None
    except OSError:
        pass

    try:
        shutil.copytree(origem, destino)
    except Exception:
        if destino.exists():
            shutil.rmtree(destino, ignore_errors=True)
        raise

    try:
        _remover_pasta_com_retries(origem)
        return destino, None
    except OSError:
        aviso = (
            "A pasta foi copiada para o novo nome, mas a pasta antiga não pôde ser apagada "
            f"(ficheiro em uso):\n{origem}\n\n"
            "Pode apagá-la manualmente no Explorador de ficheiros."
        )
        return destino, aviso


def atualizar_chave_projeto(
    trip: Trip,
    pasta_atual: Path,
    contratante: str,
    natureza_servico: str,
) -> tuple[Trip, Path, str | None]:
    """Atualiza a chave e renomeia a pasta do projeto se necessário.

    Devolve (trip, pasta, aviso_opcional).
    """
    c = (contratante or "").strip()
    n = (natureza_servico or "").strip()
    if not c or not n:
        raise ValueError("Informe Contratante e Natureza do serviço.")

    pasta_atual = Path(pasta_atual)
    if not pasta_atual.is_absolute():
        pasta_atual = (ROOT / pasta_atual).resolve()

    duplicado = encontrar_projeto_por_chave(c, n, excluir_pasta=pasta_atual)
    if duplicado is not None:
        raise ValueError(f"Já existe um projeto com esta chave:\n{duplicado.name}")

    trip.contratante = c
    trip.natureza_servico = n
    aviso: str | None = None

    novo_slug = gerar_slug_chave(c, n)
    nova_pasta = PASTA_PROJETOS / novo_slug
    if nova_pasta.resolve() != pasta_atual.resolve():
        if nova_pasta.exists():
            nova_pasta = _pasta_unica_para_slug(novo_slug)
        antigo_nome = pasta_atual.name
        pasta_antiga = pasta_atual
        pasta_atual, aviso = mover_pasta_projeto(pasta_antiga, nova_pasta)
        _reatribuir_caminhos_apos_mover(trip, pasta_antiga, pasta_atual)
        _atualizar_registry_renomeacao(antigo_nome, pasta_atual.name)

    trip.pasta_raiz = _rel_from_root(pasta_atual)
    trip.pasta_notas = _rel_from_root(pasta_atual / "notas")
    trip.pasta_saida = _rel_from_root(pasta_atual / "output")
    salvar_projeto(trip)
    marcar_ultimo_projeto(pasta_atual)
    return trip, pasta_atual, aviso


def salvar_projeto(trip: Trip) -> Path:
    if not trip.pasta_raiz:
        raise ValueError("Projeto sem pasta_raiz")
    raiz = resolve_path(trip.pasta_raiz)
    criar_estrutura_pastas(raiz)
    trip.pasta_notas = _rel_from_root(raiz / "notas")
    trip.pasta_saida = _rel_from_root(raiz / "output")
    trip.atualizado_em = datetime.now().isoformat(timespec="seconds")
    path = raiz / NOME_PROJETO_JSON
    with _SAVE_LOCK:
        atomic_write_json(path, trip.to_dict())
    return path


def carregar_projeto(pasta: Path | str) -> Trip:
    pasta = Path(pasta)
    if not pasta.is_absolute():
        pasta = (ROOT / pasta).resolve()
    json_path = pasta / NOME_PROJETO_JSON
    if not json_path.exists():
        criar_estrutura_pastas(pasta)
        trip = Trip(
            natureza_servico=pasta.name.replace("_", " "),
            pasta_raiz=_rel_from_root(pasta),
            pasta_notas=_rel_from_root(pasta / "notas"),
            pasta_saida=_rel_from_root(pasta / "output"),
        )
        salvar_projeto(trip)
        return trip
    try:
        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"O ficheiro do projeto está danificado e não pôde ser lido:\n{json_path}\n\n{e}"
        ) from e
    except OSError as e:
        raise ValueError(
            f"Não foi possível ler o projeto:\n{json_path}\n\n{e}"
        ) from e
    if not isinstance(raw, dict):
        raise ValueError(
            f"O ficheiro do projeto não tem um formato válido:\n{json_path}"
        )
    try:
        trip = Trip.from_dict(raw)
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(
            f"Não foi possível abrir o projeto:\n{json_path}\n\n{e}"
        ) from e
    trip.pasta_raiz = _rel_from_root(pasta)
    trip.pasta_notas = _rel_from_root(pasta / "notas")
    trip.pasta_saida = _rel_from_root(pasta / "output")
    return trip


def marcar_ultimo_projeto(slug_ou_pasta: str | Path) -> None:
    nome = Path(slug_ou_pasta).name
    reg = load_registry()
    reg["ultimo_projeto"] = nome
    if nome not in reg["projetos"]:
        reg["projetos"].append(nome)
    save_registry(reg)


def obter_projeto_inicial() -> tuple[Trip, Path] | None:
    """Último projeto usado, ou o primeiro da lista, ou None."""
    garantir_pasta_projetos()
    reg = load_registry()
    ultimo = (reg.get("ultimo_projeto") or "").strip()
    if ultimo:
        pasta = PASTA_PROJETOS / ultimo
        if pasta.is_dir():
            return carregar_projeto(pasta), pasta

    itens = listar_projetos()
    if itens:
        _, pasta, _ = itens[0]
        return carregar_projeto(pasta), pasta
    return None


def cabecalho_para_modelo(trip: Trip) -> dict[str, Any]:
    from app.schema import CAMPOS_MODELO_CABECALHO

    dados: dict[str, Any] = {}
    for campo in CAMPOS_MODELO_CABECALHO:
        valor = getattr(trip, campo, "") or ""
        if campo == "assinatura_arquivo" and valor:
            # Garante cópia em assets/assinaturas ligada ao nome do funcionário
            try:
                from app.assinaturas import (
                    caminho_absoluto,
                    salvar_assinatura_para_funcionario,
                )

                abs_path = caminho_absoluto(str(valor))
                if abs_path:
                    valor = salvar_assinatura_para_funcionario(
                        abs_path, trip.nome_funcionario or "funcionario"
                    )
            except Exception:
                pass
        dados[campo] = valor
    return dados


def salvar_modelo_cabecalho(trip: Trip) -> Path:
    ARQUIVO_MODELO_CABECALHO.parent.mkdir(parents=True, exist_ok=True)
    dados = cabecalho_para_modelo(trip)
    dados["meta"] = {"ultima_edicao_iso": datetime.now().isoformat()}
    atomic_write_json(ARQUIVO_MODELO_CABECALHO, dados)
    return ARQUIVO_MODELO_CABECALHO


def carregar_modelo_cabecalho() -> dict[str, Any]:
    candidatos = (
        ARQUIVO_MODELO_CABECALHO,
        bundled_asset("modelo_cabecalho.json"),
    )
    path = next((p for p in candidatos if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(
            f"Modelo de cabeçalho não encontrado:\n{ARQUIVO_MODELO_CABECALHO}"
        )
    with open(path, encoding="utf-8") as f:
        dados = json.load(f) or {}
    if not isinstance(dados, dict):
        raise ValueError("Modelo de cabeçalho inválido.")
    return dados


def migrar_dados_legado_se_preciso() -> tuple[Trip, Path] | None:
    """Migração única: notas/ ou output/projeto.json na raiz → projetos/Viagem_legado."""
    legado_json = ROOT / "output" / "projeto.json"
    notas_raiz = ROOT / "notas"
    tem_notas = notas_raiz.is_dir() and any(notas_raiz.iterdir())
    if not legado_json.exists() and not tem_notas:
        return None
    if (PASTA_PROJETOS / "Viagem_legado").exists():
        return None

    trip, pasta = criar_projeto(
        contratante="Legado",
        natureza_servico="Viagem (dados anteriores)",
        slug="Viagem_legado",
    )
    dest_notas = pasta / "notas"
    dest_out = pasta / "output"

    if legado_json.exists():
        try:
            with open(legado_json, encoding="utf-8") as f:
                antigo = Trip.from_dict(json.load(f))
            for campo in (
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
                "observacoes",
            ):
                valor = getattr(antigo, campo, None)
                if valor not in (None, ""):
                    setattr(trip, campo, valor)
            if not trip.contratante:
                trip.contratante = "Legado"
            if not trip.natureza_servico:
                trip.natureza_servico = "Viagem (dados anteriores)"
            trip.receipts = antigo.receipts
        except Exception:
            pass

    if tem_notas:
        for item in notas_raiz.iterdir():
            if item.name.startswith("."):
                continue
            alvo = dest_notas / item.name
            if item.is_dir():
                if not alvo.exists():
                    shutil.copytree(item, alvo)
            elif item.is_file() and not alvo.exists():
                shutil.copy2(item, alvo)

    novos = []
    for r in trip.receipts:
        arq = Path(r.arquivo)
        nome = arq.name
        candidatos = list(dest_notas.rglob(nome))
        if candidatos:
            try:
                r.arquivo = str(candidatos[0].resolve().relative_to(ROOT))
            except ValueError:
                r.arquivo = str(candidatos[0])
        novos.append(r)
    trip.receipts = novos
    trip.pasta_raiz = _rel_from_root(pasta)
    trip.pasta_notas = _rel_from_root(dest_notas)
    trip.pasta_saida = _rel_from_root(dest_out)
    salvar_projeto(trip)
    marcar_ultimo_projeto(pasta)
    return trip, pasta


def abrir_pasta_no_explorer(pasta: Path) -> None:
    import os
    import subprocess
    import sys

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(pasta))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(pasta)], check=False)
    else:
        subprocess.run(["xdg-open", str(pasta)], check=False)
