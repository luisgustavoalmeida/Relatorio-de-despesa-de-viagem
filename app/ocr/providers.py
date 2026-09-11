"""Provedores de identificação imagem → texto (extensível)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from app.settings import (
    DEFAULT_MODELS,
    get_api_key,
    list_api_keys,
    mark_api_key_cooldown,
    next_api_key,
)


EXTRACTION_PROMPT = """Você extrai dados de COMPROVANTES FISCAIS brasileiros (NFC-e, cupom, recibo, fatura, pedágio, posto, estacionamento, restaurante).

Analise a imagem com atenção e responda APENAS um JSON válido (sem markdown, sem texto extra):
{{
  "valor": 123.45,
  "data": "YYYY-MM-DD",
  "hora": "HH:MM",
  "estabelecimento": "Nome fantasia ou razão social curta",
  "cnpj": "00000000000000",
  "categoria": "uma das categorias listadas",
  "moeda": "BRL",
  "confianca": 0.0
}}

Categorias permitidas (use EXATAMENTE uma):
{categorias}

REGRAS OBRIGATÓRIAS PARA O VALOR:
1. O campo "valor" deve ser o TOTAL PAGO / TOTAL A PAGAR / VALOR TOTAL / TOTAL DA NOTA / TOTAL GERAL / SUBTOTAL da nota.
2. Use número decimal com PONTO (ex.: 235.83). Nunca use vírgula no JSON.
3. NÃO use: troco, quantidade de litros, preço unitário (ex.: 6,79/L), ICMS isolado, desconto isolado, número da nota, CNPJ, CPF, chave de acesso.
4. Em postos/combustível, use o total da abastecida (ex.: "Valor Total R$ 235,83"), NÃO o preço por litro.
5. Em NFC-e o valor costuma ficar LONGE à direita do rótulo "Valor Total R$" / "Subtotal R$" / "VALOR PAGO" — leia a mesma linha visual até o número à direita.
6. Em pedágio/estacionamento/fatura, use o valor cobrado/total da fatura.
7. Se o mesmo valor aparecer várias vezes (item, subtotal, total, pago), esse é o total.
8. Só use null se o total estiver realmente ilegível.

REGRAS PARA ESTABELECIMENTO:
1. Use o NOME da empresa/loja no TOPO do cupom. Se houver "Loja: NOME", use só o NOME (ex.: "Loja: LAVUP ARARAQUARA - JARDIM..." → "LAVUP ARARAQUARA").
2. Em NFC-e o nome costuma vir na MESMA linha do CNPJ, DEPOIS do número.
3. NÃO use: CNPJ sozinho se o nome existir; CPF; IE; rua/avenida/CEP; e-mail (GMAIL/HOTMAIL); "CONSUMIDOR"; item (GASOLINA); data/hora.
4. Se o nome estiver ilegível, deixe estabelecimento como "" e preencha "cnpj" com os 14 dígitos (só números).
5. Campo "cnpj": 14 dígitos sem pontuação, ou null se ilegível.
6. Prefira nome curto (até ~60 caracteres).

REGRAS PARA DATA E HORA:
1. Data da compra/emissão do comprovante — procure a linha "Emissão:" / "Emissao:" (ex.: "Emissão: 27/04/2026 18:48:50").
2. Converta dd/mm/aaaa para YYYY-MM-DD (ex.: 27/04/2026 → "2026-04-27").
3. Campo "hora": horário de emissão/compra em HH:MM (ex.: 18:48). Ignore segundos. Se só houver HH:MM:SS, use HH:MM.
4. Copie o ano EXATAMENTE como impresso no cupom (mesmo que pareça futuro). NÃO substitua pelo ano atual.
5. Se houver várias datas, prefira a de emissão/venda (não a de validade de tributos).
6. Só use null em data/hora se estiver ilegível; se "Emissão: dd/mm/aaaa HH:MM" estiver visível, preencha ambos.

CATEGORIA: escolha a mais adequada ao tipo de despesa (combustível, pedágio, estacionamento, alimentação, locação, etc.).

confianca: 0.0 a 1.0 conforme a legibilidade da imagem.
"""


def parse_json_loose(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _com_meta_ia(
    parsed: dict[str, Any],
    *,
    prompt: str,
    resposta: str,
    provedor: str,
    modelo: str,
) -> dict[str, Any]:
    """Anexa a troca completa (texto) para a UI «Ver conversa com a IA»."""
    out = dict(parsed)
    out["ia_prompt"] = prompt
    out["ia_resposta"] = resposta
    out["ia_provedor"] = provedor
    out["ia_modelo"] = modelo
    return out


class VisionProvider(ABC):
    id: str = ""
    label: str = ""
    requires_api_key: bool = True

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def extract(self, jpeg_bytes: bytes, categorias: list[str], model: str | None = None) -> dict[str, Any]:
        ...


_RECEIPT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "valor": {"type": ["number", "null"]},
        "data": {"type": ["string", "null"]},
        "hora": {"type": ["string", "null"]},
        "estabelecimento": {"type": "string"},
        "cnpj": {"type": ["string", "null"]},
        "categoria": {"type": "string"},
        "moeda": {"type": "string"},
        "confianca": {"type": "number"},
    },
    "required": ["valor", "data", "estabelecimento", "categoria", "moeda", "confianca"],
}


def _gemini_client(api_key: str):
    """Cliente oficial google-genai (Interactions API / AI Studio)."""
    from google import genai

    return genai.Client(api_key=api_key)


def _gemini_output_text(interaction: Any) -> str:
    texto = getattr(interaction, "output_text", None)
    if texto:
        return str(texto)
    parts: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if isinstance(step, dict):
            content = step.get("content") or []
        else:
            content = getattr(step, "content", None) or []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
            elif getattr(block, "type", None) == "text" and getattr(block, "text", None):
                parts.append(str(block.text))
    return "\n".join(parts)


def _gemini_erro_transitorio(exc: BaseException) -> bool:
    texto = str(exc).lower()
    return any(
        s in texto
        for s in (
            "high demand",
            "try again later",
            "temporarily",
            "unavailable",
            "overloaded",
            "resource_exhausted",
            "429",
            "500",
            "503",
            "504",
            "api_error",
        )
    )


def _gemini_erro_quota(exc: BaseException) -> bool:
    """Quota / rate-limit: pular para outra chave do pool."""
    texto = str(exc).lower()
    return any(
        s in texto
        for s in (
            "429",
            "quota",
            "rate limit",
            "rate_limit",
            "resource_exhausted",
            "too many requests",
        )
    )


def _gemini_chamar_texto(client: Any, model: str, prompt: str) -> Any:
    return client.interactions.create(model=model, input=prompt)


def _gemini_chamar_imagem(
    client: Any,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    com_schema: bool,
) -> Any:
    entrada = [
        {"type": "text", "text": prompt},
        {"type": "image", "data": image_b64, "mime_type": "image/jpeg"},
    ]
    if com_schema:
        return client.interactions.create(
            model=model,
            input=entrada,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _RECEIPT_JSON_SCHEMA,
            },
        )
    return client.interactions.create(model=model, input=entrada)


_MAX_GEMINI_FALLBACK = 3
_GEMINI_MODELS_TTL = 30 * 60
_gemini_models_cache: dict[str, tuple[float, list[str]]] = {}
_gemini_models_lock = threading.Lock()


def _gemini_fila_tentativas(escolha: str | None, api_key: str) -> list[str]:
    from app.settings import (
        eh_modelo_automatico,
        fila_modelos_estaveis,
        normalizar_modelo,
        resolver_modelo_gemini,
    )

    pref = normalizar_modelo("gemini", escolha or "")
    if eh_modelo_automatico(pref) or not pref:
        lista, _ = listar_modelos_gemini(api_key)
        return fila_modelos_estaveis(lista or None)[:_MAX_GEMINI_FALLBACK]
    concreto = resolver_modelo_gemini(pref, api_key=api_key)
    fila = [concreto]
    for m in fila_modelos_estaveis():
        if m not in fila:
            fila.append(m)
        if len(fila) >= _MAX_GEMINI_FALLBACK:
            break
    return fila


class GeminiProvider(VisionProvider):
    id = "gemini"
    label = "Google Gemini"
    requires_api_key = True

    def is_available(self) -> bool:
        return bool(list_api_keys("gemini") or os.getenv("GOOGLE_API_KEY", "").strip())

    def extract(self, jpeg_bytes: bytes, categorias: list[str], model: str | None = None) -> dict[str, Any]:
        chaves = list_api_keys("gemini")
        if not chaves:
            fallback = (os.getenv("GOOGLE_API_KEY") or "").strip()
            if fallback:
                chaves = [fallback]
        if not chaves:
            raise RuntimeError("Nenhuma chave Gemini configurada.")

        prompt = EXTRACTION_PROMPT.format(categorias="\n".join(f"- {c}" for c in categorias))
        image_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        erros: list[str] = []
        n = len(chaves)

        for i_chave in range(n):
            api_key = next_api_key("gemini") or chaves[i_chave % n]
            client = _gemini_client(api_key)
            fila = _gemini_fila_tentativas(model, api_key)
            mascara = f"…{api_key[-4:]}" if len(api_key) >= 4 else "?"

            for model_name in fila:
                try:
                    try:
                        interaction = _gemini_chamar_imagem(
                            client, model_name, prompt, image_b64, com_schema=True
                        )
                    except Exception:
                        interaction = _gemini_chamar_imagem(
                            client, model_name, prompt, image_b64, com_schema=False
                        )
                    content = _gemini_output_text(interaction) or "{}"
                    return _com_meta_ia(
                        parse_json_loose(content),
                        prompt=prompt,
                        resposta=content,
                        provedor="gemini",
                        modelo=model_name,
                    )
                except Exception as exc:
                    erros.append(f"chave {mascara} / {model_name}: {exc}")
                    if _gemini_erro_quota(exc):
                        mark_api_key_cooldown("gemini", api_key)
                        break  # próxima chave do rodízio
                    if not _gemini_erro_transitorio(exc):
                        # Auth/modelo inválido: tenta outra chave se houver
                        if n > 1 and i_chave + 1 < n:
                            break
                        if len(fila) == 1:
                            raise
                    continue

        raise RuntimeError(
            "Nenhuma chave/modelo Gemini respondeu.\n" + "\n".join(erros[:6])
        )


class OpenAIProvider(VisionProvider):
    id = "openai"
    label = "OpenAI"
    requires_api_key = True

    def is_available(self) -> bool:
        return bool(get_api_key("openai"))

    def extract(self, jpeg_bytes: bytes, categorias: list[str], model: str | None = None) -> dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=get_api_key("openai"))
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        prompt = EXTRACTION_PROMPT.format(categorias="\n".join(f"- {c}" for c in categorias))
        model_name = model or DEFAULT_MODELS["openai"]
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        return _com_meta_ia(
            parse_json_loose(content),
            prompt=prompt,
            resposta=content,
            provedor="openai",
            modelo=model_name,
        )


class AnthropicProvider(VisionProvider):
    """Claude Vision — preparado para uso quando a chave/SDK estiverem disponíveis."""

    id = "anthropic"
    label = "Anthropic Claude"
    requires_api_key = True

    def is_available(self) -> bool:
        return bool(get_api_key("anthropic"))

    def extract(self, jpeg_bytes: bytes, categorias: list[str], model: str | None = None) -> dict[str, Any]:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "Pacote 'anthropic' não instalado. Execute: pip install anthropic"
            ) from e

        client = anthropic.Anthropic(api_key=get_api_key("anthropic"))
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        prompt = EXTRACTION_PROMPT.format(categorias="\n".join(f"- {c}" for c in categorias))
        model_name = model or DEFAULT_MODELS["anthropic"]
        resp = client.messages.create(
            model=model_name,
            max_tokens=800,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        content = "\n".join(parts) or "{}"
        return _com_meta_ia(
            parse_json_loose(content),
            prompt=prompt,
            resposta=content,
            provedor="anthropic",
            modelo=model_name,
        )


PROVIDER_REGISTRY: dict[str, VisionProvider] = {
    p.id: p
    for p in (
        GeminiProvider(),
        OpenAIProvider(),
        AnthropicProvider(),
    )
}


def get_provider(provider_id: str) -> VisionProvider | None:
    return PROVIDER_REGISTRY.get(provider_id)


_GEMINI_EXCLUIR_SUBSTR = (
    "tts",
    "image",
    "embedding",
    "lyria",
    "robotics",
    "computer-use",
    "deep-research",
    "antigravity",
    "transcribe",
    "customtools",
    "omni",
)


def _gemini_modelo_util_para_ocr(model_id: str) -> bool:
    """Filtra modelos de texto/visão úteis para ler notas (não TTS/imagem/música)."""
    m = (model_id or "").lower().strip()
    if not m.startswith("gemini-"):
        return False
    # Contas novas: Interactions API bloqueia família 2.x
    if m.startswith("gemini-2.") or m.startswith("gemini-1."):
        return False
    return not any(s in m for s in _GEMINI_EXCLUIR_SUBSTR)


def _ordenar_modelos_gemini(ids: list[str]) -> list[str]:
    preferencia = (
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-pro-latest",
        "gemini-3-flash-preview",
    )
    rank = {nome: i for i, nome in enumerate(preferencia)}

    def chave(mid: str) -> tuple[int, str]:
        return (rank.get(mid, 1000), mid)

    return sorted(ids, key=chave)


def listar_modelos_gemini(api_key: str) -> tuple[list[str], str | None]:
    """Consulta a API (models.list) e devolve IDs usáveis para OCR.

    Retorna ``(lista, erro_ou_None)``. Se a listagem falhar, ``lista`` fica vazia.
    Documentação: https://ai.google.dev/api/models
    """
    key = (api_key or "").strip()
    if not key:
        return [], "Informe a chave Gemini para listar os modelos."

    cache_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    agora = time.time()
    with _gemini_models_lock:
        hit = _gemini_models_cache.get(cache_key)
        if hit and agora - hit[0] < _GEMINI_MODELS_TTL and hit[1]:
            return hit[1], None

    try:
        client = _gemini_client(key)
        encontrados: list[str] = []
        for m in client.models.list():
            nome = getattr(m, "name", None) or ""
            if isinstance(nome, str) and nome.startswith("models/"):
                nome = nome[len("models/") :]
            actions = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            actions_l = {str(a).lower() for a in actions}
            # Precisa gerar conteúdo (visão/texto)
            if actions_l and "generatecontent" not in actions_l and "generate_content" not in actions_l:
                continue
            if _gemini_modelo_util_para_ocr(nome):
                encontrados.append(nome)
        # remove duplicatas preservando ordem preferida
        unicos = list(dict.fromkeys(encontrados))
        ordenados = _ordenar_modelos_gemini(unicos)
        if not ordenados:
            return [], "A API não retornou modelos de texto/visão compatíveis."
        with _gemini_models_lock:
            _gemini_models_cache[cache_key] = (time.time(), ordenados)
        return ordenados, None
    except Exception as exc:
        return [], f"Não foi possível listar modelos: {exc}"


def extrair_modelo_sugerido_erro(texto: str) -> str | None:
    """Lê sugestão 'use models/XXX' nas mensagens 404 da Gemini."""
    import re

    m = re.search(r"models/([a-zA-Z0-9._-]+)", texto or "")
    if not m:
        return None
    from app.settings import normalizar_modelo

    return normalizar_modelo("gemini", m.group(1))


def test_provider_connection(
    provider_id: str,
    api_key: str,
    model: str | None = None,
) -> tuple[bool, str]:
    """Valida chave + modelo com uma chamada mínima (sem imagem).

    Retorna ``(ok, mensagem_para_usuario)``.
    """
    key = (api_key or "").strip()
    if not key:
        return False, "Informe a chave de API antes de testar."

    model_name = (model or DEFAULT_MODELS.get(provider_id) or "").strip()
    if not model_name:
        return False, "Selecione um modelo para o teste."

    try:
        if provider_id == "gemini":
            return _test_gemini(key, model_name)
        if provider_id == "openai":
            return _test_openai(key, model_name)
        if provider_id == "anthropic":
            return _test_anthropic(key, model_name)
        return False, f"Provedor desconhecido: {provider_id}"
    except Exception as exc:
        return False, _mensagem_erro_api(provider_id, model_name, exc)


def _mensagem_erro_api(provider_id: str, model: str, exc: BaseException) -> str:
    texto = str(exc).strip() or type(exc).__name__
    if len(texto) > 320:
        texto = texto[:317] + "…"
    dica = ""
    low = texto.lower()
    if provider_id == "gemini":
        if "high demand" in low or "try again later" in low:
            dica = (
                "\n\nA Google reportou alta demanda nesse modelo (temporario). "
                "No modo Automatico o app tenta outro Flash estavel em seguida."
            )
        elif "429" in texto or "quota" in low or "rate" in low:
            dica = (
                "\n\nLimite/cota da chave atingido. Aguarde alguns minutos, "
                "adicione outra chave Gemini (rodízio automático), "
                "troque para um modelo Flash (ex.: gemini-3.6-flash) ou revise o plano em AI Studio."
            )
        elif "404" in texto or "no longer available" in low or "not found" in low:
            from app.settings import GEMINI_ESTAVEL_FALLBACK

            sugerido = extrair_modelo_sugerido_erro(texto) or GEMINI_ESTAVEL_FALLBACK
            dica = (
                f"\n\nEste modelo nao esta disponivel para sua conta. "
                f"Use «Atualizar lista» e escolha {sugerido} (ou Automatico)."
            )
    return f"Falha no teste ({provider_id} / {model}):\n{texto}{dica}"


def _test_gemini(api_key: str, model: str) -> tuple[bool, str]:
    from app.settings import eh_modelo_automatico

    preferencia = model
    fila = _gemini_fila_tentativas(model, api_key)
    # No teste automatico: tenta no maximo 3 modelos para nao demorar demais
    if eh_modelo_automatico(preferencia):
        fila = fila[:3]
    else:
        fila = fila[:1]

    client = _gemini_client(api_key)
    tentativas: list[str] = []
    ultimo_erro: Exception | None = None

    for model_name in fila:
        try:
            interaction = _gemini_chamar_texto(
                client, model_name, "Responda apenas com a palavra OK."
            )
            texto = (_gemini_output_text(interaction) or "").strip()
            prefixo = ""
            if eh_modelo_automatico(preferencia):
                prefixo = f"Modo automatico -> {model_name}\n"
                if tentativas:
                    prefixo += "Alternativas tentadas: " + ", ".join(tentativas) + "\n"
            if texto:
                return True, f"{prefixo}Conexao OK com Gemini ({model_name}).\nResposta: {texto[:80]}"
            return True, f"{prefixo}Conexao OK com Gemini ({model_name})."
        except Exception as exc:
            ultimo_erro = exc
            tentativas.append(f"{model_name} (falhou)")
            if not _gemini_erro_transitorio(exc):
                break
            continue

    if ultimo_erro is not None:
        return False, _mensagem_erro_api("gemini", model, ultimo_erro)
    return False, "Falha no teste Gemini sem detalhe."


def _test_openai(api_key: str, model: str) -> tuple[bool, str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Responda apenas com a palavra OK."}],
        temperature=0,
        max_tokens=16,
    )
    texto = (resp.choices[0].message.content or "").strip()
    return True, f"Conexão OK com OpenAI ({model}).\nResposta: {texto[:80] or '(vazia)'}"


def _test_anthropic(api_key: str, model: str) -> tuple[bool, str]:
    try:
        import anthropic
    except ImportError:
        return False, "Pacote 'anthropic' não instalado.\nExecute: pip install anthropic"

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=16,
        temperature=0,
        messages=[{"role": "user", "content": "Responda apenas com a palavra OK."}],
    )
    partes = []
    for block in resp.content:
        if hasattr(block, "text"):
            partes.append(block.text)
    texto = "\n".join(partes).strip()
    return True, f"Conexão OK com Anthropic ({model}).\nResposta: {texto[:80] or '(vazia)'}"
