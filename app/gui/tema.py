"""Paleta de cores, tipografia e alternância claro/escuro (mesmo padrão do Gerar_Relatorio)."""

from __future__ import annotations

from typing import Any, Literal

import customtkinter as ctk
import tkinter as tk

from app.paths import asset_path
from app.settings import load_user_settings, patch_user_settings

ModoAparencia = Literal["dark", "light"]
CorTema = tuple[str, str]  # (claro, escuro)

CHAVE_TEMA = "tema_aparencia"
ARQUIVO_TEMA_APLICACAO_JSON = asset_path("tema_aplicacao.json")
MODO_ATUAL: ModoAparencia = "dark"


def _par(claro: str, escuro: str) -> CorTema:
    return (claro, escuro)


# Cores adaptativas — cada constante vale para claro e escuro ao mesmo tempo
COR_PRIMARIA = _par("#1a73e8", "#1a73e8")
COR_PRIMARIA_HOVER = _par("#1557b0", "#1557b0")
COR_FUNDO = _par("#f4f4f8", "#1e1e2e")
COR_FUNDO_SECUNDARIO = _par("#eaeaef", "#252536")
COR_FUNDO_CARD = _par("#ffffff", "#2a2a3d")
COR_BORDA = _par("#c5c5d2", "#3d3d5c")
COR_TEXTO = _par("#1c1c28", "#e8e8f0")
COR_TEXTO_SECUNDARIO = _par("#5f5f78", "#9898b0")
COR_SUCESSO = _par("#188038", "#34a853")
COR_AVISO = _par("#e37400", "#fbbc04")
COR_ERRO = _par("#d93025", "#ea4335")
COR_TEXTO_BOTAO_ATIVO = _par("#ffffff", "#ffffff")

# Faixa de status das notas (claro, escuro)
COR_NOTA_SEM_EXTRAIR = _par("#5f6368", "#9aa0a6")
COR_NOTA_ERRO = _par("#d93025", "#f28b82")
COR_NOTA_SEM_VALOR = _par("#e37400", "#f29900")
COR_NOTA_SEM_DATA = _par("#f9ab00", "#fdd663")
COR_NOTA_ATENCAO = _par("#f9ab00", "#fdd663")
COR_NOTA_A_VERIFICAR = _par("#1a73e8", "#8ab4f8")
COR_NOTA_VERIFICADA = _par("#188038", "#81c995")
COR_NOTA_DUPLICATA = _par("#9334e6", "#c58af9")
COR_NOTA_EXCLUIDA = _par("#9aa0a6", "#80868b")

COR_NOTA_BG_SEM_EXTRAIR = _par("#e8eaed", "#3c4043")
COR_NOTA_BG_ERRO = _par("#fce8e6", "#5c1a16")
COR_NOTA_BG_SEM_VALOR = _par("#feefc3", "#4d3800")
COR_NOTA_BG_SEM_DATA = _par("#fef7e0", "#3d3000")
COR_NOTA_BG_ATENCAO = _par("#fef7e0", "#3d3000")
COR_NOTA_BG_A_VERIFICAR = _par("#e8f0fe", "#1a3a5c")
COR_NOTA_BG_VERIFICADA = _par("#e6f4ea", "#0d3b1e")
COR_NOTA_BG_DUPLICATA = _par("#f3e8fd", "#3c1e5c")
COR_NOTA_BG_EXCLUIDA = _par("#f1f3f4", "#3c4043")

# Dimensões e tipografia (Segoe UI — alinhado ao Gerar_Relatorio)
RAIO_BORDA = 10
LARGURA_JANELA = 1280
ALTURA_JANELA = 800
ALTURA_JANELA_MINIMA = 650

FONT_INTERFACE = ("Segoe UI", 14)
FONT_ABA = ("Segoe UI", 12, "bold")
FONT_DICA = ("Segoe UI", 11)
FONT_DICA_ABA = ("Segoe UI", 11)
FONT_TITULO = ("Segoe UI", 16, "bold")
FONT_PAINEL_TITULO = ("Segoe UI", 12, "bold")
FONT_CAMPO_SELECAO = ("Segoe UI", 14)

CORES_CATEGORIA = [
    "#1a73e8",
    "#188038",
    "#e37400",
    "#9334e6",
    "#d93025",
    "#00897b",
    "#f9ab00",
    "#5f6368",
    "#c2185b",
    "#3949ab",
]


def _indice_modo_aparencia() -> int:
    """Índice 0=claro, 1=escuro — alinhado ao CustomTkinter após troca de tema."""
    modo_ctk = ctk.get_appearance_mode()
    if modo_ctk == "Dark":
        return 1
    if modo_ctk == "Light":
        return 0
    return 1 if MODO_ATUAL == "dark" else 0


def resolver_cor(par: CorTema) -> str:
    """Devolve a cor do par (claro, escuro) conforme o modo atual."""
    return par[_indice_modo_aparencia()]


def carregar_tema_salvo() -> ModoAparencia:
    """Lê o tema salvo em user_settings.json; padrão: escuro."""
    try:
        modo = load_user_settings().get(CHAVE_TEMA, "dark")
        if modo in ("dark", "light"):
            return modo  # type: ignore[return-value]
    except Exception:
        pass
    return "dark"


def salvar_tema(modo: ModoAparencia) -> None:
    """Persiste a preferência de tema."""
    patch_user_settings({CHAVE_TEMA: modo})


def aplicar_tema(modo: ModoAparencia, persistir: bool = True) -> ModoAparencia:
    """Ativa claro/escuro com a paleta padrão da aplicação."""
    global MODO_ATUAL

    if modo not in ("dark", "light"):
        modo = "dark"

    MODO_ATUAL = modo
    ctk.set_appearance_mode(modo)
    if ARQUIVO_TEMA_APLICACAO_JSON.is_file():
        ctk.set_default_color_theme(str(ARQUIVO_TEMA_APLICACAO_JSON))
    else:
        ctk.set_default_color_theme("blue")

    if persistir:
        salvar_tema(modo)

    return modo


def inicializar_tema() -> ModoAparencia:
    """Carrega e aplica o tema salvo (ou escuro)."""
    return aplicar_tema(carregar_tema_salvo(), persistir=False)


def alternar_tema() -> ModoAparencia:
    """Alterna entre claro e escuro."""
    novo: ModoAparencia = "light" if MODO_ATUAL == "dark" else "dark"
    return aplicar_tema(novo)


def _modo_ctk() -> str:
    return "Dark" if MODO_ATUAL == "dark" else "Light"


def obter_cores_tema() -> dict[str, str]:
    """Paleta resolvida para widgets tk embutidos."""
    return {
        "fundo": resolver_cor(COR_FUNDO),
        "fundo_superior": resolver_cor(COR_FUNDO_SECUNDARIO),
        "entrada": resolver_cor(COR_FUNDO_CARD),
        "texto": resolver_cor(COR_TEXTO),
        "texto_discreto": resolver_cor(COR_TEXTO_SECUNDARIO),
        "borda": resolver_cor(COR_BORDA),
        "scroll": resolver_cor(COR_PRIMARIA),
        "scroll_trilho": resolver_cor(COR_FUNDO_SECUNDARIO),
        "destaque": resolver_cor(COR_PRIMARIA),
        "sucesso": resolver_cor(COR_SUCESSO),
        "aviso": resolver_cor(COR_AVISO),
        "erro": resolver_cor(COR_ERRO),
        "texto_botao": resolver_cor(COR_TEXTO_BOTAO_ATIVO),
    }


def cor_canvas_tk() -> str:
    return obter_cores_tema()["fundo"]


def opcoes_item_lista_suspensa(*, selecionado: bool = False) -> dict[str, Any]:
    """Estilo comum de hover/seleção para itens de menu e listas suspensas."""
    return {
        "fg_color": COR_FUNDO_SECUNDARIO if selecionado else "transparent",
        "hover_color": COR_PRIMARIA,
        "text_color": COR_TEXTO,
        "corner_radius": 6,
    }


def opcoes_tabview_ctk() -> dict[str, Any]:
    """Parâmetros visuais para ``CTkTabview`` (botões das abas em estilo pill)."""
    return {
        "corner_radius": RAIO_BORDA,
        "border_width": 1,
        "border_color": COR_BORDA,
        "fg_color": COR_FUNDO_CARD,
        "segmented_button_fg_color": COR_FUNDO,
        "segmented_button_selected_color": COR_PRIMARIA,
        "segmented_button_selected_hover_color": COR_PRIMARIA_HOVER,
        "segmented_button_unselected_color": COR_FUNDO_SECUNDARIO,
        "segmented_button_unselected_hover_color": COR_BORDA,
        "text_color": COR_TEXTO,
        "anchor": "nw",
    }


def configurar_abas_tabview(tabview: ctk.CTkTabview) -> None:
    """Aplica fonte, alinhamento e margens internas compactas às abas."""
    tabview._segmented_button.configure(font=FONT_ABA)
    for botao in tabview._segmented_button._buttons_dict.values():
        botao.configure(anchor="w")
    apertar_margens_internas_tabview(tabview)


def apertar_margens_internas_tabview(
    tabview: ctk.CTkTabview,
    *,
    padx: int = 4,
    pady_topo: int = 2,
    pady_base: int = 0,
) -> None:
    """Reduz o padx/pady interno que o CTkTabview copia do ``corner_radius``."""
    nome = getattr(tabview, "_current_name", None)
    if not nome:
        return
    aba = tabview._tab_dict.get(nome)
    if aba is None:
        return
    try:
        escala = tabview._apply_widget_scaling
        aba.grid_configure(
            padx=escala(padx),
            pady=(escala(pady_topo), escala(pady_base)),
        )
    except Exception:
        pass


def opcoes_campo_entrada_ctk(*, largura: int | None = None) -> dict[str, Any]:
    opcoes: dict[str, Any] = {
        "corner_radius": RAIO_BORDA,
        "border_width": 1,
        "border_color": COR_BORDA,
        "fg_color": COR_FUNDO_CARD,
        "text_color": COR_TEXTO,
        "font": FONT_INTERFACE,
    }
    if largura is not None:
        opcoes["width"] = largura
    return opcoes


def opcoes_combo_ctk(*, largura: int | None = None) -> dict[str, Any]:
    """Parâmetros visuais para ``CTkComboBox``."""
    opcoes: dict[str, Any] = {
        "corner_radius": RAIO_BORDA,
        "border_width": 1,
        "border_color": COR_BORDA,
        "fg_color": COR_FUNDO_CARD,
        "button_color": COR_FUNDO_SECUNDARIO,
        "button_hover_color": COR_BORDA,
        "dropdown_fg_color": COR_FUNDO_CARD,
        "dropdown_hover_color": COR_PRIMARIA,
        "dropdown_text_color": COR_TEXTO,
        "text_color": COR_TEXTO,
        "font": FONT_CAMPO_SELECAO,
        "dropdown_font": FONT_CAMPO_SELECAO,
    }
    if largura is not None:
        opcoes["width"] = largura
    return opcoes


def opcoes_caixa_texto_ctk(*, altura_px: int) -> dict[str, Any]:
    return {
        "height": altura_px,
        "corner_radius": RAIO_BORDA,
        "border_width": 1,
        "border_color": COR_BORDA,
        "fg_color": COR_FUNDO_CARD,
        "text_color": COR_TEXTO,
        "font": FONT_INTERFACE,
        "wrap": "word",
        "activate_scrollbars": True,
    }


def opcoes_texto_tk_embutido() -> dict[str, Any]:
    """Cores para ``tk.Text`` em diálogos (ajuda, etc.)."""
    cores = obter_cores_tema()
    return {
        "bg": cores["entrada"],
        "fg": cores["texto"],
        "insertbackground": cores["texto"],
        "selectbackground": cores["destaque"],
        "selectforeground": resolver_cor(COR_TEXTO_BOTAO_ATIVO),
        "highlightthickness": 1,
        "highlightbackground": cores["borda"],
        "highlightcolor": cores["destaque"],
        "borderwidth": 0,
    }


def _filhos_para_tema(janela: tk.Misc) -> list[tk.Misc]:
    filhos: list[tk.Misc] = []
    try:
        filhos.extend(janela.winfo_children())
    except Exception:
        pass

    canvas = getattr(janela, "_parent_canvas", None)
    if canvas is not None:
        try:
            filhos.extend(canvas.winfo_children())
        except Exception:
            pass
        _anexar_janelas_canvas(canvas, filhos)

    if type(janela).__name__ == "Canvas":
        _anexar_janelas_canvas(janela, filhos)

    return filhos


def _anexar_janelas_canvas(canvas: tk.Canvas, filhos: list[tk.Misc]) -> None:
    try:
        for item_id in canvas.find_all():
            if canvas.type(item_id) != "window":
                continue
            nome = canvas.itemcget(item_id, "window")
            if not nome:
                continue
            widget = canvas.nametowidget(nome)
            if widget not in filhos:
                filhos.append(widget)
    except Exception:
        pass


def _atualizar_scrollable_frame_tema(scroll: ctk.CTkScrollableFrame) -> None:
    modo = _modo_ctk()
    fundo = resolver_cor(COR_FUNDO)
    try:
        scroll._set_appearance_mode(modo)
    except Exception:
        pass
    try:
        scroll._parent_frame._set_appearance_mode(modo)
        scroll._parent_frame._draw()
        scroll._parent_canvas.configure(bg=fundo)
        tk.Frame.configure(scroll, bg=fundo)
        scroll._scrollbar._set_appearance_mode(modo)
        scroll._scrollbar._draw()
        for filho in scroll.winfo_children():
            if isinstance(filho, ctk.CTkBaseClass):
                filho.configure(bg_color="transparent")
                filho._draw()
    except Exception:
        pass


def _redesenhar_widget(widget: tk.Misc) -> None:
    modo = _modo_ctk()

    if type(widget).__name__ == "CTkScrollableFrame":
        _atualizar_scrollable_frame_tema(widget)
        return

    if isinstance(widget, ctk.CTkBaseClass):
        if hasattr(widget, "_set_appearance_mode"):
            try:
                widget._set_appearance_mode(modo)
            except Exception:
                pass

        if isinstance(widget, (ctk.CTkTextbox, ctk.CTkEntry)):
            try:
                widget.configure(
                    border_color=COR_BORDA,
                    fg_color=COR_FUNDO_CARD,
                    text_color=COR_TEXTO,
                )
            except Exception:
                pass
        elif isinstance(widget, ctk.CTkComboBox):
            try:
                widget.configure(**{
                    k: v
                    for k, v in opcoes_combo_ctk().items()
                    if k
                    not in (
                        "font",
                        "dropdown_font",
                        "width",
                    )
                })
            except Exception:
                pass
        elif isinstance(widget, ctk.CTkFrame):
            try:
                fg = widget.cget("fg_color")
                if fg in ("transparent", "Transparent", None):
                    widget.configure(bg_color="transparent")
            except Exception:
                pass
        elif isinstance(widget, ctk.CTkLabel):
            try:
                widget.configure(bg_color="transparent")
            except Exception:
                pass
        elif isinstance(widget, ctk.CTkTabview):
            try:
                widget.configure(**{k: v for k, v in opcoes_tabview_ctk().items() if k != "anchor"})
            except Exception:
                pass

        if hasattr(widget, "_draw"):
            try:
                widget._draw()
            except Exception:
                pass


def _atualizar_widget_tk_embutido(widget: tk.Misc) -> None:
    cores = obter_cores_tema()
    if isinstance(widget, tk.Canvas):
        try:
            # Canvas de prévia de nota: a App redefine a cor no hook refresh_apos_tema
            if getattr(widget, "_rdv_preview_canvas", False):
                return
            widget.configure(bg=cor_canvas_tk())
        except tk.TclError:
            pass
        return

    if isinstance(widget, tk.PanedWindow):
        try:
            widget.configure(
                bg=cores["fundo"],
                sashwidth=5,
                sashrelief="flat",
            )
        except tk.TclError:
            pass
        return

    if isinstance(widget, tk.Text):
        try:
            widget.configure(**opcoes_texto_tk_embutido())
        except tk.TclError:
            pass


def _percorrer_arvore_tema(janela: tk.Misc, visitados: set[int], processar: Any) -> None:
    wid = id(janela)
    if wid in visitados:
        return
    visitados.add(wid)

    for filho in _filhos_para_tema(janela):
        _percorrer_arvore_tema(filho, visitados, processar)

    processar(janela)


def forcar_redesenho_tema(janela: tk.Misc, visitados: set[int] | None = None) -> None:
    """Percorre a árvore e força redesenho CTk + tk embutidos após troca de tema."""
    if visitados is None:
        visitados = set()

    def _processar(widget: tk.Misc) -> None:
        _redesenhar_widget(widget)
        _atualizar_widget_tk_embutido(widget)

    _percorrer_arvore_tema(janela, visitados, _processar)

    hook = getattr(janela, "refresh_apos_tema", None)
    if callable(hook):
        try:
            hook()
        except Exception:
            pass
