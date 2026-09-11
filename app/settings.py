from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.atomic_io import atomic_write_json, atomic_write_text
from app.config import DATA_DIR, ensure_data_dir
from app.paths import env_path, is_frozen

SETTINGS_PATH = DATA_DIR / "user_settings.json"

# Motores disponíveis na interface (id -> rótulo)
MOTORS: list[tuple[str, str]] = [
    ("local", "OCR local (gratuito / offline)"),
    ("gemini", "Google Gemini (IA)"),
    ("openai", "OpenAI GPT (IA)"),
    ("anthropic", "Anthropic Claude (IA)"),
    ("auto", "Automático (IA se houver chave, senão local)"),
]

# Qual variável de ambiente guarda a chave de cada provedor de IA
PROVIDER_API_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Lista completa de chaves Gemini (separadas por |) — rodízio entre elas
GEMINI_API_KEYS_ENV = "GEMINI_API_KEYS"
_GEMINI_KEYS_SEP = "|"

# Preferência «Automático»: escolhe o Flash estável em runtime
MODELO_GEMINI_AUTOMATICO = "automatico"
ROTULO_GEMINI_AUTOMATICO = "Automático (mais estável)"

# Modelos padrão por provedor (gemini = automático)
DEFAULT_MODELS: dict[str, str] = {
    "gemini": MODELO_GEMINI_AUTOMATICO,
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
}

# Usado quando o modo Automático não consegue consultar a API
GEMINI_ESTAVEL_FALLBACK = "gemini-3.6-flash"

# Ordem do modo Automático: IDs concretos estáveis primeiro.
# Evita aliases *-latest (costumam ficar sobrecarregados / erro 500).
ORDEM_ESTAVEL_GEMINI: tuple[str, ...] = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    # aliases por último (menos previsíveis sob carga)
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
)

# Modelos Gemini retirados / bloqueados para contas novas → substituto
_GEMINI_MODELOS_SUBSTITUTOS: dict[str, str] = {
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-2.0-flash-001": "gemini-3.6-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.0-flash-lite-001": "gemini-3.5-flash-lite",
    "gemini-2.5-flash": "gemini-3.6-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-1.5-flash": "gemini-3.6-flash",
    "gemini-1.5-flash-001": "gemini-3.6-flash",
    "gemini-1.5-flash-002": "gemini-3.6-flash",
    "gemini-1.5-pro": "gemini-3.1-pro-preview",
    "gemini-1.5-pro-001": "gemini-3.1-pro-preview",
    "gemini-1.5-pro-002": "gemini-3.1-pro-preview",
    "gemini-pro": "gemini-3.6-flash",
    "gemini-pro-vision": "gemini-3.6-flash",
}

# Fallback offline se a API de listagem falhar (só família 3.x estáveis)
AVAILABLE_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-3.1-pro-preview",
        "gemini-pro-latest",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4.1-nano",
        "o4-mini",
        "o3-mini",
    ],
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-haiku-20240307",
    ],
}

_MODELO_SETTINGS_KEY: dict[str, str] = {
    "gemini": "modelo_gemini",
    "openai": "modelo_openai",
    "anthropic": "modelo_anthropic",
}


def eh_modelo_automatico(modelo: str | None) -> bool:
    m = (modelo or "").strip().lower()
    return m in {
        MODELO_GEMINI_AUTOMATICO,
        "auto",
        ROTULO_GEMINI_AUTOMATICO.lower(),
    }


def rotulo_modelo_combo(provider: str, modelo_id: str) -> str:
    """Texto exibido no combo (Automático → rótulo amigável)."""
    if provider == "gemini" and eh_modelo_automatico(modelo_id):
        return ROTULO_GEMINI_AUTOMATICO
    return (modelo_id or "").strip()


def id_modelo_combo(provider: str, texto: str) -> str:
    """Converte texto do combo de volta para o id gravado."""
    t = (texto or "").strip()
    if provider == "gemini" and (eh_modelo_automatico(t) or t == ROTULO_GEMINI_AUTOMATICO):
        return MODELO_GEMINI_AUTOMATICO
    return t


def escolher_modelo_estavel(candidatos: list[str] | None = None) -> str:
    """Escolhe o modelo concreto mais estável entre os candidatos (ou fallback)."""
    return fila_modelos_estaveis(candidatos)[0]


def fila_modelos_estaveis(candidatos: list[str] | None = None) -> list[str]:
    """Fila ordenada para o modo Automático (tenta o próximo se um falhar)."""
    cand = [c for c in (candidatos or list(AVAILABLE_MODELS.get("gemini") or [])) if c]
    cand = [c for c in cand if c not in _GEMINI_MODELOS_SUBSTITUTOS]
    ordenados: list[str] = []
    for pref in ORDEM_ESTAVEL_GEMINI:
        if pref in cand and pref not in ordenados:
            ordenados.append(pref)
    for c in cand:
        if c not in ordenados:
            ordenados.append(c)
    return ordenados or [GEMINI_ESTAVEL_FALLBACK]


def resolver_modelo_gemini(
    escolha: str | None,
    *,
    candidatos: list[str] | None = None,
    api_key: str | None = None,
) -> str:
    """Converte preferência (incl. automático) em ID concreto de modelo.

    Se ``api_key`` for passada e a escolha for automática, tenta listar na API.
    """
    pref = normalizar_modelo("gemini", escolha or MODELO_GEMINI_AUTOMATICO)
    if not eh_modelo_automatico(pref):
        return pref

    lista = list(candidatos) if candidatos else None
    if lista is None and api_key:
        try:
            from app.ocr.providers import listar_modelos_gemini

            lista, _erro = listar_modelos_gemini(api_key)
        except Exception:
            lista = None
    return escolher_modelo_estavel(lista)


def modelos_para_combo(provider: str, atual: str | None = None, lista_api: list[str] | None = None) -> list[str]:
    """Itens do combo (rótulos). Gemini inclui «Automático» no topo."""
    if provider == "gemini":
        base = list(lista_api) if lista_api else list(AVAILABLE_MODELS.get("gemini") or [])
        # Remove ids legados / automático duplicado da lista concreta
        base = [
            m
            for m in base
            if m and not eh_modelo_automatico(m) and m not in _GEMINI_MODELOS_SUBSTITUTOS
        ]
        atual_id = id_modelo_combo("gemini", atual or DEFAULT_MODELS["gemini"])
        if atual_id and not eh_modelo_automatico(atual_id) and atual_id not in base:
            # Ainda mostra o salvo se for concreto e não estiver na lista
            if atual_id not in _GEMINI_MODELOS_SUBSTITUTOS:
                base.insert(0, atual_id)
            else:
                atual_id = MODELO_GEMINI_AUTOMATICO
        valores = [ROTULO_GEMINI_AUTOMATICO] + base
        return valores

    base = list(AVAILABLE_MODELS.get(provider) or [])
    atual_n = normalizar_modelo(provider, atual or DEFAULT_MODELS.get(provider) or "")
    if atual_n and atual_n not in base:
        base.insert(0, atual_n)
    if not base and atual_n:
        base = [atual_n]
    return base


def normalizar_modelo(provider: str, modelo: str) -> str:
    """Preserva «automatico»; troca IDs descontinuados pelo substituto."""
    m = (modelo or "").strip()
    if provider == "gemini":
        if not m or eh_modelo_automatico(m):
            return MODELO_GEMINI_AUTOMATICO
        if m.startswith("models/"):
            m = m[len("models/") :]
        m = _GEMINI_MODELOS_SUBSTITUTOS.get(m, m)
        return m
    if not m:
        return DEFAULT_MODELS.get(provider, "")
    return m


def settings_key_modelo(provider: str) -> str:
    return _MODELO_SETTINGS_KEY.get(provider, f"modelo_{provider}")


_FILE_ATTRIBUTE_HIDDEN = 0x02
_FILE_ATTRIBUTE_NORMAL = 0x80


def _env_file() -> Path:
    """Caminho atual do .env (sempre dinâmico — importante no .exe)."""
    return env_path()


def _set_hidden_windows(path: Path, hidden: bool) -> None:
    if os.name != "nt" or not path.exists():
        return
    try:
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return
        if hidden:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | _FILE_ATTRIBUTE_HIDDEN)
        else:
            ctypes.windll.kernel32.SetFileAttributesW(
                str(path), (attrs & ~_FILE_ATTRIBUTE_HIDDEN) | _FILE_ATTRIBUTE_NORMAL
            )
    except (AttributeError, OSError):
        pass


def _ensure_env_loaded() -> None:
    from app.paths import user_dir

    caminho = _env_file()
    legado = user_dir() / ".env"
    if legado.is_file() and legado.resolve() != caminho.resolve():
        load_dotenv(legado, override=False)
    load_dotenv(caminho, override=True)


def read_env_file() -> dict[str, str]:
    """Lê KEY=VALUE diretamente do arquivo (não depende só de os.environ)."""
    data: dict[str, str] = {}
    caminho = _env_file()
    if not caminho.is_file():
        legado = __import__("app.paths", fromlist=["user_dir"]).user_dir() / ".env"
        if legado.is_file():
            caminho = legado
        else:
            return data
    try:
        text = caminho.read_text(encoding="utf-8")
    except OSError:
        return data
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def write_env_file(updates: dict[str, str | None]) -> None:
    """Atualiza chaves no .env preservando o restante do arquivo.

    No Windows, arquivos ocultos não podem ser sobrescritos — remove o atributo
    antes de gravar e volta a ocultar no .exe.
    """
    caminho = _env_file()
    caminho.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if caminho.exists():
        _set_hidden_windows(caminho, False)
        try:
            lines = caminho.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                val = updates[key]
                if val is None:
                    continue  # remove
                new_lines.append(f"{key}={val}")
                seen.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key in seen or val is None:
            continue
        new_lines.append(f"{key}={val}")

    text = "\n".join(new_lines).rstrip() + "\n"
    atomic_write_text(caminho, text)

    # Só oculta o arquivo no .exe (pasta data/ já fica menos visível)
    if is_frozen():
        _set_hidden_windows(caminho, True)

    # Atualiza os.environ imediatamente
    for key, val in updates.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    _ensure_env_loaded()


def get_api_key(provider: str) -> str:
    """Retorna uma chave disponível (Gemini: primeira fora de cooldown; sem avançar rodízio)."""
    keys = list_api_keys(provider)
    if not keys:
        if provider == "gemini":
            return (os.getenv("GOOGLE_API_KEY") or "").strip()
        return ""
    if provider != "gemini":
        return keys[0]
    agora = time.time()
    with _gemini_rr_lock:
        for k in keys:
            if _gemini_cooldown.get(k, 0.0) <= agora:
                return k
        return keys[0]


def list_api_keys(provider: str) -> list[str]:
    """Lista todas as chaves salvas (Gemini suporta várias; demais = 0 ou 1)."""
    if provider == "gemini":
        return _list_gemini_keys()
    k = ""
    env_name = PROVIDER_API_ENV.get(provider)
    if env_name:
        k = (read_env_file().get(env_name) or "").strip()
        if not k:
            _ensure_env_loaded()
            k = (os.getenv(env_name) or "").strip()
    return [k] if k else []


def _list_gemini_keys() -> list[str]:
    """Lê GEMINI_API_KEYS e/ou GEMINI_API_KEY (legado)."""
    data = read_env_file()
    bruto = (data.get(GEMINI_API_KEYS_ENV) or os.getenv(GEMINI_API_KEYS_ENV) or "").strip()
    primaria = (data.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    google = (data.get("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

    chaves: list[str] = []
    if bruto:
        for parte in bruto.replace("\n", _GEMINI_KEYS_SEP).split(_GEMINI_KEYS_SEP):
            k = parte.strip()
            if k and k not in chaves:
                chaves.append(k)
    for extra in (primaria, google):
        if extra and extra not in chaves:
            chaves.append(extra)
    return chaves


def save_api_keys(provider: str, keys: list[str]) -> None:
    """Persiste lista de chaves. Gemini: rodízio; demais: só a primeira."""
    limpas = []
    for k in keys:
        k = (k or "").strip()
        if k and k not in limpas:
            limpas.append(k)

    if provider == "gemini":
        updates: dict[str, str | None] = {}
        if limpas:
            updates["GEMINI_API_KEY"] = limpas[0]
            updates[GEMINI_API_KEYS_ENV] = _GEMINI_KEYS_SEP.join(limpas)
        else:
            updates["GEMINI_API_KEY"] = None
            updates[GEMINI_API_KEYS_ENV] = None
        write_env_file(updates)
        _reset_gemini_rotation(limpas)
        return

    env_name = PROVIDER_API_ENV.get(provider)
    if not env_name:
        return
    write_env_file({env_name: limpas[0] if limpas else None})


_gemini_rr_lock = threading.Lock()
_gemini_rr_index = 0
_gemini_cooldown: dict[str, float] = {}  # chave -> timestamp até quando evitar
_GEMINI_COOLDOWN_SECS = 60.0
_gemini_thread_pref = threading.local()


def _reset_gemini_rotation(keys: list[str] | None = None) -> None:
    global _gemini_rr_index
    with _gemini_rr_lock:
        _gemini_rr_index = 0
        if keys is not None:
            vivos = set(keys)
            for k in list(_gemini_cooldown.keys()):
                if k not in vivos:
                    _gemini_cooldown.pop(k, None)


def mark_api_key_cooldown(provider: str, key: str, seconds: float | None = None) -> None:
    """Marca chave como em cooldown (ex.: quota 429) para o rodízio pular."""
    if provider != "gemini" or not key:
        return
    até = time.time() + (seconds if seconds is not None else _GEMINI_COOLDOWN_SECS)
    with _gemini_rr_lock:
        _gemini_cooldown[key] = até


def next_api_key(provider: str) -> str:
    """Próxima chave do rodízio (Gemini). Outros provedores: única chave.

    Se a thread tiver preferência (uso paralelo), tenta essa chave primeiro.
    """
    if provider != "gemini":
        keys = list_api_keys(provider)
        return keys[0] if keys else ""

    keys = _list_gemini_keys()
    if not keys:
        return (os.getenv("GOOGLE_API_KEY") or "").strip()
    agora = time.time()
    preferred = getattr(_gemini_thread_pref, "key", None)

    with _gemini_rr_lock:
        global _gemini_rr_index
        if preferred and preferred in keys:
            ate = _gemini_cooldown.get(preferred, 0.0)
            if ate <= agora:
                _gemini_cooldown.pop(preferred, None)
                os.environ["GEMINI_API_KEY"] = preferred
                return preferred

        n = len(keys)
        for _ in range(n):
            idx = _gemini_rr_index % n
            _gemini_rr_index = idx + 1
            candidata = keys[idx]
            ate = _gemini_cooldown.get(candidata, 0.0)
            if ate <= agora:
                _gemini_cooldown.pop(candidata, None)
                os.environ["GEMINI_API_KEY"] = candidata
                return candidata
        melhor = min(keys, key=lambda k: _gemini_cooldown.get(k, 0.0))
        os.environ["GEMINI_API_KEY"] = melhor
        return melhor


def prefer_api_key(provider: str, key: str | None) -> None:
    """Atribui chave preferida à thread atual (extração paralela)."""
    if provider != "gemini":
        return
    if key:
        _gemini_thread_pref.key = key
    elif hasattr(_gemini_thread_pref, "key"):
        delattr(_gemini_thread_pref, "key")


def clear_preferred_api_key(provider: str = "gemini") -> None:
    prefer_api_key(provider, None)


def parallel_extraction_workers(motor: str | None = None) -> int:
    """Quantos workers paralelos usar na extração em lote.

    Gemini / auto (com chaves Gemini): 1 worker por chave (até o nº de notas).
    Demais motores: 1 (evita estourar cota de chave única / RapidOCR).
    """
    if motor is None:
        motor = (load_user_settings().get("motor") or "local").strip().lower()
    else:
        motor = (motor or "local").strip().lower()

    if motor == "gemini":
        n = len(list_api_keys("gemini"))
        return max(1, min(n or 1, 16))
    if motor in {"auto", "visao", "vision"}:
        n = len(list_api_keys("gemini"))
        if n >= 2:
            return min(n, 16)
        return 1
    return 1


def peek_api_keys_status(provider: str = "gemini") -> str:
    """Resumo curto para a UI."""
    keys = list_api_keys(provider)
    if not keys:
        return "Nenhuma chave salva"
    if len(keys) == 1:
        return f"1 chave: {mask_api_key(keys[0])}"
    return (
        f"{len(keys)} chaves — uso em paralelo e rodízio "
        f"({mask_api_key(keys[0])} …)"
    )

def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "…" + key[-4:]


def load_user_settings() -> dict[str, Any]:
    import json

    ensure_data_dir()
    _ensure_env_loaded()
    # Fonte de verdade do motor: user_settings.json (não o .env)
    defaults = {
        "motor": "local",
        "modelo_gemini": DEFAULT_MODELS["gemini"],
        "modelo_openai": DEFAULT_MODELS["openai"],
        "modelo_anthropic": DEFAULT_MODELS["anthropic"],
        "tema_aparencia": "dark",
        "layout_notas": {"sash0": 0.28, "sash1": 0.72},
    }
    if not SETTINGS_PATH.exists():
        # Primeira execução: se o .env antigo já tinha motor, aproveita
        env_motor = (os.getenv("OCR_MOTOR") or "").strip().lower()
        if env_motor:
            defaults["motor"] = env_motor
        return dict(defaults)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return dict(defaults)
    merged = dict(defaults)
    merged.update({k: v for k, v in data.items() if v is not None})
    # Migra modelos Gemini descontinuados (ex.: gemini-2.0-flash → gemini-3.6-flash)
    antigo = str(merged.get("modelo_gemini") or "")
    novo = normalizar_modelo("gemini", antigo)
    if novo and novo != antigo:
        merged["modelo_gemini"] = novo
        try:
            atomic_write_json(SETTINGS_PATH, merged)
        except OSError:
            pass
    # Garante layout com defaults
    layout = merged.get("layout_notas") or {}
    if not isinstance(layout, dict):
        layout = {}
    merged["layout_notas"] = {
        "sash0": float(layout.get("sash0", 0.28)),
        "sash1": float(layout.get("sash1", 0.72)),
    }
    return merged


def persist_motor(motor: str) -> None:
    """Grava o motor em user_settings.json e sincroniza OCR_MOTOR no .env."""
    motor = (motor or "local").strip().lower() or "local"
    patch_user_settings({"motor": motor})
    write_env_file(
        {
            "OCR_MOTOR": motor,
            "VISION_PROVIDER": motor if motor in PROVIDER_API_ENV else "auto",
        }
    )


def patch_user_settings(updates: dict[str, Any]) -> None:
    """Atualiza chaves em user_settings.json sem mexer no .env."""
    ensure_data_dir()
    settings = load_user_settings()
    settings.update(updates)
    atomic_write_json(SETTINGS_PATH, settings)


def save_user_settings(
    *,
    motor: str,
    api_keys: dict[str, str] | None = None,
    api_keys_lists: dict[str, list[str]] | None = None,
    modelos: dict[str, str] | None = None,
) -> None:
    """Salva motor/modelos em JSON e chaves de API no .env."""
    ensure_data_dir()
    settings = load_user_settings()
    settings["motor"] = motor
    if modelos:
        for k, v in modelos.items():
            if not v:
                continue
            if k == "modelo_gemini" or k.endswith("_gemini"):
                settings[k] = normalizar_modelo("gemini", v)
            else:
                settings[k] = v

    atomic_write_json(SETTINGS_PATH, settings)

    env_updates: dict[str, str | None] = {
        "OCR_MOTOR": motor,
        "VISION_PROVIDER": motor if motor in PROVIDER_API_ENV else "auto",
    }
    write_env_file(env_updates)

    if api_keys_lists:
        for provider, keys in api_keys_lists.items():
            save_api_keys(provider, keys)

    if api_keys:
        for provider, key in api_keys.items():
            if api_keys_lists and provider in api_keys_lists:
                continue
            key = (key or "").strip()
            if not key:
                continue
            if provider == "gemini":
                atuais = list_api_keys("gemini")
                if key not in atuais:
                    atuais.append(key)
                save_api_keys("gemini", atuais or [key])
            else:
                save_api_keys(provider, [key])


def apply_settings_to_config(config: dict[str, Any]) -> dict[str, Any]:
    """Mescla preferências do usuário no config em memória."""
    settings = load_user_settings()
    config.setdefault("ocr", {})
    config.setdefault("visao", {})
    config["ocr"]["motor"] = settings.get("motor") or config["ocr"].get("motor") or "local"
    config["visao"]["provedor"] = config["ocr"]["motor"]
    config["visao"]["modelo_gemini"] = normalizar_modelo(
        "gemini", settings.get("modelo_gemini") or DEFAULT_MODELS["gemini"]
    )
    config["visao"]["modelo_openai"] = settings.get("modelo_openai") or DEFAULT_MODELS["openai"]
    config["visao"]["modelo_anthropic"] = settings.get("modelo_anthropic") or DEFAULT_MODELS["anthropic"]
    return config


def motor_label(motor_id: str) -> str:
    for mid, label in MOTORS:
        if mid == motor_id:
            return label
    return motor_id
