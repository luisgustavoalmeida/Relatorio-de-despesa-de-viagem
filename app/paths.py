"""Caminhos da aplicação: pasta do usuário (gravável) vs recursos empacotados.

Em desenvolvimento, ambos apontam para a raiz do repositório.
No .exe (PyInstaller), a pasta do executável é a área do usuário e os
recursos somente-leitura ficam em ``sys._MEIPASS`` (pasta ``_internal``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_FILE_ATTRIBUTE_HIDDEN = 0x02

# Relativos à pasta do usuário — o que o app cria/edita
_SEED_FILES: tuple[tuple[str, ...], ...] = (
    ("config.yaml",),
    ("assets", "tema_aplicacao.json"),
    ("assets", "modelo_cabecalho.json"),
    ("assets", "manual.json"),
    ("assets", "sobre.json"),
    ("assets", "rdv", "RDV-PADRÃO.xlsx"),
    ("assets", "rdv", "mapeamento_celulas.json"),
)

# Pastas internas: no .exe ficam ocultas no Explorer
_PASTAS_INTERNAS_EXE: tuple[str, ...] = ("_internal", "data", "assets")
_ARQUIVOS_INTERNOS_EXE: tuple[str, ...] = ("config.yaml",)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Recursos empacotados (somente leitura no .exe)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """Pasta gravável: ao lado do .exe, ou a raiz do repositório."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return user_dir() / "data"


def projetos_dir() -> Path:
    return user_dir() / "projetos"


def env_path() -> Path:
    """Chaves de API — dentro de data/, fora da vista no Explorer (pasta oculta no .exe)."""
    return data_dir() / ".env"


def bundled_asset(*parts: str) -> Path:
    return bundle_dir().joinpath("assets", *parts)


def user_asset(*parts: str) -> Path:
    return user_dir().joinpath("assets", *parts)


def asset_path(*parts: str) -> Path:
    """Cópia do usuário se existir; senão o arquivo empacotado; senão destino de gravação."""
    user = user_asset(*parts)
    if user.exists():
        return user
    bundled = bundled_asset(*parts)
    if bundled.exists():
        return bundled
    return user


def arquivo_manual_ajuda() -> Path:
    """Conteúdo do menu Ajuda → Manual (editável em assets/manual.json)."""
    return asset_path("manual.json")


def arquivo_sobre_ajuda() -> Path:
    """Conteúdo do menu Ajuda → Sobre (editável em assets/sobre.json)."""
    return asset_path("sobre.json")


def _copy_if_missing(origem: Path, destino: Path) -> None:
    if destino.exists() or not origem.is_file():
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(origem.read_bytes())


def _ocultar_windows(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    try:
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | _FILE_ATTRIBUTE_HIDDEN)
    except (AttributeError, OSError):
        pass


def _migrar_env_legado(raiz: Path) -> None:
    """Copia .env da raiz (versão antiga) para data/.env, sem apagar no modo fonte."""
    legado = raiz / ".env"
    destino = env_path()
    if destino.exists() or not legado.is_file():
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        destino.write_bytes(legado.read_bytes())
    except OSError:
        return
    if is_frozen():
        try:
            legado.unlink()
        except OSError:
            pass


def prepare_runtime() -> Path:
    """Cria pastas do usuário, copia modelos padrão e oculta o que não é para o usuário.

    Deve ser chamado no arranque, antes de ler config/settings.
    """
    raiz = user_dir()
    if is_frozen():
        try:
            os.chdir(raiz)
        except OSError:
            pass
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    projetos_dir().mkdir(parents=True, exist_ok=True)
    user_asset("assinaturas").mkdir(parents=True, exist_ok=True)
    user_asset("rdv").mkdir(parents=True, exist_ok=True)

    _migrar_env_legado(raiz)

    # Se um .env antigo ficou oculto (bug Windows), torna gravável de novo
    env = env_path()
    if env.is_file():
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(env))
            if attrs != -1 and (attrs & _FILE_ATTRIBUTE_HIDDEN):
                ctypes.windll.kernel32.SetFileAttributesW(
                    str(env), (attrs & ~_FILE_ATTRIBUTE_HIDDEN) | 0x80
                )
        except (AttributeError, OSError):
            pass

    for parts in _SEED_FILES:
        _copy_if_missing(bundle_dir().joinpath(*parts), raiz.joinpath(*parts))

    if is_frozen():
        for nome in _PASTAS_INTERNAS_EXE:
            _ocultar_windows(raiz / nome)
        for nome in _ARQUIVOS_INTERNOS_EXE:
            _ocultar_windows(raiz / nome)
        # Não ocultar data/.env individualmente: no Windows, arquivo oculto
        # não pode ser sobrescrito (PermissionError) e a chave de API se perde.

    return raiz
