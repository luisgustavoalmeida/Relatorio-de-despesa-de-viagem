from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageTk

from app.ajuda_conteudo import (
    carregar_documento_ajuda,
    configurar_tags_texto_ajuda,
    preencher_widget_manual,
    preencher_widget_sobre,
)
from app.assinaturas import (
    caminho_absoluto,
    caminho_relativo,
    garantir_pasta_assinaturas,
    resolver_assinatura,
    salvar_assinatura_para_funcionario,
)
from app.categorias_usuario import categorias_completas
from app.config import ROOT, load_config, resolve_path
from app.paths import arquivo_manual_ajuda, arquivo_sobre_ajuda
from app.duplicates import (
    atualizar_hash_conteudo,
    ids_duplicados,
    ids_marcacao_duplicata,
    indice_duplicatas,
    motivo_duplicata,
    normalizar_hora,
    resumo_duplicatas,
)
from app.gui.categorias_dialog import CategoriasDialog
from app.gui.ia_conversa_dialog import IaConversaDialog
from app.gui.menu_barra import EntradaMenuBarra, criar_barra_menu_ctk
from app.gui.rdv_modelo_dialog import MapeamentoRdvDialog
from app.gui.settings_dialog import SettingsDialog
from app.reports.excel_export import PASTA_MODELO_RDV, _template_path, reload_mapeamento_rdv
from app.gui.tema import (
    CORES_CATEGORIA,
    COR_ERRO,
    COR_FUNDO_CARD,
    COR_FUNDO_SECUNDARIO,
    COR_NOTA_A_VERIFICAR,
    COR_NOTA_ATENCAO,
    COR_NOTA_BG_A_VERIFICAR,
    COR_NOTA_BG_ATENCAO,
    COR_NOTA_BG_DUPLICATA,
    COR_NOTA_BG_ERRO,
    COR_NOTA_BG_EXCLUIDA,
    COR_NOTA_BG_SEM_DATA,
    COR_NOTA_BG_SEM_EXTRAIR,
    COR_NOTA_BG_SEM_VALOR,
    COR_NOTA_BG_VERIFICADA,
    COR_NOTA_DUPLICATA,
    COR_NOTA_ERRO,
    COR_NOTA_EXCLUIDA,
    COR_NOTA_SEM_DATA,
    COR_NOTA_SEM_EXTRAIR,
    COR_NOTA_SEM_VALOR,
    COR_NOTA_VERIFICADA,
    COR_PRIMARIA,
    COR_PRIMARIA_HOVER,
    COR_SUCESSO,
    COR_TEXTO,
    COR_TEXTO_SECUNDARIO,
    FONT_DICA,
    FONT_INTERFACE,
    FONT_TITULO,
    RAIO_BORDA,
    alternar_tema,
    configurar_abas_tabview,
    forcar_redesenho_tema,
    inicializar_tema,
    opcoes_caixa_texto_ctk,
    opcoes_campo_entrada_ctk,
    opcoes_combo_ctk,
    opcoes_tabview_ctk,
    opcoes_texto_tk_embutido,
    resolver_cor,
)
from app.models.receipt import Receipt, ReceiptStatus
from app.models.trip import Trip
from app.ocr.extractor import extract_receipt
from app.projects import (
    abrir_pasta_no_explorer,
    atualizar_chave_projeto,
    carregar_modelo_cabecalho,
    carregar_projeto,
    criar_projeto,
    listar_projetos,
    marcar_ultimo_projeto,
    migrar_dados_legado_se_preciso,
    obter_projeto_inicial,
    rotulo_projeto,
    salvar_modelo_cabecalho,
)
from app.schema import CAMPOS_CABECALHO, CAMPOS_MODELO_CABECALHO, ROTULOS_CABECALHO
from app.reports import EscopoRelatorio, exportar_pacote, pasta_relatorios
from app.reports.period import (
    ano_mes_de,
    notas_sem_data,
    resumo_por_mes,
    rotulo_mes,
    selecionar_receipts,
)
from app.settings import (
    apply_settings_to_config,
    clear_preferred_api_key,
    get_api_key,
    list_api_keys,
    load_user_settings,
    motor_label,
    parallel_extraction_workers,
    patch_user_settings,
    prefer_api_key,
    resolver_modelo_gemini,
)
from app.storage import (
    format_brl,
    move_receipt_to_excluded,
    restore_receipt_from_excluded,
    save_trip,
    sync_receipts_from_folder,
)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Relatório de Despesas de Viagem")
        self.geometry("1280x800")
        self.minsize(1000, 650)

        self.config_data = apply_settings_to_config(load_config())
        self.confianca_minima = float(self.config_data.get("confianca_minima", 0.7))
        self.categorias = categorias_completas(self.config_data)

        self.trip = Trip()
        self._mapa_rotulo_para_pasta: dict[str, Path] = {}
        self._ignorando_troca_projeto = False
        self.selected_id: str | None = None
        self._photo = None
        self._tk_photo = None
        self._preview_pil: Image.Image | None = None
        self._zoom = 1.0
        self._fit_zoom = 1.0
        self._disp_size = (1, 1)
        self._img_offset = (0, 0)
        self._viewport_cache = (520, 560)
        self._crop_mode = False
        self._crop_start = None
        self._crop_box = None
        self._crop_rect_id = None
        self._panning = False
        self._scroll_focus = None
        self._busy = False
        self._bg_thread: threading.Thread | None = None
        self._list_sig: tuple | None = None
        self._persist_lock = threading.Lock()
        self._group_mode = tk.StringVar(value="todos")
        self._list_scope = tk.StringVar(value="ativas")
        self._view_period = tk.StringVar(value="todos")  # todos | sem_data | YYYY-MM
        self._list_item_widgets: dict[str, ctk.CTkFrame] = {}
        self._period_combo_map: dict[str, str] = {"Todos os meses": "todos"}
        self._dup_ids: set[str] = set()
        self._dup_grupos: dict[str, list[str]] = {}

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Delete>", self._on_tecla_delete)
        self.bind_all("<KP_Delete>", self._on_tecla_delete)
        self.after(100, self._initial_load)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self._build_menu()
        self._build_project_bar()
        self._build_tabs()

    def _build_menu(self) -> None:
        self._barra_menu_frame, self._menus_ctk = criar_barra_menu_ctk(
            self,
            [
                ("Arquivo", self._itens_menu_arquivo()),
                ("Notas", self._itens_menu_notas()),
                ("Relatório", self._itens_menu_relatorio()),
                ("Exibir", self._itens_menu_exibir()),
                ("Ajuda", self._itens_menu_ajuda()),
            ],
        )

    def _itens_menu_arquivo(self) -> list[EntradaMenuBarra]:
        return [
            EntradaMenuBarra("Salvar agora", self._save_trip_meta),
            EntradaMenuBarra("Novo projeto", self._dialog_novo_projeto),
            EntradaMenuBarra(
                "Editar chave do projeto (contratante + natureza)",
                self._dialog_editar_chave_projeto,
            ),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Abrir pasta do projeto", self._abrir_pasta_projeto),
            EntradaMenuBarra("Abrir pasta de notas", self._abrir_pasta_notas),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Salvar modelo de cabeçalho", self._salvar_modelo_cabecalho),
            EntradaMenuBarra("Carregar modelo de cabeçalho", self._carregar_modelo_cabecalho),
            EntradaMenuBarra("Assinatura do funcionário…", self._escolher_assinatura),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Configurações…", self._open_settings),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Sair", self._on_close),
        ]

    def _itens_menu_notas(self) -> list[EntradaMenuBarra]:
        return [
            EntradaMenuBarra("Atualizar lista de notas", self._scan_folder),
            EntradaMenuBarra("Extrair notas pendentes", self._extract_pending),
            EntradaMenuBarra("Reextrair nota selecionada", self._reextract_selected),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Ver conversa com a IA…", self._ver_conversa_ia),
        ]

    def _itens_menu_relatorio(self) -> list[EntradaMenuBarra]:
        return [
            EntradaMenuBarra("Gerar relatório…", self._generate_reports),
            EntradaMenuBarra("Abrir pasta de relatórios", self._abrir_pasta_relatorios),
            EntradaMenuBarra.sep(),
            EntradaMenuBarra("Mostrar pasta do template RDV", self._abrir_pasta_modelo_rdv),
            EntradaMenuBarra("Editar mapeamento de células…", self._editar_mapeamento_rdv),
            EntradaMenuBarra("Gerenciar categorias extras…", self._gerenciar_categorias),
        ]

    def _itens_menu_exibir(self) -> list[EntradaMenuBarra]:
        return [
            EntradaMenuBarra("Alternar tema claro/escuro", self._alternar_tema_aplicacao),
        ]

    def _itens_menu_ajuda(self) -> list[EntradaMenuBarra]:
        return [
            EntradaMenuBarra("Manual", self._mostrar_manual_ajuda),
            EntradaMenuBarra("Sobre", self._mostrar_sobre_ajuda),
        ]

    def _build_project_bar(self) -> None:
        self._barra_projeto = ctk.CTkFrame(self, fg_color=COR_FUNDO_SECUNDARIO, corner_radius=0)
        self._barra_projeto.pack(fill="x", padx=0, pady=0)
        conteudo = ctk.CTkFrame(self._barra_projeto, fg_color="transparent")
        conteudo.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(
            conteudo,
            text="Projeto (contratante + natureza):",
            font=FONT_INTERFACE,
            text_color=COR_TEXTO,
        ).pack(side="left", padx=(0, 6))
        self.combo_projeto = ctk.CTkComboBox(
            conteudo,
            state="readonly",
            command=self._on_project_combo,
            **opcoes_combo_ctk(largura=520),
        )
        self.combo_projeto.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(
            conteudo,
            text="Novo…",
            width=90,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=self._dialog_novo_projeto,
        ).pack(side="left", padx=(8, 0))

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(
            self,
            command=self._on_tab_change,
            **opcoes_tabview_ctk(),
        )
        self.tabview.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.tabview.add("Dados do projeto")
        self.tabview.add("Notas e relatório")
        self.tabview.add("Prévia visual")
        configurar_abas_tabview(self.tabview)

        self.lbl_totais = ctk.CTkLabel(
            self.tabview,
            text="Total: R$ 0,00",
            anchor="e",
            font=FONT_INTERFACE,
            text_color=COR_TEXTO,
        )
        # Mesma faixa das guias (sem bind — CTk bloqueia); alinhado à direita, ao lado das abas
        self.lbl_totais.grid(row=1, rowspan=2, column=0, sticky="ne", padx=(0, 16), pady=(6, 0))
        self.lbl_totais.lift()

        self._build_tab_dados(self.tabview.tab("Dados do projeto"))
        self._build_tab_notas(self.tabview.tab("Notas e relatório"))
        self._build_tab_previa(self.tabview.tab("Prévia visual"))
        self._last_tab = "Dados do projeto"

    def _build_tab_dados(self, aba) -> None:
        aba.grid_columnconfigure(0, weight=1)
        aba.grid_rowconfigure(0, weight=1)

        wrap = ctk.CTkScrollableFrame(aba, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        wrap.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            wrap,
            text="Informações destinadas aos cabeçalhos das planilhas (RDV).",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 12))

        self._widgets_cabecalho: dict[str, ctk.CTkEntry] = {}
        placeholders = {
            "inicio_contratual": "AAAA-MM-DD",
            "termino_contratual": "AAAA-MM-DD",
            "adiantamento": "0,00",
        }
        for i, campo in enumerate(CAMPOS_CABECALHO, start=1):
            rotulo = ROTULOS_CABECALHO.get(campo, campo)
            ctk.CTkLabel(wrap, text=f"{rotulo}:", font=FONT_INTERFACE, anchor="e").grid(
                row=i, column=0, sticky="e", padx=(0, 12), pady=6
            )
            entry = ctk.CTkEntry(
                wrap,
                placeholder_text=placeholders.get(campo),
                **opcoes_campo_entrada_ctk(largura=420),
            )
            entry.grid(row=i, column=1, sticky="ew", pady=6)
            self._widgets_cabecalho[campo] = entry

        row_obs = 1 + len(CAMPOS_CABECALHO)
        ctk.CTkLabel(wrap, text="Observações:", font=FONT_INTERFACE, anchor="ne").grid(
            row=row_obs, column=0, sticky="ne", padx=(0, 12), pady=6
        )
        self.txt_obs_projeto = ctk.CTkTextbox(wrap, **opcoes_caixa_texto_ctk(altura_px=90))
        self.txt_obs_projeto.grid(row=row_obs, column=1, sticky="ew", pady=6)

        row_ass = row_obs + 1
        ctk.CTkLabel(wrap, text="Assinatura:", font=FONT_INTERFACE, anchor="ne").grid(
            row=row_ass, column=0, sticky="ne", padx=(0, 12), pady=6
        )
        frame_ass = ctk.CTkFrame(wrap, fg_color="transparent")
        frame_ass.grid(row=row_ass, column=1, sticky="ew", pady=6)
        self.lbl_preview_assinatura = ctk.CTkLabel(
            frame_ass,
            text="Nenhuma assinatura",
            width=220,
            height=72,
            fg_color=COR_FUNDO_SECUNDARIO,
            corner_radius=RAIO_BORDA,
            text_color=COR_TEXTO_SECUNDARIO,
            font=FONT_DICA,
        )
        self.lbl_preview_assinatura.pack(side="left", padx=(0, 10))
        self._img_preview_assinatura = None
        btns_ass = ctk.CTkFrame(frame_ass, fg_color="transparent")
        btns_ass.pack(side="left", fill="y")
        ctk.CTkButton(
            btns_ass,
            text="Adicionar assinatura…",
            width=170,
            command=self._escolher_assinatura,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(
            btns_ass,
            text="Remover assinatura",
            width=170,
            command=self._remover_assinatura,
        ).pack(anchor="w", pady=2)
        ctk.CTkButton(
            btns_ass,
            text="Abrir pasta de assinaturas",
            width=170,
            command=self._abrir_pasta_assinaturas,
        ).pack(anchor="w", pady=2)
        self.lbl_status_assinatura = ctk.CTkLabel(
            frame_ass,
            text="",
            anchor="w",
            justify="left",
            wraplength=280,
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        self.lbl_status_assinatura.pack(side="left", padx=(12, 0), fill="x", expand=True)

        ctk.CTkLabel(wrap, text="Pasta do projeto:", font=FONT_INTERFACE, anchor="e").grid(
            row=row_ass + 1, column=0, sticky="ne", padx=(0, 12), pady=(12, 6)
        )
        self.lbl_pasta_projeto = ctk.CTkLabel(
            wrap,
            text="—",
            anchor="w",
            justify="left",
            wraplength=520,
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        self.lbl_pasta_projeto.grid(row=row_ass + 1, column=1, sticky="ew", pady=(12, 6))

        acoes = ctk.CTkFrame(wrap, fg_color="transparent")
        acoes.grid(row=row_ass + 2, column=0, columnspan=2, sticky="w", pady=(16, 8))
        ctk.CTkButton(
            acoes,
            text="Salvar dados",
            width=140,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=self._save_trip_meta,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            acoes,
            text="Abrir pasta de notas",
            width=160,
            command=self._abrir_pasta_notas,
        ).pack(side="left", padx=4)

    def _build_tab_notas(self, aba) -> None:
        aba.grid_columnconfigure(0, weight=1)
        aba.grid_rowconfigure(0, weight=1)

        body = ctk.CTkFrame(aba, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # Painel redimensionável: lista | visualização | conferência
        self.notas_paned = tk.PanedWindow(
            body,
            orient=tk.HORIZONTAL,
            sashwidth=8,
            sashrelief=tk.FLAT,
            bg=resolver_cor(COR_FUNDO_CARD),
            bd=0,
        )
        self.notas_paned.grid(row=0, column=0, sticky="nsew")

        # Esquerda: lista
        left = ctk.CTkFrame(self.notas_paned)
        left.grid_rowconfigure(6, weight=1)
        left.grid_columnconfigure(0, weight=1)

        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(btns, text="Pasta…", width=70, command=self._abrir_pasta_notas).pack(side="left", padx=2)
        ctk.CTkButton(btns, text="Escanear", width=70, command=self._scan_folder).pack(side="left", padx=2)
        ctk.CTkButton(btns, text="Extrair", width=70, command=self._extract_pending).pack(side="left", padx=2)

        self.lbl_motor = ctk.CTkLabel(
            left, text="", anchor="w", text_color=COR_TEXTO_SECUNDARIO, font=FONT_DICA
        )
        self.lbl_motor.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))

        scope = ctk.CTkFrame(left, fg_color="transparent")
        scope.grid(row=2, column=0, sticky="ew", padx=4, pady=2)
        self.radio_scope_ativas = ctk.CTkRadioButton(
            scope,
            text="Ativas (0)",
            variable=self._list_scope,
            value="ativas",
            command=self._on_scope_change,
            font=FONT_INTERFACE,
        )
        self.radio_scope_ativas.pack(side="left", padx=(4, 12))
        self.radio_scope_excluidas = ctk.CTkRadioButton(
            scope,
            text="Excluídas (0)",
            variable=self._list_scope,
            value="excluidas",
            command=self._on_scope_change,
            font=FONT_INTERFACE,
        )
        self.radio_scope_excluidas.pack(side="left", padx=4)

        periodo_bar = ctk.CTkFrame(left, fg_color="transparent")
        periodo_bar.grid(row=3, column=0, sticky="ew", padx=4, pady=2)
        periodo_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(periodo_bar, text="Mês:", font=FONT_INTERFACE).grid(row=0, column=0, sticky="w", padx=(4, 6))
        self.combo_periodo_notas = ctk.CTkComboBox(
            periodo_bar,
            values=["Todos os meses"],
            command=self._on_view_period_change,
            **opcoes_combo_ctk(largura=220),
        )
        self.combo_periodo_notas.set("Todos os meses")
        self.combo_periodo_notas.grid(row=0, column=1, sticky="ew")

        filt = ctk.CTkFrame(left, fg_color="transparent")
        filt.grid(row=4, column=0, sticky="ew", padx=4, pady=2)
        ctk.CTkLabel(filt, text="Agrupar:").pack(side="left")
        for label, val in [("Todos", "todos"), ("Categoria", "categoria"), ("Data", "data")]:
            ctk.CTkRadioButton(
                filt, text=label, variable=self._group_mode, value=val, command=self._refresh_list
            ).pack(side="left", padx=4)

        legenda = ctk.CTkFrame(left, fg_color="transparent")
        legenda.grid(row=5, column=0, sticky="ew", padx=6, pady=(0, 2))
        for texto, cor in (
            ("Sem extrair", COR_NOTA_SEM_EXTRAIR),
            ("Erro", COR_NOTA_ERRO),
            ("Sem valor", COR_NOTA_SEM_VALOR),
            ("A verificar", COR_NOTA_A_VERIFICAR),
            ("OK", COR_NOTA_VERIFICADA),
            ("Duplicata", COR_NOTA_DUPLICATA),
        ):
            chip = ctk.CTkFrame(legenda, fg_color=cor, width=10, height=10, corner_radius=3)
            chip.pack(side="left", padx=(0, 3))
            chip.pack_propagate(False)
            ctk.CTkLabel(legenda, text=texto, font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO).pack(
                side="left", padx=(0, 8)
            )

        self.list_frame = ctk.CTkScrollableFrame(left)
        self.list_frame.grid(row=6, column=0, sticky="nsew", padx=4, pady=4)

        # Centro: preview com canvas + barras H/V (zoom no ponto do mouse)
        center = ctk.CTkFrame(self.notas_paned)
        self.preview_center = center
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)

        self.preview_frame = ctk.CTkFrame(center, fg_color="transparent")
        self.preview_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.preview_frame.grid_rowconfigure(0, weight=1)
        self.preview_frame.grid_columnconfigure(0, weight=1)
        # Alias usado em cálculos antigos de chrome
        self.preview_scroll = self.preview_frame
        self._viewport_cache = (520, 560)

        self._preview_bg = self._preview_canvas_bg()
        self.preview_canvas = tk.Canvas(
            self.preview_frame,
            highlightthickness=0,
            bd=0,
            bg=self._preview_bg,
            cursor="arrow",
            xscrollincrement=1,
            yscrollincrement=1,
        )
        self.preview_canvas._rdv_preview_canvas = True  # type: ignore[attr-defined]
        # Mesmo estilo das barras do CTkScrollableFrame (lista de notas, etc.)
        self.preview_vbar = ctk.CTkScrollbar(
            self.preview_frame,
            orientation="vertical",
            command=self.preview_canvas.yview,
        )
        self.preview_hbar = ctk.CTkScrollbar(
            self.preview_frame,
            orientation="horizontal",
            command=self.preview_canvas.xview,
        )
        self.preview_canvas.configure(
            xscrollcommand=self.preview_hbar.set,
            yscrollcommand=self.preview_vbar.set,
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_vbar.grid(row=0, column=1, sticky="ns")
        self.preview_hbar.grid(row=1, column=0, sticky="ew")

        self._img_offset = (0, 0)
        self._scroll_focus: tuple[float, float, float, float] | None = None  # fx,fy,mx,my

        self.preview_canvas.create_text(
            200,
            160,
            text="Selecione uma nota",
            fill=resolver_cor(COR_TEXTO_SECUNDARIO),
            tags="placeholder",
            font=("Segoe UI", 12),
        )
        self._sync_preview_chrome()

        self.preview_canvas.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview_canvas.bind("<Button-4>", lambda e: self._on_preview_wheel_linux(e, 1))
        self.preview_canvas.bind("<Button-5>", lambda e: self._on_preview_wheel_linux(e, -1))
        self.preview_canvas.bind("<Shift-MouseWheel>", self._on_preview_shift_wheel)
        self.preview_canvas.bind("<ButtonPress-1>", self._on_preview_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_preview_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_preview_release)
        # Botão do meio também arrasta a imagem
        self.preview_canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.preview_canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.preview_canvas.bind("<ButtonRelease-2>", self._on_pan_end)

        rot_bar = ctk.CTkFrame(center, fg_color="transparent")
        self.preview_rot_bar = rot_bar
        rot_bar.grid(row=1, column=0, pady=4)
        ctk.CTkButton(rot_bar, text="↺ 90°", width=70, command=lambda: self._rotate(-90)).pack(side="left", padx=3)
        ctk.CTkButton(rot_bar, text="↻ 90°", width=70, command=lambda: self._rotate(90)).pack(side="left", padx=3)
        ctk.CTkButton(rot_bar, text="−", width=40, command=lambda: self._zoom_by(1 / 1.25)).pack(side="left", padx=3)
        self.lbl_zoom = ctk.CTkLabel(rot_bar, text="100%", width=56)
        self.lbl_zoom.pack(side="left", padx=2)
        ctk.CTkButton(rot_bar, text="+", width=40, command=lambda: self._zoom_by(1.25)).pack(side="left", padx=3)
        ctk.CTkButton(rot_bar, text="Ajustar", width=70, command=self._zoom_fit).pack(side="left", padx=3)
        ctk.CTkButton(rot_bar, text="100%", width=55, command=self._zoom_100).pack(side="left", padx=3)

        crop_bar = ctk.CTkFrame(center, fg_color="transparent")
        self.preview_crop_bar = crop_bar
        crop_bar.grid(row=2, column=0, pady=(0, 6))
        self.btn_crop = ctk.CTkButton(
            crop_bar,
            text="✂ Recortar",
            width=100,
            command=self._toggle_crop_mode,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        )
        self.btn_crop.pack(side="left", padx=3)
        self.btn_crop_apply = ctk.CTkButton(
            crop_bar,
            text="Aplicar recorte",
            width=120,
            command=self._apply_crop,
            fg_color=COR_SUCESSO,
            hover_color="#0f6b2c",
            state="disabled",
        )
        self.btn_crop_apply.pack(side="left", padx=3)
        self.btn_crop_cancel = ctk.CTkButton(
            crop_bar,
            text="Cancelar",
            width=90,
            command=self._cancel_crop_mode,
            fg_color=COR_FUNDO_SECUNDARIO,
            text_color=COR_TEXTO_SECUNDARIO,
            state="disabled",
        )
        self.btn_crop_cancel.pack(side="left", padx=3)
        self.btn_crop_undo = ctk.CTkButton(
            crop_bar,
            text="↩ Desfazer recorte",
            width=140,
            command=self._undo_crop,
            fg_color=COR_FUNDO_SECUNDARIO,
            text_color=COR_TEXTO_SECUNDARIO,
            state="disabled",
        )
        self.btn_crop_undo.pack(side="left", padx=3)
        self.lbl_crop_hint = ctk.CTkLabel(
            crop_bar,
            text="",
            text_color=COR_TEXTO_SECUNDARIO,
            font=FONT_DICA,
        )
        self.lbl_crop_hint.pack(side="left", padx=8)

        # Direita: formulário
        right = ctk.CTkFrame(self.notas_paned)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(right, text="Conferência", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, padx=8, pady=(8, 4), sticky="w"
        )

        self.lbl_arquivo = ctk.CTkLabel(right, text="Arquivo: —", wraplength=260, anchor="w", justify="left")
        self.lbl_arquivo.grid(row=1, column=0, padx=8, pady=2, sticky="ew")

        self.lbl_fonte = ctk.CTkLabel(right, text="Fonte: —", anchor="w")
        self.lbl_fonte.grid(row=2, column=0, padx=8, pady=2, sticky="ew")

        self.lbl_duplicata = ctk.CTkLabel(
            right,
            text="",
            anchor="w",
            justify="left",
            wraplength=260,
            text_color=COR_NOTA_DUPLICATA,
            font=FONT_DICA,
        )
        self.lbl_duplicata.grid(row=3, column=0, padx=8, pady=(0, 2), sticky="ew")

        form = ctk.CTkFrame(right, fg_color="transparent")
        form.grid(row=4, column=0, sticky="ew", padx=8, pady=4)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Valor (R$)").grid(row=0, column=0, sticky="w", pady=3)
        self.ent_valor = ctk.CTkEntry(form, **opcoes_campo_entrada_ctk())
        self.ent_valor.grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 0))

        ctk.CTkLabel(form, text="Data").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_data = ctk.CTkEntry(form, placeholder_text="AAAA-MM-DD", **opcoes_campo_entrada_ctk())
        self.ent_data.grid(row=1, column=1, sticky="ew", pady=3, padx=(8, 0))

        ctk.CTkLabel(form, text="Hora").grid(row=2, column=0, sticky="w", pady=3)
        self.ent_hora = ctk.CTkEntry(form, placeholder_text="HH:MM", **opcoes_campo_entrada_ctk())
        self.ent_hora.grid(row=2, column=1, sticky="ew", pady=3, padx=(8, 0))

        ctk.CTkLabel(form, text="Estabelecimento").grid(row=3, column=0, sticky="w", pady=3)
        self.ent_estab = ctk.CTkEntry(form, **opcoes_campo_entrada_ctk())
        self.ent_estab.grid(row=3, column=1, sticky="ew", pady=3, padx=(8, 0))

        ctk.CTkLabel(form, text="Categoria").grid(row=4, column=0, sticky="w", pady=3)
        cat_row = ctk.CTkFrame(form, fg_color="transparent")
        cat_row.grid(row=4, column=1, sticky="ew", pady=3, padx=(8, 0))
        cat_row.grid_columnconfigure(0, weight=1)
        self.cmb_cat = ctk.CTkComboBox(cat_row, values=self.categorias, **opcoes_combo_ctk())
        self.cmb_cat.grid(row=0, column=0, sticky="ew")
        self.cmb_cat.set("Outros")
        ctk.CTkButton(
            cat_row,
            text="+",
            width=36,
            command=self._gerenciar_categorias,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        ).grid(row=0, column=1, padx=(6, 0))

        ctk.CTkLabel(form, text="Observações").grid(row=5, column=0, sticky="nw", pady=3)
        self.txt_obs = ctk.CTkTextbox(form, **opcoes_caixa_texto_ctk(altura_px=70))
        self.txt_obs.grid(row=5, column=1, sticky="ew", pady=3, padx=(8, 0))

        self.lbl_conf = ctk.CTkLabel(right, text="Confiança: —", anchor="w")
        self.lbl_conf.grid(row=5, column=0, padx=8, pady=2, sticky="ew")

        acoes_nota = ctk.CTkFrame(right, fg_color="transparent")
        acoes_nota.grid(row=6, column=0, sticky="new", padx=8, pady=8)

        self.btn_aplicar = ctk.CTkButton(acoes_nota, text="Aplicar edição", command=self._apply_edits)
        self.btn_conferida = ctk.CTkButton(
            acoes_nota, text="Marcar conferida", fg_color=COR_SUCESSO, command=self._mark_conferida
        )
        self.btn_desfazer_conferida = ctk.CTkButton(
            acoes_nota,
            text="Remover conferida",
            fg_color="#5f6368",
            hover_color="#3c4043",
            command=self._unmark_conferida,
        )
        self.btn_excluir = ctk.CTkButton(
            acoes_nota, text="Excluir nota", fg_color=COR_ERRO, command=self._exclude
        )
        self.btn_restaurar = ctk.CTkButton(acoes_nota, text="Restaurar nota", command=self._restore)
        self.btn_reextrair = ctk.CTkButton(acoes_nota, text="Reextrair", command=self._reextract_selected)
        self.btn_conversa_ia = ctk.CTkButton(
            acoes_nota,
            text="Ver conversa com a IA",
            fg_color="#5f6368",
            hover_color="#3c4043",
            command=self._ver_conversa_ia,
        )
        self.btn_aplicar.pack(fill="x", pady=2)
        self.btn_conferida.pack(fill="x", pady=2)
        self.btn_reextrair.pack(fill="x", pady=2)
        self.btn_conversa_ia.pack(fill="x", pady=2)

        self._build_status_bar(right)

        self.notas_paned.add(left, minsize=200, stretch="always")
        self.notas_paned.add(center, minsize=280, stretch="always")
        self.notas_paned.add(right, minsize=220, stretch="always")
        self.notas_paned.bind("<ButtonRelease-1>", self._on_notas_paned_release)
        self.after(250, self._restaurar_layout_notas)

    def _layout_notas_ratios(self) -> dict[str, float]:
        layout = load_user_settings().get("layout_notas") or {}
        try:
            s0 = float(layout.get("sash0", 0.28))
            s1 = float(layout.get("sash1", 0.72))
        except (TypeError, ValueError):
            s0, s1 = 0.28, 0.72
        s0 = min(max(s0, 0.12), 0.55)
        s1 = min(max(s1, s0 + 0.15), 0.88)
        return {"sash0": s0, "sash1": s1}

    def _restaurar_layout_notas(self) -> None:
        """Aplica larguras salvas das 3 colunas."""
        if not hasattr(self, "notas_paned"):
            return
        try:
            self.update_idletasks()
            total = self.notas_paned.winfo_width()
            if total < 200:
                self.after(200, self._restaurar_layout_notas)
                return
            ratios = self._layout_notas_ratios()
            self.notas_paned.sash_place(0, int(total * ratios["sash0"]), 0)
            self.notas_paned.sash_place(1, int(total * ratios["sash1"]), 0)
        except Exception:
            pass

    def _salvar_layout_notas(self) -> None:
        if not hasattr(self, "notas_paned"):
            return
        try:
            total = max(self.notas_paned.winfo_width(), 1)
            x0, _ = self.notas_paned.sash_coord(0)
            x1, _ = self.notas_paned.sash_coord(1)
            patch_user_settings(
                {
                    "layout_notas": {
                        "sash0": round(x0 / total, 4),
                        "sash1": round(x1 / total, 4),
                    }
                }
            )
        except Exception:
            pass

    def _on_notas_paned_release(self, _event=None) -> None:
        # Pequeno delay para o sash já ter a posição final
        self.after(50, self._salvar_layout_notas)

    def _build_tab_previa(self, aba) -> None:
        """Prévia visual dos gastos — resumo financeiro e composição por categoria."""
        aba.grid_columnconfigure(0, weight=1)
        aba.grid_rowconfigure(1, weight=1)

        topo = ctk.CTkFrame(aba, fg_color="transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        topo.grid_columnconfigure(0, weight=1)

        self.lbl_previa_titulo = ctk.CTkLabel(
            topo, text="Prévia dos gastos", font=FONT_TITULO, anchor="w"
        )
        self.lbl_previa_titulo.grid(row=0, column=0, sticky="w")
        self.lbl_previa_subtitulo = ctk.CTkLabel(
            topo,
            text="Visão rápida para conferência antes de gerar o relatório.",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            anchor="w",
        )
        self.lbl_previa_subtitulo.grid(row=1, column=0, sticky="w", pady=(2, 0))

        previa_acoes = ctk.CTkFrame(topo, fg_color="transparent")
        previa_acoes.grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))
        ctk.CTkLabel(previa_acoes, text="Mês:", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO).pack(
            side="left", padx=(0, 4)
        )
        self.combo_periodo_previa = ctk.CTkComboBox(
            previa_acoes,
            values=["Todos os meses"],
            command=self._on_view_period_change,
            **opcoes_combo_ctk(largura=200),
        )
        self.combo_periodo_previa.set("Todos os meses")
        self.combo_periodo_previa.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            previa_acoes, text="Atualizar", width=100, command=self._refresh_previa_visual
        ).pack(side="left")

        self.previa_scroll = ctk.CTkScrollableFrame(aba, fg_color="transparent")
        self.previa_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.previa_scroll.grid_columnconfigure(0, weight=1)

        # KPIs financeiros
        self.previa_kpis = ctk.CTkFrame(self.previa_scroll, fg_color="transparent")
        self.previa_kpis.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for col in range(4):
            self.previa_kpis.grid_columnconfigure(col, weight=1)

        self._previa_kpi_labels: dict[str, ctk.CTkLabel] = {}
        self._previa_kpi_hints: dict[str, ctk.CTkLabel] = {}
        for i, chave in enumerate(("total", "adiantamento", "reembolso", "media")):
            card = ctk.CTkFrame(self.previa_kpis, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
            card.grid(row=0, column=i, sticky="ew", padx=4, pady=4)
            tit = ctk.CTkLabel(card, text="", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="w")
            tit.pack(fill="x", padx=14, pady=(12, 0))
            val = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=22, weight="bold"), anchor="w")
            val.pack(fill="x", padx=14, pady=(4, 0))
            hint = ctk.CTkLabel(card, text="", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="w")
            hint.pack(fill="x", padx=14, pady=(2, 12))
            self._previa_kpi_labels[chave] = val
            self._previa_kpi_hints[chave] = hint
            setattr(self, f"_previa_kpi_tit_{chave}", tit)

        self._previa_kpi_tit_total.configure(text="Total das despesas")
        self._previa_kpi_tit_adiantamento.configure(text="Adiantamento")
        self._previa_kpi_tit_reembolso.configure(text="A reembolsar")
        self._previa_kpi_tit_media.configure(text="Ticket médio")

        # Fórmula + meta do projeto
        self.lbl_previa_financeiro = ctk.CTkLabel(
            self.previa_scroll, text="", font=FONT_INTERFACE, anchor="w", justify="left", wraplength=980
        )
        self.lbl_previa_financeiro.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 2))
        self.lbl_previa_meta = ctk.CTkLabel(
            self.previa_scroll,
            text="",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self.lbl_previa_meta.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 8))

        # Alertas / pendências
        self.previa_alertas = ctk.CTkFrame(self.previa_scroll, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        self.previa_alertas.grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        self.previa_alertas.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.previa_alertas, text="Pendências", font=FONT_INTERFACE, anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self.previa_alertas_frame = ctk.CTkFrame(self.previa_alertas, fg_color="transparent")
        self.previa_alertas_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 12))

        # Composição por categoria
        cat_box = ctk.CTkFrame(self.previa_scroll, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        cat_box.grid(row=4, column=0, sticky="ew", padx=4, pady=4)
        cat_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cat_box,
            text="Composição por categoria  ·  barras = % do total",
            font=FONT_INTERFACE,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        self.lbl_previa_composicao = ctk.CTkLabel(
            cat_box, text="", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="w"
        )
        self.lbl_previa_composicao.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        self.previa_cat_frame = ctk.CTkFrame(cat_box, fg_color="transparent")
        self.previa_cat_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.previa_cat_frame.grid_columnconfigure(2, weight=1)
        self.lbl_previa_cat_rodape = ctk.CTkLabel(
            cat_box, text="", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="e"
        )
        self.lbl_previa_cat_rodape.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 12))

        # Status da conferência (maior/menor gasto removidos — pouco úteis)
        extras = ctk.CTkFrame(self.previa_scroll, fg_color="transparent")
        extras.grid(row=5, column=0, sticky="ew", padx=4, pady=4)
        extras.grid_columnconfigure(0, weight=1)
        self._previa_extra_labels: dict[str, ctk.CTkLabel] = {}
        card = ctk.CTkFrame(extras, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(
            card, text="Status da conferência", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="w"
        ).pack(fill="x", padx=14, pady=(12, 0))
        val = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=15, weight="bold"), anchor="w", wraplength=600)
        val.pack(fill="x", padx=14, pady=(4, 12))
        self._previa_extra_labels["status"] = val

        # Lista de gastos com ordenação
        top_box = ctk.CTkFrame(self.previa_scroll, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        top_box.grid(row=6, column=0, sticky="ew", padx=4, pady=8)
        top_box.grid_columnconfigure(0, weight=1)
        lista_topo = ctk.CTkFrame(top_box, fg_color="transparent")
        lista_topo.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        lista_topo.grid_columnconfigure(0, weight=1)
        self.lbl_previa_lista_titulo = ctk.CTkLabel(
            lista_topo, text="Todos os gastos", font=FONT_INTERFACE, anchor="w"
        )
        self.lbl_previa_lista_titulo.grid(row=0, column=0, sticky="w", padx=4)
        self._previa_sort = tk.StringVar(value="valor")
        sort_bar = ctk.CTkFrame(lista_topo, fg_color="transparent")
        sort_bar.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(sort_bar, text="Ordenar:", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO).pack(
            side="left", padx=(0, 6)
        )
        for label, val in (("Valor", "valor"), ("Data", "data"), ("Categoria", "categoria")):
            ctk.CTkRadioButton(
                sort_bar,
                text=label,
                variable=self._previa_sort,
                value=val,
                command=self._refresh_previa_visual,
                font=FONT_DICA,
            ).pack(side="left", padx=4)
        self.previa_top_frame = ctk.CTkFrame(top_box, fg_color="transparent")
        self.previa_top_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 12))
        self.previa_top_frame.grid_columnconfigure(0, weight=1)

    def _clear_frame_children(self, frame) -> None:
        for w in frame.winfo_children():
            w.destroy()

    def _cor_categoria(self, idx: int) -> str:
        return CORES_CATEGORIA[idx % len(CORES_CATEGORIA)]

    def _refresh_previa_visual(self) -> None:
        if not hasattr(self, "previa_cat_frame"):
            return

        self._sincronizar_combos_periodo()
        ativos = self._receipts_para_visao(incluir_excluidas=False)
        conferidas = [r for r in ativos if r.status == ReceiptStatus.CONFERIDA]
        com_valor = [r for r in ativos if r.valor is not None]
        sem_valor = [r for r in ativos if r.valor is None]
        sem_data = [r for r in ativos if not r.data]
        pendentes = [r for r in ativos if r.status != ReceiptStatus.CONFERIDA]

        # Totais só das notas da visão atual (filtro mensal)
        totais_visao: dict[str, float] = {}
        for r in com_valor:
            try:
                v = float(r.valor)
            except (TypeError, ValueError):
                continue
            cat = (r.categoria or "").strip() or "Outros"
            totais_visao[cat] = totais_visao.get(cat, 0.0) + v
        total = float(sum(totais_visao.values()))
        adiant = float(self.trip.adiantamento or 0)
        tem_adiant = self.trip.adiantamento is not None and self._view_period.get() == "todos"
        saldo = total - adiant if tem_adiant else total
        reembolso = max(0.0, saldo) if tem_adiant else total
        ticket = (total / len(com_valor)) if com_valor else 0.0

        filtro = self._rotulo_periodo_atual()
        self.lbl_previa_titulo.configure(
            text="Prévia dos gastos" if self._view_period.get() == "todos" else f"Prévia — {filtro}"
        )

        self._previa_kpi_labels["total"].configure(text=format_brl(total))
        self._previa_kpi_hints["total"].configure(
            text=f"{len(com_valor)} nota(s) com valor" + (f" · {len(sem_valor)} sem valor" if sem_valor else "")
            + (f" · filtro: {filtro}" if self._view_period.get() != "todos" else "")
        )
        if tem_adiant:
            self._previa_kpi_labels["adiantamento"].configure(text=format_brl(adiant))
            self._previa_kpi_hints["adiantamento"].configure(text="Informado no projeto")
        elif self._view_period.get() != "todos":
            self._previa_kpi_labels["adiantamento"].configure(text="—")
            self._previa_kpi_hints["adiantamento"].configure(
                text="Adiantamento só no relatório completo / visão «Todos»"
            )
        else:
            self._previa_kpi_labels["adiantamento"].configure(text="R$ 0,00")
            self._previa_kpi_hints["adiantamento"].configure(
                text="Não informado — edite em Dados do projeto"
            )
        self._previa_kpi_labels["reembolso"].configure(text=format_brl(reembolso))
        if tem_adiant and saldo < 0:
            self._previa_kpi_hints["reembolso"].configure(
                text=f"Adiantamento cobre as despesas (+ {format_brl(abs(saldo))} a favor)"
            )
        elif tem_adiant:
            self._previa_kpi_hints["reembolso"].configure(text="Total − adiantamento")
        else:
            self._previa_kpi_hints["reembolso"].configure(text="Total do período (sem rateio de adiantamento)")
        self._previa_kpi_labels["media"].configure(text=format_brl(ticket) if com_valor else "—")
        self._previa_kpi_hints["media"].configure(
            text=f"{len(conferidas)}/{len(ativos)} conferidas" if ativos else "Sem notas"
        )

        if tem_adiant:
            if saldo >= 0:
                formula = (
                    f"Total {format_brl(total)}  −  Adiantamento {format_brl(adiant)}"
                    f"  =  A reembolsar {format_brl(reembolso)}"
                )
            else:
                formula = (
                    f"Total {format_brl(total)}  −  Adiantamento {format_brl(adiant)}"
                    f"  =  Nada a reembolsar (crédito {format_brl(abs(saldo))})"
                )
        else:
            formula = f"Total do período: {format_brl(total)}"
            if self._view_period.get() != "todos":
                formula += "  ·  Adiantamento não rateado nesta visão mensal"
        self.lbl_previa_financeiro.configure(text=formula)

        periodo = f"{self.trip.inicio_contratual or '—'} a {self.trip.termino_contratual or '—'}"
        partes = [
            f"Funcionário: {self.trip.nome_funcionario or '—'}",
            f"Endereço: {self.trip.endereco or '—'}",
            f"Período contratual: {periodo}",
            f"Visão: {filtro}",
        ]
        if self.trip.contratante:
            partes.insert(1, f"Contratante: {self.trip.contratante}")
        if self.trip.empreendimento:
            partes.append(f"Empreendimento: {self.trip.empreendimento}")
        if self.trip.natureza_servico:
            partes.insert(0, f"Natureza: {self.trip.natureza_servico}")
        if self.trip.numero_rdv:
            partes.append(f"RDV: {self.trip.numero_rdv}")
        self.lbl_previa_meta.configure(text="  ·  ".join(partes))

        # Pendências
        self._clear_frame_children(self.previa_alertas_frame)
        alertas: list[tuple[str, str]] = []
        if sem_valor:
            alertas.append((COR_NOTA_SEM_VALOR[0], f"{len(sem_valor)} nota(s) sem valor"))
        if sem_data and self._view_period.get() == "todos":
            alertas.append((COR_NOTA_SEM_DATA[0], f"{len(sem_data)} nota(s) sem data"))
        if pendentes:
            alertas.append((COR_PRIMARIA, f"{len(pendentes)} nota(s) ainda não conferidas"))
        texto_dup = resumo_duplicatas(ativos)
        if texto_dup:
            alertas.append(
                (
                    resolver_cor(COR_NOTA_DUPLICATA),
                    texto_dup + " — mesma data, hora, valor e estabelecimento",
                )
            )
        if not tem_adiant and self._view_period.get() == "todos" and total > 0 and self.trip.adiantamento is None:
            alertas.append(("#5f6368", "Adiantamento não informado"))
        n_sem_data_proj = len(notas_sem_data(self.trip.receipts))
        if self._view_period.get() not in ("todos", "sem_data") and n_sem_data_proj:
            alertas.append(
                (
                    COR_NOTA_SEM_DATA[0],
                    f"{n_sem_data_proj} nota(s) sem data ficam fora desta visão mensal",
                )
            )
        if not alertas:
            ctk.CTkLabel(
                self.previa_alertas_frame,
                text="Nenhuma pendência — pronto para gerar o relatório.",
                text_color=COR_SUCESSO,
                anchor="w",
            ).pack(fill="x", padx=4, pady=4)
        else:
            for cor, texto in alertas:
                chip = ctk.CTkFrame(self.previa_alertas_frame, fg_color="transparent")
                chip.pack(fill="x", padx=4, pady=3)
                ctk.CTkFrame(chip, fg_color=cor, width=8, height=8, corner_radius=4).pack(
                    side="left", padx=(0, 8)
                )
                ctk.CTkLabel(chip, text=texto, anchor="w").pack(side="left", fill="x")

        # Categorias — barra proporcional ao TOTAL (não ao maior item)
        self._clear_frame_children(self.previa_cat_frame)
        self.previa_cat_frame.grid_columnconfigure(2, weight=1)
        totais = dict(sorted(totais_visao.items(), key=lambda x: (-x[1], x[0])))
        qtd_por_cat: dict[str, int] = {}
        for r in com_valor:
            cat = (r.categoria or "").strip() or "Outros"
            qtd_por_cat[cat] = qtd_por_cat.get(cat, 0) + 1

        if not totais:
            self.lbl_previa_composicao.configure(text="")
            ctk.CTkLabel(
                self.previa_cat_frame,
                text="Nenhuma despesa com valor neste filtro. Extraia e confira as notas.",
                text_color=COR_TEXTO_SECUNDARIO,
                anchor="w",
            ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=8)
            self.lbl_previa_cat_rodape.configure(text="")
        else:
            ordenado = sorted(totais.items(), key=lambda x: -x[1])
            # Mini composição textual (legend)
            self.lbl_previa_composicao.configure(
                text="  ·  ".join(
                    f"{cat} {((v / total) * 100 if total else 0):.0f}%" for cat, v in ordenado[:6]
                )
            )
            for i, (cat, valor) in enumerate(ordenado):
                n = qtd_por_cat.get(cat, 0)
                pct = (float(valor) / total) if total else 0.0
                cor = self._cor_categoria(i)

                # Indicador colorido
                ctk.CTkFrame(
                    self.previa_cat_frame, fg_color=cor, width=10, height=10, corner_radius=3
                ).grid(row=i, column=0, sticky="w", padx=(4, 6), pady=6)

                ctk.CTkLabel(
                    self.previa_cat_frame,
                    text=f"{cat}  ·  {n} nota{'s' if n != 1 else ''}",
                    anchor="w",
                    width=220,
                ).grid(row=i, column=1, sticky="w", padx=2, pady=6)

                bar = ctk.CTkProgressBar(
                    self.previa_cat_frame,
                    height=16,
                    progress_color=cor,
                    fg_color=("#e8eaed", "#3c4043"),
                )
                bar.grid(row=i, column=2, sticky="ew", padx=10, pady=6)
                # Correção: proporção do total geral (85% → barra a 0.85)
                bar.set(max(0.0, min(1.0, pct)))

                ctk.CTkLabel(
                    self.previa_cat_frame,
                    text=f"{format_brl(valor)}   {pct * 100:.1f}%",
                    anchor="e",
                    font=ctk.CTkFont(weight="bold"),
                    width=160,
                ).grid(row=i, column=3, sticky="e", padx=4, pady=6)

            self.lbl_previa_cat_rodape.configure(
                text=(
                    f"Soma das categorias: {format_brl(sum(totais.values()))}  =  "
                    f"Total {format_brl(total)}"
                )
            )

        # Status conferência
        if not ativos:
            self._previa_extra_labels["status"].configure(text="Sem notas neste filtro")
        elif not pendentes:
            self._previa_extra_labels["status"].configure(text="100% conferidas")
        else:
            pct_ok = (len(conferidas) / len(ativos)) * 100
            self._previa_extra_labels["status"].configure(
                text=f"{pct_ok:.0f}% conferidas\n{len(pendentes)} pendente(s)"
            )

        # Lista completa
        self._clear_frame_children(self.previa_top_frame)
        modo = self._previa_sort.get() if hasattr(self, "_previa_sort") else "valor"

        def chave_ord(r: Receipt):
            if modo == "data":
                return (r.data_ordenacao().isoformat(), -(float(r.valor) if r.valor is not None else -1))
            if modo == "categoria":
                return (
                    (r.categoria or "Outros").lower(),
                    -(float(r.valor) if r.valor is not None else -1),
                    r.data_ordenacao().isoformat(),
                )
            return (
                -(float(r.valor) if r.valor is not None else -1.0),
                r.data_ordenacao().isoformat(),
                r.estabelecimento or "",
            )

        ordenados = sorted(ativos, key=chave_ord)
        titulo_lista = (
            f"Todos os gastos ({len(ordenados)})"
            if self._view_period.get() == "todos"
            else f"Gastos — {filtro} ({len(ordenados)})"
        )
        self.lbl_previa_lista_titulo.configure(text=titulo_lista)
        if not ordenados:
            ctk.CTkLabel(
                self.previa_top_frame,
                text="Nenhuma nota neste filtro.",
                text_color=COR_TEXTO_SECUNDARIO,
                anchor="w",
            ).pack(fill="x", padx=4, pady=8)
            return

        # Cabeçalho da tabela
        head = ctk.CTkFrame(self.previa_top_frame, fg_color="transparent")
        head.pack(fill="x", padx=4, pady=(2, 6))
        for txt, side in (("Data / Categoria / Estabelecimento", "left"), ("Valor", "right")):
            ctk.CTkLabel(
                head, text=txt, font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO, anchor="w" if side == "left" else "e"
            ).pack(side=side, fill="x", expand=(side == "left"))

        for r in ordenados:
            linha = ctk.CTkFrame(self.previa_top_frame, fg_color="transparent")
            linha.pack(fill="x", padx=4, pady=2)
            estab = (r.estabelecimento or Path(r.arquivo).name)[:48]
            data = r.data or "sem data"
            cat = r.categoria or "Outros"
            badge = "OK" if r.status == ReceiptStatus.CONFERIDA else "…"
            ctk.CTkLabel(
                linha,
                text=f"[{badge}]  {data}  ·  {cat}  ·  {estab}",
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                linha,
                text=format_brl(r.valor) if r.valor is not None else "sem valor",
                anchor="e",
                font=ctk.CTkFont(weight="bold"),
                text_color=COR_TEXTO_SECUNDARIO if r.valor is None else None,
            ).pack(side="right")

    def _build_status_bar(self, parent) -> None:
        """Rodapé da coluna de conferência: status, progresso e gerar relatório."""
        rodape = ctk.CTkFrame(parent, fg_color=COR_FUNDO_SECUNDARIO, corner_radius=0)
        rodape.grid(row=6, column=0, sticky="sew", padx=0, pady=0)

        self.lbl_status = ctk.CTkLabel(
            rodape,
            text="Pronto.",
            anchor="w",
            justify="left",
            wraplength=200,
            font=FONT_INTERFACE,
        )
        self.lbl_status.pack(fill="x", padx=8, pady=(8, 4))

        self.progress = ctk.CTkProgressBar(rodape, height=8)
        self.progress.pack(fill="x", padx=8, pady=4)
        self.progress.set(0)

        ctk.CTkButton(
            rodape,
            text="Gerar relatório",
            height=36,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=self._generate_reports,
        ).pack(fill="x", padx=8, pady=(4, 8))

        def _ajustar_wrap(_event=None) -> None:
            largura = max(120, rodape.winfo_width() - 24)
            self.lbl_status.configure(wraplength=largura)

        rodape.bind("<Configure>", _ajustar_wrap)

    # ---------- projeto ----------

    def _initial_load(self) -> None:
        self._refresh_motor_label()
        migrar_dados_legado_se_preciso()

        try:
            inicial = obter_projeto_inicial()
        except ValueError as e:
            messagebox.showerror("Projeto", str(e))
            self._refresh_project_combo()
            self._set_status("Não foi possível abrir o último projeto.")
            return
        if inicial is None:
            messagebox.showinfo(
                "Primeiro uso",
                "Crie um projeto com Arquivo → Novo projeto ou o botão Novo…",
            )
            self._refresh_project_combo()
            self._set_status("Nenhum projeto aberto.")
            return

        trip, pasta = inicial
        self._carregar_projeto_em_ui(trip, pasta, sync=True)

    def _refresh_project_combo(self, selecionar: Path | None = None) -> None:
        self._mapa_rotulo_para_pasta.clear()
        itens = listar_projetos()
        rotulos: list[str] = []
        for rotulo, pasta, _trip in itens:
            # Evita colisão de rótulos
            chave = rotulo
            if chave in self._mapa_rotulo_para_pasta:
                chave = f"{rotulo} [{pasta.name}]"
            rotulos.append(chave)
            self._mapa_rotulo_para_pasta[chave] = pasta

        self._ignorando_troca_projeto = True
        try:
            self.combo_projeto.configure(values=rotulos if rotulos else ["(nenhum projeto)"])
            if selecionar is not None:
                for rotulo, pasta in self._mapa_rotulo_para_pasta.items():
                    if pasta.resolve() == Path(selecionar).resolve():
                        self.combo_projeto.set(rotulo)
                        break
                else:
                    if rotulos:
                        self.combo_projeto.set(rotulos[0])
            elif self.trip.pasta_raiz:
                alvo = resolve_path(self.trip.pasta_raiz)
                for rotulo, pasta in self._mapa_rotulo_para_pasta.items():
                    if pasta.resolve() == alvo.resolve():
                        self.combo_projeto.set(rotulo)
                        break
                else:
                    if rotulos:
                        self.combo_projeto.set(rotulos[0])
            elif rotulos:
                self.combo_projeto.set(rotulos[0])
            else:
                self.combo_projeto.set("(nenhum projeto)")
        finally:
            self._ignorando_troca_projeto = False

    def _on_project_combo(self, _valor: str | None = None) -> None:
        if self._ignorando_troca_projeto:
            return
        rotulo = (self.combo_projeto.get() or "").strip()
        pasta = self._mapa_rotulo_para_pasta.get(rotulo)
        if not pasta or not pasta.is_dir():
            return
        if self.trip.pasta_raiz:
            atual = resolve_path(self.trip.pasta_raiz)
            if atual.resolve() == pasta.resolve():
                return
        self._persist()
        try:
            trip = carregar_projeto(pasta)
        except ValueError as e:
            messagebox.showerror("Projeto", str(e))
            self._refresh_project_combo()
            return
        self._carregar_projeto_em_ui(trip, pasta, sync=True)

    def _carregar_projeto_em_ui(self, trip: Trip, pasta: Path, *, sync: bool = False) -> None:
        self.trip = trip
        marcar_ultimo_projeto(pasta)
        self._fill_meta_fields()
        self._refresh_project_combo(selecionar=pasta)
        self.selected_id = None
        self._list_scope.set("ativas")

        if sync:
            novos, _ = sync_receipts_from_folder(self.trip, self.config_data)
            if novos:
                self._set_status(f"{len(novos)} novo(s) arquivo(s) encontrado(s). Use Extrair.")
            else:
                self._set_status(f"Projeto aberto: {rotulo_projeto(self.trip)}")
            self._persist()
        else:
            self._set_status(f"Projeto aberto: {rotulo_projeto(self.trip)}")

        self._refresh_list()
        self._refresh_totais()
        self._clear_form_if_needed()
        if self.trip.receipts:
            first = next((r for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA), None)
            if first:
                self._select_receipt(first.id)

    def _fill_meta_fields(self) -> None:
        for campo, entry in self._widgets_cabecalho.items():
            entry.delete(0, "end")
            valor = getattr(self.trip, campo, None)
            if campo == "adiantamento":
                if valor is not None:
                    entry.insert(0, f"{float(valor):.2f}".replace(".", ","))
                continue
            if valor:
                entry.insert(0, str(valor))

        self.txt_obs_projeto.delete("1.0", "end")
        if self.trip.observacoes:
            self.txt_obs_projeto.insert("1.0", self.trip.observacoes)

        if self.trip.pasta_raiz:
            self.lbl_pasta_projeto.configure(text=str(resolve_path(self.trip.pasta_raiz)))
        else:
            self.lbl_pasta_projeto.configure(text="—")

        # Reutiliza assinatura já salva para o mesmo nome de funcionário
        if not self.trip.assinatura_arquivo:
            path_ass = resolver_assinatura(self.trip.nome_funcionario, None)
            if path_ass:
                self.trip.assinatura_arquivo = caminho_relativo(path_ass)
        self._atualizar_preview_assinatura()

    def _limpar_imagem_preview_assinatura(self) -> None:
        """Remove a PhotoImage do Label Tk antes de soltar a referência Python.

        O CTkLabel.configure aplica ``text`` antes de ``image`` e, com
        ``image=None``, não limpa a imagem do widget interno — se a
        PhotoImage já foi coletada, qualquer configure(text=...) estoura
        TclError: image "pyimageN" doesn't exist.
        """
        lbl = getattr(self, "lbl_preview_assinatura", None)
        if lbl is None:
            return
        try:
            lbl._label.configure(image="")
        except Exception:
            pass
        atual = getattr(lbl, "_image", None)
        if isinstance(atual, ctk.CTkImage):
            try:
                atual.remove_configure_callback(lbl._update_image)
            except (ValueError, AttributeError):
                pass
        try:
            lbl._image = None
        except Exception:
            pass
        self._img_preview_assinatura = None

    def _atualizar_preview_assinatura(self) -> None:
        if not hasattr(self, "lbl_preview_assinatura"):
            return
        self._limpar_imagem_preview_assinatura()
        path = resolver_assinatura(self.trip.nome_funcionario, self.trip.assinatura_arquivo)
        if not path:
            try:
                self.lbl_preview_assinatura.configure(text="Nenhuma assinatura")
            except Exception:
                pass
            if hasattr(self, "lbl_status_assinatura"):
                self.lbl_status_assinatura.configure(text="")
            return
        try:
            img = Image.open(path).convert("RGBA")
            img.thumbnail((200, 64), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._img_preview_assinatura = ctk_img
            # Imagem e texto em chamadas separadas (CTk aplica text antes de image)
            self.lbl_preview_assinatura.configure(image=ctk_img)
            self.lbl_preview_assinatura.configure(text="")
            if hasattr(self, "lbl_status_assinatura"):
                self.lbl_status_assinatura.configure(
                    text=f"Arquivo: {caminho_relativo(path)}\n"
                    f"(salva com o nome do funcionário e no modelo de cabeçalho)"
                )
        except Exception:
            self._limpar_imagem_preview_assinatura()
            try:
                self.lbl_preview_assinatura.configure(text="Erro ao carregar")
            except Exception:
                pass
            if hasattr(self, "lbl_status_assinatura"):
                self.lbl_status_assinatura.configure(text=str(path))

    def _escolher_assinatura(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Assinatura", "Abra ou crie um projeto antes de adicionar a assinatura.")
            return
        self._sync_meta_from_fields()
        nome = (self.trip.nome_funcionario or "").strip()
        if not nome:
            messagebox.showinfo(
                "Assinatura",
                "Preencha o «Nome funcionário» antes de adicionar a assinatura.",
            )
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Selecionar imagem da assinatura",
            filetypes=[
                ("Imagens", "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not path:
            return
        try:
            rel = salvar_assinatura_para_funcionario(Path(path), nome)
        except Exception as e:
            messagebox.showerror("Assinatura", str(e))
            return
        self.trip.assinatura_arquivo = rel
        self._persist()
        self._atualizar_preview_assinatura()
        self._set_status(f"Assinatura salva: {rel}")

    def _remover_assinatura(self) -> None:
        if not self.trip.assinatura_arquivo and not resolver_assinatura(
            self.trip.nome_funcionario, None
        ):
            messagebox.showinfo("Assinatura", "Não há assinatura para remover neste projeto.")
            return
        if not messagebox.askyesno(
            "Remover assinatura",
            "Remover a assinatura deste projeto?\n"
            "(O arquivo em assets/assinaturas permanece para reutilização.)",
        ):
            return
        self.trip.assinatura_arquivo = ""
        self._persist()
        self._atualizar_preview_assinatura()
        self._set_status("Assinatura removida do projeto.")

    def _abrir_pasta_assinaturas(self) -> None:
        pasta = garantir_pasta_assinaturas()
        abrir_pasta_no_explorer(pasta)

    def _dialog_chave_projeto(self, *, editar: bool) -> None:
        """Novo projeto ou editar chave (contratante + natureza)."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Editar chave do cliente" if editar else "Novo cliente")
        dlg.geometry("560x260")
        dlg.minsize(480, 220)
        dlg.transient(self)
        dlg.grab_set()

        frame = ctk.CTkFrame(dlg, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.grid_columnconfigure(1, weight=1)

        if editar:
            ctk.CTkLabel(
                frame,
                text="A chave identifica a pasta do projeto e a lista «Projeto (contratante + natureza)».",
                font=FONT_DICA,
                text_color=COR_TEXTO_SECUNDARIO,
                wraplength=500,
                justify="left",
                anchor="w",
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ctk.CTkLabel(frame, text="Contratante (chave):", font=FONT_INTERFACE, anchor="e").grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=8
        )
        ent_c = ctk.CTkEntry(frame, **opcoes_campo_entrada_ctk(largura=320))
        ent_c.grid(row=1, column=1, sticky="ew", pady=8)

        ctk.CTkLabel(frame, text="Natureza do serviço (chave):", font=FONT_INTERFACE, anchor="e").grid(
            row=2, column=0, sticky="e", padx=(0, 10), pady=8
        )
        ent_n = ctk.CTkEntry(frame, **opcoes_campo_entrada_ctk(largura=320))
        ent_n.grid(row=2, column=1, sticky="ew", pady=8)

        if editar:
            ent_c.insert(0, self.trip.contratante or "")
            ent_n.insert(0, self.trip.natureza_servico or "")

        def confirmar() -> None:
            c = ent_c.get().strip()
            n = ent_n.get().strip()
            if not c or not n:
                messagebox.showerror(
                    "Chave do projeto",
                    "Informe Contratante e Natureza do serviço.",
                    parent=dlg,
                )
                return
            try:
                if editar:
                    if not self.trip.pasta_raiz:
                        messagebox.showinfo("Chave", "Abra um projeto antes de editar a chave.", parent=dlg)
                        return
                    self._persist()
                    self._liberar_recursos_ficheiros_projeto()
                    pasta_atual = resolve_path(self.trip.pasta_raiz)
                    if (
                        c == (self.trip.contratante or "").strip()
                        and n == (self.trip.natureza_servico or "").strip()
                    ):
                        dlg.destroy()
                        return
                    trip, pasta, aviso = atualizar_chave_projeto(self.trip, pasta_atual, c, n)
                    # Mantém valores digitados nos widgets de chave sincronizados
                    if "contratante" in self._widgets_cabecalho:
                        w = self._widgets_cabecalho["contratante"]
                        w.delete(0, "end")
                        w.insert(0, c)
                    if "natureza_servico" in self._widgets_cabecalho:
                        w = self._widgets_cabecalho["natureza_servico"]
                        w.delete(0, "end")
                        w.insert(0, n)
                    dlg.destroy()
                    self._carregar_projeto_em_ui(trip, pasta, sync=False)
                    self._set_status(f"Chave atualizada: {rotulo_projeto(trip)}")
                    if aviso:
                        messagebox.showwarning("Chave atualizada", aviso)
                else:
                    if self.trip.pasta_raiz:
                        self._persist()
                    trip, pasta = criar_projeto(contratante=c, natureza_servico=n)
                    dlg.destroy()
                    self._carregar_projeto_em_ui(trip, pasta, sync=True)
                    self.tabview.set("Dados do projeto")
                    self._set_status(f"Projeto criado: {rotulo_projeto(trip)}")
            except Exception as e:
                messagebox.showerror("Erro", str(e), parent=dlg)

        botoes = ctk.CTkFrame(frame, fg_color="transparent")
        botoes.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        ctk.CTkButton(
            botoes,
            text="Guardar" if editar else "Criar e abrir",
            width=160,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=confirmar,
        ).pack(anchor="center")

        ent_c.focus_set()
        dlg.bind("<Return>", lambda _e: confirmar())

    def _dialog_novo_projeto(self) -> None:
        self._dialog_chave_projeto(editar=False)

    def _dialog_editar_chave_projeto(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Chave", "Abra ou crie um projeto antes de editar a chave.")
            return
        self._dialog_chave_projeto(editar=True)

    def _cabecalho_formulario_tem_valores(self) -> bool:
        for campo, entry in self._widgets_cabecalho.items():
            if entry.get().strip():
                return True
        if self.txt_obs_projeto.get("1.0", "end").strip():
            return True
        return False

    def _salvar_modelo_cabecalho(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Modelo", "Abra um projeto antes de salvar o modelo de cabeçalho.")
            return
        self._sync_meta_from_fields()
        # Se há assinatura resolvida, garante path relativo no trip antes de gravar o modelo
        path_ass = resolver_assinatura(self.trip.nome_funcionario, self.trip.assinatura_arquivo)
        if path_ass:
            try:
                self.trip.assinatura_arquivo = salvar_assinatura_para_funcionario(
                    path_ass, self.trip.nome_funcionario or "funcionario"
                )
            except Exception:
                self.trip.assinatura_arquivo = caminho_relativo(path_ass)
        try:
            path = salvar_modelo_cabecalho(self.trip)
        except Exception as e:
            messagebox.showerror("Modelo", str(e))
            return
        self._persist()
        self._atualizar_preview_assinatura()
        self._set_status(f"Modelo de cabeçalho salvo: {path.name}")
        messagebox.showinfo(
            "Modelo",
            f"Modelo de cabeçalho salvo em:\n{path}\n\n"
            "A assinatura (se houver) fica em assets/assinaturas e é reutilizada pelo nome do funcionário.",
        )

    def _carregar_modelo_cabecalho(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Modelo", "Abra um projeto antes de carregar o modelo de cabeçalho.")
            return
        try:
            modelo = carregar_modelo_cabecalho()
        except Exception as e:
            messagebox.showerror("Modelo", str(e))
            return
        if self._cabecalho_formulario_tem_valores():
            if not messagebox.askyesno(
                "Carregar modelo",
                "O formulário já tem valores preenchidos.\n"
                "Deseja substituí-los pelos valores do modelo salvo?",
            ):
                return
        for campo in CAMPOS_MODELO_CABECALHO:
            if campo == "observacoes":
                continue
            if campo not in self._widgets_cabecalho:
                continue
            # Não altera a chave do projeto ao carregar modelo
            if campo in ("contratante", "natureza_servico"):
                continue
            valor = modelo.get(campo, "")
            entry = self._widgets_cabecalho[campo]
            entry.delete(0, "end")
            if valor not in (None, ""):
                if campo == "adiantamento":
                    try:
                        entry.insert(0, f"{float(valor):.2f}".replace(".", ","))
                    except (TypeError, ValueError):
                        entry.insert(0, str(valor))
                else:
                    entry.insert(0, str(valor))

        obs = str(modelo.get("observacoes") or "")
        self.txt_obs_projeto.delete("1.0", "end")
        if obs:
            self.txt_obs_projeto.insert("1.0", obs)

        ass = str(modelo.get("assinatura_arquivo") or "").strip()
        if ass:
            abs_ass = caminho_absoluto(ass)
            if abs_ass:
                self.trip.assinatura_arquivo = caminho_relativo(abs_ass)
            else:
                # Tenta achar pelo nome do funcionário do modelo
                nome_mod = str(modelo.get("nome_funcionario") or self.trip.nome_funcionario or "")
                encontrado = resolver_assinatura(nome_mod, None)
                self.trip.assinatura_arquivo = caminho_relativo(encontrado) if encontrado else ""
        else:
            # Se o modelo não tem path, usa assinatura já salva para o nome carregado
            nome_mod = str(modelo.get("nome_funcionario") or "").strip()
            if nome_mod:
                encontrado = resolver_assinatura(nome_mod, None)
                if encontrado:
                    self.trip.assinatura_arquivo = caminho_relativo(encontrado)

        self._persist()
        self._atualizar_preview_assinatura()
        self._set_status("Modelo de cabeçalho carregado.")

    def _abrir_pasta_projeto(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Pasta", "Nenhum projeto aberto.")
            return
        abrir_pasta_no_explorer(resolve_path(self.trip.pasta_raiz))

    def _abrir_pasta_notas(self) -> None:
        if not self.trip.pasta_notas:
            messagebox.showinfo("Pasta", "Nenhum projeto aberto.")
            return
        pasta = resolve_path(self.trip.pasta_notas)
        pasta.mkdir(parents=True, exist_ok=True)
        abrir_pasta_no_explorer(pasta)

    def _abrir_pasta_relatorios(self) -> None:
        if not self.trip.pasta_saida and not self.trip.pasta_raiz:
            messagebox.showinfo("Pasta", "Nenhum projeto aberto.")
            return
        pasta_saida = resolve_path(self.trip.pasta_saida)
        pasta_saida.mkdir(parents=True, exist_ok=True)
        pasta = pasta_relatorios(pasta_saida)
        pasta.mkdir(parents=True, exist_ok=True)
        abrir_pasta_no_explorer(pasta)

    def _abrir_pasta_modelo_rdv(self) -> None:
        import subprocess
        import sys

        PASTA_MODELO_RDV.mkdir(parents=True, exist_ok=True)
        template = _template_path(reload_mapeamento_rdv())
        if template.is_file() and sys.platform.startswith("win"):
            try:
                subprocess.run(
                    ["explorer", "/select,", str(template.resolve())],
                    check=False,
                )
                return
            except OSError:
                pass
        abrir_pasta_no_explorer(PASTA_MODELO_RDV)

    def _editar_mapeamento_rdv(self) -> None:
        def _apos_salvar() -> None:
            self._set_status("Mapeamento do modelo RDV atualizado.")

        MapeamentoRdvDialog(self, on_saved=_apos_salvar)

    def _gerenciar_categorias(self) -> None:
        CategoriasDialog(self, on_changed=self._atualizar_lista_categorias)

    def _atualizar_lista_categorias(self) -> None:
        atual = self.cmb_cat.get()
        self.categorias = categorias_completas(self.config_data)
        self.cmb_cat.configure(values=self.categorias)
        if atual in self.categorias:
            self.cmb_cat.set(atual)
        elif "Outros" in self.categorias:
            self.cmb_cat.set("Outros")
        self._set_status("Lista de categorias atualizada.")

    def _alternar_tema_aplicacao(self) -> None:
        alternar_tema()
        forcar_redesenho_tema(self)

    def refresh_apos_tema(self) -> None:
        """Hook chamado por forcar_redesenho_tema após alternar claro/escuro."""
        try:
            self._barra_menu_frame.configure(fg_color=COR_FUNDO_SECUNDARIO)
            self._barra_projeto.configure(fg_color=COR_FUNDO_SECUNDARIO)
        except Exception:
            pass
        try:
            self.tabview.configure(**{k: v for k, v in opcoes_tabview_ctk().items() if k != "anchor"})
            configurar_abas_tabview(self.tabview)
        except Exception:
            pass
        try:
            self.notas_paned.configure(bg=resolver_cor(COR_FUNDO_CARD))
        except Exception:
            pass
        self._sync_preview_chrome()
        try:
            self._refresh_list()
        except Exception:
            pass
        try:
            self._refresh_previa_visual()
        except Exception:
            pass

    def _mostrar_dialogo_conteudo_ajuda(
        self,
        caminho: Path,
        preencher: Callable[[Any, dict[str, Any]], None],
        titulo_padrao: str,
    ) -> None:
        """Abre janela com texto formatado a partir de um JSON em assets/."""
        try:
            doc = carregar_documento_ajuda(caminho)
        except FileNotFoundError:
            messagebox.showerror(
                "Ajuda",
                f"Ficheiro não encontrado:\n\n{caminho}",
                parent=self,
            )
            return
        except (json.JSONDecodeError, ValueError, OSError) as erro:
            messagebox.showerror(
                "Ajuda",
                f"Não foi possível ler o conteúdo:\n\n{erro}",
                parent=self,
            )
            return
        titulo_janela = str(doc.get("titulo", titulo_padrao) or titulo_padrao).strip()
        topo = ctk.CTkToplevel(self)
        topo.title(titulo_janela)
        topo.transient(self)
        topo.geometry("760x620")
        topo.minsize(520, 400)
        ctk.CTkLabel(
            topo,
            text=str(caminho),
            font=ctk.CTkFont(size=11),
            text_color=("#666666", "#AAAAAA"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 4))
        corpo = ctk.CTkFrame(topo, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        texto = scrolledtext.ScrolledText(
            corpo,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            padx=8,
            pady=8,
            **opcoes_texto_tk_embutido(),
        )
        texto.pack(fill="both", expand=True)
        configurar_tags_texto_ajuda(texto)
        preencher(texto, doc)
        texto.configure(state=tk.DISABLED)
        ctk.CTkButton(topo, text="Fechar", command=topo.destroy).pack(pady=12)

    def _mostrar_manual_ajuda(self) -> None:
        self._mostrar_dialogo_conteudo_ajuda(
            arquivo_manual_ajuda(),
            preencher_widget_manual,
            "Manual",
        )

    def _mostrar_sobre_ajuda(self) -> None:
        self._mostrar_dialogo_conteudo_ajuda(
            arquivo_sobre_ajuda(),
            preencher_widget_sobre,
            "Sobre",
        )

    def _on_tab_change(self) -> None:
        try:
            tab = self.tabview.get()
        except Exception:
            return
        if tab == "Notas e relatório" and getattr(self, "_last_tab", None) != tab:
            n = self._sync_novas_notas(silent=True)
            if n:
                self._set_status(f"{len(n)} nova(s) nota(s) na pasta. Use Extrair para ler.")
            self.after(100, self._restaurar_layout_notas)
        if tab == "Prévia visual":
            self._refresh_previa_visual()
        self._last_tab = tab

    def _sync_novas_notas(self, *, silent: bool = False) -> list:
        """Inclui arquivos novos da pasta sem alterar notas já cadastradas."""
        if self._busy:
            return []
        if not self.trip.pasta_raiz:
            return []
        antes = len(self.trip.receipts)
        novos, _ = sync_receipts_from_folder(self.trip, self.config_data)
        # Persist se houve notas novas OU se a deduplicação removeu entradas
        if novos or len(self.trip.receipts) != antes:
            self._persist()
            self._refresh_list()
            self._refresh_totais()
        elif not silent:
            self._refresh_list()
        return novos

    def _on_close(self) -> None:
        try:
            self._salvar_layout_notas()
        except Exception:
            pass
        if self._bg_thread is not None and self._bg_thread.is_alive():
            try:
                self._set_status("A gravar… aguarde a operação em curso.")
                self.update_idletasks()
            except Exception:
                pass
            self._bg_thread.join(timeout=25.0)
        try:
            if self.trip.pasta_raiz:
                self._persist()
        except Exception:
            pass
        self.destroy()

    # ---------- helpers ----------

    def _set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _refresh_motor_label(self) -> None:
        motor = load_user_settings().get("motor", "local")
        self.lbl_motor.configure(text=f"Motor: {motor_label(motor)}")

    def _open_settings(self) -> None:
        SettingsDialog(self, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        self.config_data = apply_settings_to_config(load_config())
        self.confianca_minima = float(self.config_data.get("confianca_minima", 0.7))
        self._atualizar_lista_categorias()
        self._refresh_motor_label()
        self._set_status(f"Configurações salvas. Motor: {motor_label(load_user_settings().get('motor', 'local'))}")

    def _persist(self) -> None:
        if not self.trip.pasta_raiz:
            return
        with self._persist_lock:
            self._sync_meta_from_fields()
            save_trip(self.trip)

    def _parse_brl_input(self, text: str) -> float | None:
        t = (text or "").strip()
        if not t:
            return None
        t = t.replace("R$", "").replace(" ", "")
        if "," in t and "." in t:
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")
            else:
                t = t.replace(",", "")
        elif "," in t:
            t = t.replace(".", "").replace(",", ".")
        return float(t)

    def _sync_meta_from_fields(self) -> None:
        for campo, entry in self._widgets_cabecalho.items():
            texto = entry.get().strip()
            if campo == "adiantamento":
                if not texto:
                    self.trip.adiantamento = None
                else:
                    try:
                        self.trip.adiantamento = self._parse_brl_input(texto)
                    except ValueError:
                        pass
                continue
            if campo in ("inicio_contratual", "termino_contratual"):
                setattr(self.trip, campo, texto or None)
                continue
            setattr(self.trip, campo, texto)
        self.trip.observacoes = self.txt_obs_projeto.get("1.0", "end").strip()

    def _save_trip_meta(self) -> None:
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Salvar", "Crie ou abra um projeto antes de salvar.")
            return
        for campo in ("inicio_contratual", "termino_contratual"):
            data = self._widgets_cabecalho[campo].get().strip()
            if data:
                try:
                    datetime.strptime(data, "%Y-%m-%d")
                except ValueError:
                    rotulo = ROTULOS_CABECALHO.get(campo, campo)
                    messagebox.showerror("Data inválida", f"{rotulo}: use AAAA-MM-DD.")
                    return
        raw_adiant = self._widgets_cabecalho["adiantamento"].get().strip()
        if raw_adiant:
            try:
                self._parse_brl_input(raw_adiant)
            except ValueError:
                messagebox.showerror("Adiantamento", "Informe um valor numérico válido (ex.: 1500,00).")
                return
        # Se chave mudou no formulário, oferece renomear pasta
        c = self._widgets_cabecalho["contratante"].get().strip()
        n = self._widgets_cabecalho["natureza_servico"].get().strip()
        chave_mudou = (
            c
            and n
            and (
                c != (self.trip.contratante or "").strip()
                or n != (self.trip.natureza_servico or "").strip()
            )
        )
        self._persist()
        if chave_mudou:
            if messagebox.askyesno(
                "Chave do projeto",
                "Contratante ou Natureza do serviço foram alterados.\n"
                "Deseja atualizar a chave e renomear a pasta do projeto?",
            ):
                try:
                    self._liberar_recursos_ficheiros_projeto()
                    pasta_atual = resolve_path(self.trip.pasta_raiz)
                    trip, pasta, aviso = atualizar_chave_projeto(self.trip, pasta_atual, c, n)
                    self._carregar_projeto_em_ui(trip, pasta, sync=False)
                    if aviso:
                        messagebox.showwarning("Chave atualizada", aviso)
                except Exception as e:
                    messagebox.showerror("Chave", str(e))
        self._refresh_project_combo()
        self.lbl_pasta_projeto.configure(text=str(resolve_path(self.trip.pasta_raiz)))
        self._set_status("Dados do projeto salvos.")

    def _liberar_recursos_ficheiros_projeto(self) -> None:
        """Liberta preview/imagens para o Windows permitir renomear a pasta."""
        import gc

        try:
            self._clear_preview()
        except Exception:
            pass
        self.selected_id = None
        self._photo = None
        self._tk_photo = None
        self._preview_pil = None
        try:
            self.update_idletasks()
        except Exception:
            pass
        gc.collect()
        try:
            self.update_idletasks()
        except Exception:
            pass

    def _get_receipt(self, rid: str | None = None) -> Receipt | None:
        rid = rid or self.selected_id
        if not rid:
            return None
        for r in self.trip.receipts:
            if r.id == rid:
                return r
        return None

    def _refresh_totais(self) -> None:
        self._sincronizar_combos_periodo()
        # Total do cabeçalho: sempre o projeto inteiro (evita achar que "sumiu" dinheiro)
        total_proj = self.trip.total_geral()
        periodo = self._view_period.get()
        if periodo == "todos":
            self.lbl_totais.configure(text=f"Total: {format_brl(total_proj)}")
        else:
            visao = self._receipts_para_visao(incluir_excluidas=False)
            sub = 0.0
            for r in visao:
                if r.valor is None:
                    continue
                try:
                    sub += float(r.valor)
                except (TypeError, ValueError):
                    pass
            self.lbl_totais.configure(
                text=f"Total projeto: {format_brl(total_proj)}  ·  Filtro: {format_brl(sub)}"
            )
        try:
            self._refresh_previa_visual()
        except Exception:
            pass

    def _montar_mapa_periodo(self) -> tuple[list[str], dict[str, str]]:
        """Opções do combo de mês: rótulo → chave (todos|sem_data|YYYY-MM)."""
        mapa: dict[str, str] = {"Todos os meses": "todos"}
        valores = ["Todos os meses"]
        for am, qtd, tot in resumo_por_mes(self.trip.receipts):
            rotulo = f"{rotulo_mes(am)}  ({qtd} · {format_brl(tot)})"
            mapa[rotulo] = am
            valores.append(rotulo)
        n_sem = len(notas_sem_data(self.trip.receipts))
        if n_sem:
            rotulo_sd = f"Sem data  ({n_sem})"
            mapa[rotulo_sd] = "sem_data"
            valores.append(rotulo_sd)
        return valores, mapa

    def _sincronizar_combos_periodo(self) -> None:
        valores, mapa = self._montar_mapa_periodo()
        self._period_combo_map = mapa
        chave_atual = self._view_period.get()
        # Encontra rótulo correspondente à chave atual
        rotulo_sel = "Todos os meses"
        for rotulo, chave in mapa.items():
            if chave == chave_atual:
                rotulo_sel = rotulo
                break
        else:
            self._view_period.set("todos")
            rotulo_sel = "Todos os meses"

        for combo_name in ("combo_periodo_notas", "combo_periodo_previa"):
            combo = getattr(self, combo_name, None)
            if combo is None:
                continue
            try:
                combo.configure(values=valores)
                combo.set(rotulo_sel)
            except Exception:
                pass

    def _on_view_period_change(self, escolha: str | None = None) -> None:
        if escolha is None:
            escolha = ""
            for name in ("combo_periodo_notas", "combo_periodo_previa"):
                c = getattr(self, name, None)
                if c is not None:
                    try:
                        escolha = c.get()
                        break
                    except Exception:
                        pass
        chave = self._period_combo_map.get(escolha, "todos")
        # Se o mapa ainda não tem a escolha, tenta pelo texto
        if escolha and escolha not in self._period_combo_map:
            if escolha.startswith("Sem data"):
                chave = "sem_data"
            elif escolha == "Todos os meses":
                chave = "todos"
            else:
                # Extrai YYYY-MM do início do rótulo via mapa reconstruído
                _, mapa = self._montar_mapa_periodo()
                chave = mapa.get(escolha, "todos")
                self._period_combo_map = mapa
        self._view_period.set(chave)
        self._sincronizar_combos_periodo()
        self._refresh_list()
        self._refresh_totais()

    def _rotulo_periodo_atual(self) -> str:
        chave = self._view_period.get()
        if chave == "todos":
            return "Todos os meses"
        if chave == "sem_data":
            return "Sem data"
        return rotulo_mes(chave)

    def _receipts_para_visao(self, *, incluir_excluidas: bool = False) -> list[Receipt]:
        if incluir_excluidas:
            base = list(self.trip.receipts)
        else:
            base = [r for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
        chave = self._view_period.get()
        if chave == "todos":
            return base
        if chave == "sem_data":
            return [r for r in base if not ano_mes_de(r.data)]
        return [r for r in base if ano_mes_de(r.data) == chave]

    def _visible_receipts(self) -> list[Receipt]:
        if self._list_scope.get() == "excluidas":
            base = [r for r in self.trip.receipts if r.status == ReceiptStatus.EXCLUIDA]
        else:
            base = [r for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
        chave = self._view_period.get()
        if chave == "todos":
            return base
        if chave == "sem_data":
            return [r for r in base if not ano_mes_de(r.data)]
        return [r for r in base if ano_mes_de(r.data) == chave]

    def _atualizar_indice_duplicatas(self) -> None:
        """Recalcula fingerprints; marca em roxo só a nota mais recente de cada grupo."""
        self._dup_grupos = indice_duplicatas(self.trip.receipts)
        self._dup_ids = ids_marcacao_duplicata(self.trip.receipts)

    def _receipt_badge(self, r: Receipt) -> str:
        cond = self._classificar_nota(r)
        return {
            "excluida": "[X]",
            "erro": "[ERR]",
            "sem_extrair": "[···]",
            "sem_valor": "[$]",
            "sem_data": "[DT]",
            "duplicata": "[DUP]",
            "atencao": "[!]",
            "verificada": "[OK]",
            "a_verificar": "[ ]",
        }.get(cond, "[ ]")

    def _classificar_nota(self, r: Receipt) -> str:
        """Condição visual para cor/legenda da lista."""
        if r.status == ReceiptStatus.EXCLUIDA:
            return "excluida"
        fonte = (r.fonte_extracao or "").strip().lower()
        if not fonte or fonte == "pendente":
            return "sem_extrair"
        if fonte == "falha" or fonte.startswith("falha"):
            return "erro"
        if r.valor is None:
            return "sem_valor"
        if not r.data:
            return "sem_data"
        if r.id in self._dup_ids:
            return "duplicata"
        if r.status == ReceiptStatus.CONFERIDA:
            return "verificada"
        if r.confianca < self.confianca_minima:
            return "atencao"
        return "a_verificar"

    def _estilo_nota_lista(self, r: Receipt) -> tuple[str, object, object]:
        """Retorna (rótulo_curto, cor_faixa, cor_fundo)."""
        cond = self._classificar_nota(r)
        mapa = {
            "sem_extrair": ("Sem extrair", COR_NOTA_SEM_EXTRAIR, COR_NOTA_BG_SEM_EXTRAIR),
            "erro": ("Erro", COR_NOTA_ERRO, COR_NOTA_BG_ERRO),
            "sem_valor": ("Sem valor", COR_NOTA_SEM_VALOR, COR_NOTA_BG_SEM_VALOR),
            "sem_data": ("Sem data", COR_NOTA_SEM_DATA, COR_NOTA_BG_SEM_DATA),
            "duplicata": ("Duplicata", COR_NOTA_DUPLICATA, COR_NOTA_BG_DUPLICATA),
            "atencao": ("Revisar", COR_NOTA_ATENCAO, COR_NOTA_BG_ATENCAO),
            "a_verificar": ("A verificar", COR_NOTA_A_VERIFICAR, COR_NOTA_BG_A_VERIFICAR),
            "verificada": ("Verificada", COR_NOTA_VERIFICADA, COR_NOTA_BG_VERIFICADA),
            "excluida": ("Excluída", COR_NOTA_EXCLUIDA, COR_NOTA_BG_EXCLUIDA),
        }
        return mapa.get(cond, mapa["a_verificar"])

    def _texto_base_item(self, r: Receipt) -> str:
        status_txt, _, _ = self._estilo_nota_lista(r)
        valor = format_brl(r.valor)
        data = r.data or "sem data"
        if r.data and r.hora:
            data = f"{r.data} {r.hora}"
        nome = (r.estabelecimento or Path(r.arquivo).name)[:36]
        extra = ""
        if r.origem_pdf and r.pagina:
            extra = f"  (p{r.pagina}"
            if r.parte:
                extra += f".{r.parte}"
            extra += ")"
        return f"{status_txt}  ·  {data}  ·  {valor}  ·  {nome}{extra}"

    def _aplicar_visual_item(self, r: Receipt) -> None:
        linha = self._list_item_widgets.get(r.id)
        if linha is None:
            return
        _, cor_faixa, cor_fundo = self._estilo_nota_lista(r)
        texto_base = self._texto_base_item(r)
        linha._texto_base = texto_base  # type: ignore[attr-defined]
        linha._cor_faixa = cor_faixa  # type: ignore[attr-defined]
        try:
            linha.configure(fg_color=cor_fundo)
            faixa = getattr(linha, "_faixa_nota", None)
            if faixa is not None:
                faixa.configure(fg_color=COR_PRIMARIA if r.id == self.selected_id else cor_faixa)
        except Exception:
            pass

    def _add_list_item(self, r: Receipt) -> None:
        _, cor_faixa, cor_fundo = self._estilo_nota_lista(r)
        texto_base = self._texto_base_item(r)
        selecionada = r.id == self.selected_id
        linha = ctk.CTkFrame(
            self.list_frame,
            fg_color=cor_fundo,
            corner_radius=8,
            height=36,
            border_width=3 if selecionada else 0,
            border_color=COR_PRIMARIA,
        )
        linha.pack(fill="x", padx=2, pady=2)
        linha.pack_propagate(False)

        faixa = ctk.CTkFrame(
            linha,
            width=10 if selecionada else 6,
            fg_color=COR_PRIMARIA if selecionada else cor_faixa,
            corner_radius=4,
        )
        faixa.pack(side="left", fill="y", padx=(3, 6), pady=3)
        faixa.pack_propagate(False)

        prefixo = "▶  " if selecionada else ""
        lbl = ctk.CTkLabel(
            linha,
            text=f"{prefixo}{texto_base}",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=11, weight="bold" if selecionada else "normal"),
        )
        lbl.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=4)

        def _sel(_event=None, rid=r.id):
            self._select_receipt(rid)

        for w in (linha, faixa, lbl):
            w.bind("<Button-1>", _sel)

        self._list_item_widgets[r.id] = linha
        linha._lbl_nota = lbl  # type: ignore[attr-defined]
        linha._faixa_nota = faixa  # type: ignore[attr-defined]
        linha._cor_faixa = cor_faixa  # type: ignore[attr-defined]
        linha._texto_base = texto_base  # type: ignore[attr-defined]

    def _atualizar_destaque_lista(self) -> None:
        """Destaca a nota aberta na visualização/conferência."""
        for rid, linha in self._list_item_widgets.items():
            sel = rid == self.selected_id
            try:
                linha.configure(border_width=3 if sel else 0, border_color=COR_PRIMARIA)
                faixa = getattr(linha, "_faixa_nota", None)
                lbl = getattr(linha, "_lbl_nota", None)
                base = getattr(linha, "_texto_base", "")
                cor_faixa = getattr(linha, "_cor_faixa", COR_PRIMARIA)
                if faixa is not None:
                    faixa.configure(width=10 if sel else 6, fg_color=COR_PRIMARIA if sel else cor_faixa)
                if lbl is not None:
                    lbl.configure(
                        text=("▶  " if sel else "") + base,
                        font=ctk.CTkFont(size=11, weight="bold" if sel else "normal"),
                    )
            except Exception:
                pass

    def _on_scope_change(self) -> None:
        self.selected_id = None
        self._refresh_list()
        self._clear_form_if_needed()

    def _atualizar_contagem_escopo(self, n_ativas: int, n_excl: int) -> None:
        """Atualiza os rótulos Ativas (N) / Excluídas (N) lado a lado."""
        try:
            self.radio_scope_ativas.configure(text=f"Ativas ({n_ativas})")
            self.radio_scope_excluidas.configure(text=f"Excluídas ({n_excl})")
        except Exception:
            pass

    def _clear_form_if_needed(self) -> None:
        if self.selected_id:
            return
        self._clear_preview("Selecione uma nota")
        self.lbl_arquivo.configure(text="Arquivo: —")
        self.lbl_fonte.configure(text="Fonte: —")
        self.lbl_duplicata.configure(text="")
        self.lbl_conf.configure(text="Confiança: —")
        self.lbl_zoom.configure(text="—")
        self._update_action_buttons(None)
        self._update_crop_undo_button(None)

    def _clear_preview(self, message: str = "") -> None:
        """Limpa o preview sem deixar referência Tk quebrada (pyimage)."""
        self._exit_crop_mode(silent=True)
        self._panning = False
        self._preview_pil = None
        self._tk_photo = None
        self._photo = None
        self._disp_size = (1, 1)
        self._img_offset = (0, 0)
        self._scroll_focus = None
        try:
            self._sync_preview_chrome()
            self.preview_canvas.delete("all")
            self.preview_canvas.configure(
                scrollregion=(0, 0, 400, 300), cursor=self._preview_idle_cursor()
            )
            fill = "#9898b0" if self._is_dark_ui() else "#5f5f78"
            self.preview_canvas.create_text(
                200,
                150,
                text=message or "Selecione uma nota",
                fill=fill,
                tags="placeholder",
                font=("Segoe UI", 12),
            )
            self.preview_canvas.xview_moveto(0)
            self.preview_canvas.yview_moveto(0)
        except Exception:
            pass

    def _update_action_buttons(self, r: Receipt | None) -> None:
        excluida = bool(r and r.status == ReceiptStatus.EXCLUIDA)
        conferida = bool(r and r.status == ReceiptStatus.CONFERIDA)
        tem_ia = bool(
            r
            and (
                (getattr(r, "ia_resposta", None) or "").strip()
                or (getattr(r, "ia_prompt", None) or "").strip()
            )
        )
        for btn in (
            self.btn_aplicar,
            self.btn_conferida,
            self.btn_desfazer_conferida,
            self.btn_excluir,
            self.btn_restaurar,
            self.btn_reextrair,
            self.btn_conversa_ia,
        ):
            btn.pack_forget()
        self.btn_aplicar.pack(fill="x", pady=2)
        if excluida:
            self.btn_restaurar.pack(fill="x", pady=2)
        else:
            if conferida:
                self.btn_desfazer_conferida.pack(fill="x", pady=2)
            else:
                self.btn_conferida.pack(fill="x", pady=2)
            self.btn_reextrair.pack(fill="x", pady=2)
            self.btn_conversa_ia.pack(fill="x", pady=2)
            self.btn_conversa_ia.configure(
                state="normal" if tem_ia else "disabled",
                text="Ver conversa com a IA" if tem_ia else "Ver conversa com a IA (sem dados)",
            )
            self.btn_excluir.pack(fill="x", pady=2)

    def _refresh_list(self) -> None:
        self._sincronizar_combos_periodo()
        self._atualizar_indice_duplicatas()

        mode = self._group_mode.get()
        receipts = self._visible_receipts()
        n_ativas = sum(1 for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA)
        n_excl = sum(1 for r in self.trip.receipts if r.status == ReceiptStatus.EXCLUIDA)
        self._atualizar_contagem_escopo(n_ativas, n_excl)

        def sort_key(r: Receipt):
            return (r.data_ordenacao().isoformat(), r.estabelecimento or Path(r.arquivo).name)

        def _rebuild() -> None:
            for w in self.list_frame.winfo_children():
                w.destroy()
            self._list_item_widgets.clear()

        if not receipts:
            self._list_sig = None
            _rebuild()
            if self._list_scope.get() == "excluidas":
                msg = "Nenhuma nota excluída neste filtro."
            elif self._view_period.get() != "todos":
                msg = f"Nenhuma nota em «{self._rotulo_periodo_atual()}»."
            else:
                msg = "Nenhuma nota ativa."
            ctk.CTkLabel(self.list_frame, text=msg, text_color=COR_TEXTO_SECUNDARIO).pack(padx=8, pady=12)
            return

        if mode == "categoria":
            _rebuild()
            groups: dict[str, list[Receipt]] = {}
            for r in receipts:
                groups.setdefault(r.categoria, []).append(r)
            ordered: list[Receipt] = []
            for cat in sorted(groups.keys()):
                ctk.CTkLabel(self.list_frame, text=cat, font=ctk.CTkFont(weight="bold")).pack(
                    anchor="w", padx=4, pady=(8, 2)
                )
                for r in sorted(groups[cat], key=sort_key):
                    self._add_list_item(r)
                    ordered.append(r)
            self._list_sig = None
        elif mode == "data":
            _rebuild()
            groups = {}
            for r in receipts:
                groups.setdefault(r.data or "Sem data", []).append(r)
            for d in sorted(groups.keys()):
                ctk.CTkLabel(self.list_frame, text=d, font=ctk.CTkFont(weight="bold")).pack(
                    anchor="w", padx=4, pady=(8, 2)
                )
                for r in sorted(groups[d], key=sort_key):
                    self._add_list_item(r)
            self._list_sig = None
        else:
            ordered = sorted(receipts, key=sort_key)
            ids = tuple(r.id for r in ordered)
            sig = (mode, self._list_scope.get(), self._view_period.get(), ids)
            if (
                sig == self._list_sig
                and self._list_item_widgets
                and all(rid in self._list_item_widgets for rid in ids)
            ):
                for r in ordered:
                    self._aplicar_visual_item(r)
                self._atualizar_destaque_lista()
                return
            _rebuild()
            for r in ordered:
                self._add_list_item(r)
            self._list_sig = sig

    def _select_receipt(self, rid: str) -> None:
        self.selected_id = rid
        r = self._get_receipt(rid)
        if not r:
            return

        self.lbl_arquivo.configure(text=f"Arquivo: {Path(r.arquivo).name}")
        fonte_txt = f"Fonte: {r.fonte_extracao or '—'} | Status: {r.status.value}"
        if getattr(r, "cnpj", None):
            fonte_txt += f" | CNPJ {r.cnpj}"
        self.lbl_fonte.configure(text=fonte_txt)
        self.lbl_conf.configure(text=f"Confiança: {r.confianca:.0%}")

        if r.id in self._dup_ids and r.status != ReceiptStatus.EXCLUIDA:
            n_pares = 0
            for grupo in self._dup_grupos.values():
                if r.id in grupo:
                    n_pares = len(grupo) - 1
                    break
            motivo = motivo_duplicata(r)
            self.lbl_duplicata.configure(
                text=f"Duplicata (mais recente do grupo): {motivo} — "
                f"coincide com {n_pares} outra(s) nota(s)."
            )
        else:
            self.lbl_duplicata.configure(text="")

        self.ent_valor.delete(0, "end")
        if r.valor is not None:
            self.ent_valor.insert(0, f"{r.valor:.2f}".replace(".", ","))

        self.ent_data.delete(0, "end")
        if r.data:
            self.ent_data.insert(0, r.data)

        self.ent_hora.delete(0, "end")
        if r.hora:
            self.ent_hora.insert(0, r.hora)

        self.ent_estab.delete(0, "end")
        self.ent_estab.insert(0, r.estabelecimento)

        if r.categoria in self.categorias:
            self.cmb_cat.set(r.categoria)
        elif r.categoria:
            # Mantém categoria custom/desconhecida visível no combo
            vals = list(self.categorias)
            if r.categoria not in vals:
                vals = [r.categoria] + [v for v in vals if v != "Outros"] + ["Outros"]
                self.cmb_cat.configure(values=vals)
            self.cmb_cat.set(r.categoria)
        else:
            self.cmb_cat.set("Outros")

        self.txt_obs.delete("1.0", "end")
        self.txt_obs.insert("1.0", r.observacoes)

        self._update_action_buttons(r)
        self._update_crop_undo_button(r)
        self._atualizar_destaque_lista()
        self._show_preview(r)

    def _resolve_path(self, arquivo: str) -> Path:
        p = Path(arquivo)
        if p.is_absolute():
            return p
        return (ROOT / p).resolve()

    def _show_preview(self, r: Receipt) -> None:
        self._exit_crop_mode(silent=True)
        path = self._resolve_path(r.arquivo)
        if not path.exists():
            self._clear_preview(f"Arquivo não encontrado:\n{path}")
            self.lbl_zoom.configure(text="—")
            return

        try:
            if path.suffix.lower() == ".pdf":
                import pypdfium2 as pdfium

                pdf = pdfium.PdfDocument(str(path))
                try:
                    page = pdf[0]
                    img = page.render(scale=2.0).to_pil()
                finally:
                    pdf.close()
            else:
                img = Image.open(path)
                img.load()  # força leitura antes de fechar arquivo

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            # Cópia independente — evita imagem "preguiçosa" ligada a arquivo movido/fechado
            img = img.copy()

            if r.rotacao:
                img = img.rotate(-r.rotacao, expand=True)

            self._preview_pil = img
            self._zoom = self._calc_fit_zoom()
            self._fit_zoom = self._zoom
            self._scroll_focus = None
            self._render_preview()
            self._center_preview_view()
        except Exception as e:
            self._clear_preview(f"Erro ao abrir imagem:\n{e}")
            self.lbl_zoom.configure(text="—")

    def _preview_canvas_bg(self) -> str:
        """Fundo do canvas alinhado ao card do tema (claro/escuro)."""
        return resolver_cor(COR_FUNDO_CARD)

    def _is_dark_ui(self) -> bool:
        try:
            appearance = ctk.get_appearance_mode()
            if appearance == "Dark":
                return True
            if appearance == "Light":
                return False
        except Exception:
            pass
        from app.gui.tema import MODO_ATUAL

        return MODO_ATUAL == "dark"

    def _sync_preview_chrome(self) -> None:
        """Alinha fundo do canvas ao painel; barras usam o tema padrão do CTk."""
        self._preview_bg = self._preview_canvas_bg()
        try:
            self.preview_canvas.configure(bg=self._preview_bg)
            placeholder_fill = resolver_cor(COR_TEXTO_SECUNDARIO)
            for item in self.preview_canvas.find_withtag("placeholder"):
                self.preview_canvas.itemconfigure(item, fill=placeholder_fill)
        except Exception:
            pass

    def _preview_viewport_size(self) -> tuple[int, int]:
        """Área útil estável para o fit — NÃO usa o tamanho da imagem/conteúdo.

        Medir o CTkScrollableFrame causa ciclo: imagem menor → área reportada menor →
        próximo Ajustar encolhe de novo (~1% por clique).
        """
        self.update_idletasks()
        try:
            # Largura da coluna central via sashes do PanedWindow (independente da imagem)
            x0, _ = self.notas_paned.sash_coord(0)
            x1, _ = self.notas_paned.sash_coord(1)
            center_w = max(int(x1 - x0), int(self.preview_center.winfo_width()), 200)
            center_h = max(int(self.notas_paned.winfo_height()), int(self.preview_center.winfo_height()), 200)

            chrome = 28  # padding do scroll
            for bar in (self.preview_rot_bar, self.preview_crop_bar):
                # reqheight é estável (não muda com o zoom da imagem)
                chrome += max(int(bar.winfo_reqheight()), 36) + 10

            w = center_w - 36
            h = center_h - chrome
            w = max(w, 200)
            h = max(h, 200)
            self._viewport_cache = (w, h)
            return w, h
        except Exception:
            return self._viewport_cache

    def _calc_fit_zoom(self) -> float:
        """Maior zoom em que a imagem inteira cabe na área (pode ser > 100%)."""
        if self._preview_pil is None:
            return 1.0
        max_w, max_h = self._preview_viewport_size()
        iw, ih = self._preview_pil.size
        if iw <= 0 or ih <= 0:
            return 1.0
        zoom = min(max_w / float(iw), max_h / float(ih))
        return max(0.05, min(zoom, 8.0))

    def _zoom_fit(self) -> None:
        if self._preview_pil is None:
            return
        self.update_idletasks()
        z = self._calc_fit_zoom()
        if abs(z - self._zoom) < 0.005 and abs(z - self._fit_zoom) < 0.005:
            self.lbl_zoom.configure(text=f"{int(round(self._zoom * 100))}%")
            self._center_preview_view()
            return
        self._zoom = z
        self._fit_zoom = z
        self._scroll_focus = None
        self._render_preview()
        self._center_preview_view()

    def _render_preview(self) -> None:
        if self._preview_pil is None:
            return
        iw, ih = self._preview_pil.size
        self._zoom = max(0.05, min(self._zoom, 8.0))
        disp_w = max(1, int(iw * self._zoom))
        disp_h = max(1, int(ih * self._zoom))

        max_pixels = 4_000_000
        if disp_w * disp_h > max_pixels:
            scale = (max_pixels / (disp_w * disp_h)) ** 0.5
            disp_w = max(1, int(disp_w * scale))
            disp_h = max(1, int(disp_h * scale))
            self._zoom = min(self._zoom, disp_w / max(iw, 1))

        display = self._preview_pil
        if (disp_w, disp_h) != (iw, ih):
            display = self._preview_pil.resize((disp_w, disp_h), Image.Resampling.BILINEAR)

        self._disp_size = (disp_w, disp_h)

        try:
            self._sync_preview_chrome()
            self.update_idletasks()
            vw = max(int(self.preview_canvas.winfo_width()), 1)
            vh = max(int(self.preview_canvas.winfo_height()), 1)
            # Região de rolagem pelo menos do tamanho da janela → barras só quando necessário
            scroll_w = max(disp_w, vw)
            scroll_h = max(disp_h, vh)
            ox = max(0, (scroll_w - disp_w) // 2)
            oy = max(0, (scroll_h - disp_h) // 2)
            self._img_offset = (ox, oy)

            self._tk_photo = ImageTk.PhotoImage(display)
            self.preview_canvas.delete("all")
            self.preview_canvas.configure(
                scrollregion=(0, 0, scroll_w, scroll_h),
                bg=self._preview_bg,
                cursor=self._preview_idle_cursor(),
            )
            self.preview_canvas.create_image(ox, oy, anchor="nw", image=self._tk_photo, tags="img")

            if self._crop_mode and self._crop_box:
                self._draw_crop_rect(self._crop_box)

            if self._scroll_focus is not None:
                fx, fy, mx, my = self._scroll_focus
                self._scroll_focus = None
                self._scroll_to_image_point(fx, fy, mx, my)
        except Exception as e:
            self._clear_preview(f"Erro ao exibir imagem:\n{e}")
            return

        self.lbl_zoom.configure(text=f"{int(round(self._zoom * 100))}%")

    def _scroll_to_image_point(self, fx: float, fy: float, mx: float, my: float) -> None:
        """Mantém o ponto (fx,fy) da imagem sob as coords do widget (mx,my)."""
        dw, dh = self._disp_size
        ox, oy = self._img_offset
        fx = max(0.0, min(1.0, fx))
        fy = max(0.0, min(1.0, fy))
        nx = ox + fx * dw
        ny = oy + fy * dh
        try:
            bbox = self.preview_canvas.cget("scrollregion").split()
            sw = max(float(bbox[2]) - float(bbox[0]), 1.0)
            sh = max(float(bbox[3]) - float(bbox[1]), 1.0)
        except Exception:
            sw, sh = float(max(dw, 1)), float(max(dh, 1))
        left = nx - mx
        top = ny - my
        self.preview_canvas.xview_moveto(max(0.0, min(1.0, left / sw)))
        self.preview_canvas.yview_moveto(max(0.0, min(1.0, top / sh)))

    def _center_preview_view(self) -> None:
        """Centraliza a imagem na área visível."""
        try:
            self.update_idletasks()
            bbox = self.preview_canvas.cget("scrollregion").split()
            sw = max(float(bbox[2]) - float(bbox[0]), 1.0)
            sh = max(float(bbox[3]) - float(bbox[1]), 1.0)
            vw = max(int(self.preview_canvas.winfo_width()), 1)
            vh = max(int(self.preview_canvas.winfo_height()), 1)
            self.preview_canvas.xview_moveto(max(0.0, (sw - vw) / (2 * sw)))
            self.preview_canvas.yview_moveto(max(0.0, (sh - vh) / (2 * sh)))
        except Exception:
            self.preview_canvas.xview_moveto(0)
            self.preview_canvas.yview_moveto(0)

    def _canvas_to_image_fraction(self, event_x: int, event_y: int) -> tuple[float, float]:
        """Converte clique no canvas → fração (0–1) na imagem exibida."""
        dw, dh = self._disp_size
        ox, oy = self._img_offset
        cx = float(self.preview_canvas.canvasx(event_x)) - ox
        cy = float(self.preview_canvas.canvasy(event_y)) - oy
        fx = cx / max(dw, 1)
        fy = cy / max(dh, 1)
        return max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))

    def _zoom_by(self, factor: float, event=None) -> None:
        if self._preview_pil is None:
            return
        if self._crop_mode:
            self._crop_box = None
            self._crop_start = None
            self.btn_crop_apply.configure(state="disabled")
            self.lbl_crop_hint.configure(text="Zoom alterado — selecione a área novamente")

        old = self._zoom
        new = max(0.05, min(old * factor, 8.0))
        if abs(new - old) < 1e-6:
            return

        if event is not None:
            fx, fy = self._canvas_to_image_fraction(event.x, event.y)
            self._scroll_focus = (fx, fy, float(event.x), float(event.y))
        else:
            # Zoom pelos botões +/- : mantém o centro da área visível
            try:
                vw = max(int(self.preview_canvas.winfo_width()), 1)
                vh = max(int(self.preview_canvas.winfo_height()), 1)
                mx, my = vw / 2, vh / 2
                fx, fy = self._canvas_to_image_fraction(int(mx), int(my))
                self._scroll_focus = (fx, fy, mx, my)
            except Exception:
                self._scroll_focus = None

        self._zoom = new
        self._render_preview()

    def _zoom_100(self) -> None:
        if self._preview_pil is None:
            return
        self._zoom = 1.0
        self._scroll_focus = None
        self._render_preview()
        self._center_preview_view()

    def _on_preview_wheel(self, event) -> str:
        if self._preview_pil is None:
            return "break"
        if getattr(event, "delta", 0) > 0:
            self._zoom_by(1.15, event)
        elif getattr(event, "delta", 0) < 0:
            self._zoom_by(1 / 1.15, event)
        return "break"

    def _on_preview_wheel_linux(self, event, direction: int) -> str:
        if self._preview_pil is None:
            return "break"
        if direction > 0:
            self._zoom_by(1.15, event)
        else:
            self._zoom_by(1 / 1.15, event)
        return "break"

    def _on_preview_shift_wheel(self, event) -> str:
        """Shift + scroll = pan horizontal."""
        if self._preview_pil is None:
            return "break"
        delta = getattr(event, "delta", 0)
        step = -1 if delta > 0 else 1
        self.preview_canvas.xview_scroll(step, "units")
        return "break"

    def _preview_idle_cursor(self) -> str:
        if self._crop_mode:
            return "crosshair"
        if self._preview_pil is not None:
            return "fleur"
        return "arrow"

    def _on_preview_press(self, event) -> None:
        if self._crop_mode:
            self._on_crop_press(event)
            return
        self._on_pan_start(event)

    def _on_preview_drag(self, event) -> None:
        if self._crop_mode:
            self._on_crop_drag(event)
            return
        self._on_pan_drag(event)

    def _on_preview_release(self, event) -> None:
        if self._crop_mode:
            self._on_crop_release(event)
            return
        self._on_pan_end(event)

    def _on_pan_start(self, event) -> None:
        if self._preview_pil is None:
            return
        self._panning = True
        self.preview_canvas.scan_mark(event.x, event.y)
        self.preview_canvas.configure(cursor="fleur")

    def _on_pan_drag(self, event) -> None:
        if not self._panning or self._preview_pil is None:
            return
        self.preview_canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_pan_end(self, _event=None) -> None:
        self._panning = False
        try:
            self.preview_canvas.configure(cursor=self._preview_idle_cursor())
        except Exception:
            pass

    def _toggle_crop_mode(self) -> None:
        if self._crop_mode:
            self._exit_crop_mode()
            return
        if self._preview_pil is None or not self.selected_id:
            messagebox.showinfo("Recortar", "Selecione uma nota com imagem para recortar.")
            return
        r = self._get_receipt()
        if r and r.status == ReceiptStatus.EXCLUIDA:
            messagebox.showinfo("Recortar", "Restaure a nota antes de recortar.")
            return
        path = self._resolve_path(r.arquivo) if r else None
        if path and path.suffix.lower() == ".pdf":
            messagebox.showinfo(
                "Recortar",
                "Não é possível recortar PDF diretamente. Use uma página já separada (PNG/JPG).",
            )
            return
        self._crop_mode = True
        self._crop_start = None
        self._crop_box = None
        self.preview_canvas.configure(cursor="crosshair")
        self.btn_crop.configure(text="✂ Recortando…", fg_color="#e37400", hover_color="#c65300")
        self.btn_crop_apply.configure(state="disabled")
        self.btn_crop_cancel.configure(state="normal")
        self.btn_crop_undo.configure(state="disabled")
        self.lbl_crop_hint.configure(text="Arraste na imagem para selecionar a área útil")
        self._render_preview()
        self._set_status("Modo recorte: arraste para marcar a área e clique em Aplicar recorte.")

    def _cancel_crop_mode(self) -> None:
        self._exit_crop_mode()
        self._set_status("Recorte cancelado.")

    def _exit_crop_mode(self, silent: bool = False) -> None:
        was = self._crop_mode
        self._crop_mode = False
        self._crop_start = None
        self._crop_box = None
        self._crop_rect_id = None
        try:
            self.preview_canvas.delete("crop")
            self.preview_canvas.configure(cursor=self._preview_idle_cursor())
            self.btn_crop.configure(text="✂ Recortar", fg_color=COR_PRIMARIA, hover_color=COR_PRIMARIA_HOVER)
            self.btn_crop_apply.configure(state="disabled")
            self.btn_crop_cancel.configure(state="disabled")
            self.lbl_crop_hint.configure(text="")
        except Exception:
            pass
        if was and not silent and self._preview_pil is not None:
            self._render_preview()
        self._update_crop_undo_button()

    def _draw_crop_rect(self, box: tuple[int, int, int, int]) -> None:
        self.preview_canvas.delete("crop")
        ox, oy = self._img_offset
        x0, y0, x1, y1 = box
        x0, y0, x1, y1 = x0 + ox, y0 + oy, x1 + ox, y1 + oy
        dw, dh = self._disp_size
        # Máscara no entorno (coords do canvas = offset + imagem)
        fill = "#000000"
        stipple = "gray50"
        img_l, img_t = ox, oy
        img_r, img_b = ox + dw, oy + dh
        if y0 > img_t:
            self.preview_canvas.create_rectangle(
                img_l, img_t, img_r, y0, fill=fill, stipple=stipple, outline="", tags="crop"
            )
        if y1 < img_b:
            self.preview_canvas.create_rectangle(
                img_l, y1, img_r, img_b, fill=fill, stipple=stipple, outline="", tags="crop"
            )
        if x0 > img_l:
            self.preview_canvas.create_rectangle(
                img_l, y0, x0, y1, fill=fill, stipple=stipple, outline="", tags="crop"
            )
        if x1 < img_r:
            self.preview_canvas.create_rectangle(
                x1, y0, img_r, y1, fill=fill, stipple=stipple, outline="", tags="crop"
            )
        self._crop_rect_id = self.preview_canvas.create_rectangle(
            x0, y0, x1, y1, outline="#1a73e8", width=2, tags="crop"
        )

    def _on_crop_press(self, event) -> None:
        if not self._crop_mode or self._preview_pil is None:
            return
        ox, oy = self._img_offset
        # Coords no espaço da imagem (considera scroll)
        x = int(self.preview_canvas.canvasx(event.x) - ox)
        y = int(self.preview_canvas.canvasy(event.y) - oy)
        self._crop_start = (x, y)
        self._crop_box = None
        self.preview_canvas.delete("crop")
        self.btn_crop_apply.configure(state="disabled")

    def _on_crop_drag(self, event) -> None:
        if not self._crop_mode or self._crop_start is None:
            return
        ox, oy = self._img_offset
        x0, y0 = self._crop_start
        x1 = int(self.preview_canvas.canvasx(event.x) - ox)
        y1 = int(self.preview_canvas.canvasy(event.y) - oy)
        dw, dh = self._disp_size
        x0 = max(0, min(x0, dw))
        y0 = max(0, min(y0, dh))
        x1 = max(0, min(x1, dw))
        y1 = max(0, min(y1, dh))
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._crop_box = box
        self._draw_crop_rect(box)

    def _on_crop_release(self, event) -> None:
        if not self._crop_mode or self._crop_start is None:
            return
        self._on_crop_drag(event)
        self._crop_start = None
        box = self._crop_box
        if not box:
            return
        w = box[2] - box[0]
        h = box[3] - box[1]
        if w < 8 or h < 8:
            self.preview_canvas.delete("crop")
            self._crop_box = None
            self.btn_crop_apply.configure(state="disabled")
            self.lbl_crop_hint.configure(text="Seleção muito pequena — arraste novamente")
            return
        self.btn_crop_apply.configure(state="normal")
        self.lbl_crop_hint.configure(text="Área marcada — clique em Aplicar recorte")

    def _display_to_image_box(self, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Converte coords do canvas (imagem exibida) para pixels da PIL original."""
        assert self._preview_pil is not None
        iw, ih = self._preview_pil.size
        dw, dh = self._disp_size
        sx = iw / max(dw, 1)
        sy = ih / max(dh, 1)
        x0, y0, x1, y1 = box
        left = max(0, min(iw, int(round(x0 * sx))))
        top = max(0, min(ih, int(round(y0 * sy))))
        right = max(0, min(iw, int(round(x1 * sx))))
        bottom = max(0, min(ih, int(round(y1 * sy))))
        if right <= left:
            right = min(iw, left + 1)
        if bottom <= top:
            bottom = min(ih, top + 1)
        return left, top, right, bottom

    def _pre_recorte_dir(self) -> Path:
        pasta = resolve_path(self.trip.pasta_notas or "notas")
        d = pasta / "_pre_recorte"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _tem_backup_recorte(self, r: Receipt | None) -> bool:
        if not r or not (r.arquivo_pre_recorte or "").strip():
            return False
        try:
            return self._resolve_path(r.arquivo_pre_recorte).exists()
        except Exception:
            return False

    def _update_crop_undo_button(self, r: Receipt | None = None) -> None:
        r = r or self._get_receipt()
        pode = self._tem_backup_recorte(r) and not self._crop_mode
        try:
            self.btn_crop_undo.configure(state="normal" if pode else "disabled")
        except Exception:
            pass

    def _ensure_pre_crop_backup(self, r: Receipt) -> None:
        """Guarda a imagem completa antes do 1º recorte (não sobrescreve em recortes seguintes)."""
        if self._tem_backup_recorte(r):
            return
        if self._preview_pil is None:
            return
        dest = self._pre_recorte_dir() / f"{r.id}.png"
        self._preview_pil.convert("RGB").save(dest, format="PNG", optimize=True)
        try:
            r.arquivo_pre_recorte = str(dest.relative_to(ROOT))
        except ValueError:
            r.arquivo_pre_recorte = str(dest)

    def _apply_crop(self) -> None:
        if not self._crop_mode or not self._crop_box or self._preview_pil is None:
            return
        r = self._get_receipt()
        if not r:
            return
        path = self._resolve_path(r.arquivo)
        if not path.exists():
            messagebox.showerror("Recortar", f"Arquivo não encontrado:\n{path}")
            return
        if path.suffix.lower() == ".pdf":
            messagebox.showerror("Recortar", "Não é possível sobrescrever PDF. Use a imagem separada.")
            return

        left, top, right, bottom = self._display_to_image_box(self._crop_box)
        if (right - left) < 4 or (bottom - top) < 4:
            messagebox.showwarning("Recortar", "Área de recorte inválida.")
            return

        try:
            self._ensure_pre_crop_backup(r)
            cropped = self._preview_pil.crop((left, top, right, bottom)).convert("RGB")
            suffix = path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                cropped.save(path, format="JPEG", quality=92, optimize=True)
            elif suffix == ".png":
                cropped.save(path, format="PNG", optimize=True)
            elif suffix in {".webp", ".bmp", ".tif", ".tiff"}:
                cropped.save(path)
            else:
                out = path.with_suffix(".png")
                cropped.save(out, format="PNG", optimize=True)
                try:
                    rel = out.relative_to(ROOT)
                    r.arquivo = str(rel)
                except ValueError:
                    r.arquivo = str(out)

            r.rotacao = 0
            r.atualizado_em = datetime.now().isoformat(timespec="seconds")
            if r.status == ReceiptStatus.CONFERIDA:
                r.status = ReceiptStatus.PENDENTE
            self._exit_crop_mode(silent=True)
            self._persist()
            self._preview_pil = cropped
            self._zoom = self._calc_fit_zoom()
            self._fit_zoom = self._zoom
            self._scroll_focus = None
            self._render_preview()
            self._center_preview_view()
            self._refresh_list()
            self._select_receipt(r.id)
            self._set_status("Recorte aplicado. Use «Desfazer recorte» para voltar à imagem original.")
        except Exception as e:
            messagebox.showerror("Recortar", f"Falha ao salvar recorte:\n{e}")

    def _undo_crop(self) -> None:
        """Restaura a imagem ao estado anterior ao primeiro recorte."""
        r = self._get_receipt()
        if not r or not self._tem_backup_recorte(r):
            messagebox.showinfo(
                "Desfazer recorte",
                "Não há backup desta nota.\nSó é possível desfazer recortes feitos após esta atualização.",
            )
            return
        if self._crop_mode:
            self._exit_crop_mode(silent=True)

        backup = self._resolve_path(r.arquivo_pre_recorte)
        path = self._resolve_path(r.arquivo)
        try:
            img = Image.open(backup)
            img.load()
            restored = img.convert("RGB")
            suffix = path.suffix.lower()
            path.parent.mkdir(parents=True, exist_ok=True)
            if suffix in {".jpg", ".jpeg"}:
                restored.save(path, format="JPEG", quality=92, optimize=True)
            elif suffix == ".png":
                restored.save(path, format="PNG", optimize=True)
            elif suffix in {".webp", ".bmp", ".tif", ".tiff"}:
                restored.save(path)
            else:
                out = path.with_suffix(".png")
                restored.save(out, format="PNG", optimize=True)
                try:
                    r.arquivo = str(out.relative_to(ROOT))
                except ValueError:
                    r.arquivo = str(out)
                path = out

            r.rotacao = 0
            r.atualizado_em = datetime.now().isoformat(timespec="seconds")
            if r.status == ReceiptStatus.CONFERIDA:
                r.status = ReceiptStatus.PENDENTE
            self._persist()
            self._preview_pil = restored.copy()
            self._zoom = self._calc_fit_zoom()
            self._fit_zoom = self._zoom
            self._scroll_focus = None
            self._render_preview()
            self._center_preview_view()
            self._refresh_list()
            self._update_crop_undo_button(r)
            self._set_status("Recorte desfeito — imagem original restaurada.")
        except Exception as e:
            messagebox.showerror("Desfazer recorte", f"Falha ao restaurar imagem:\n{e}")

    def _rotate(self, delta: int) -> None:
        if self._crop_mode:
            self._exit_crop_mode(silent=True)
        r = self._get_receipt()
        if not r:
            return
        r.rotacao = (r.rotacao + delta) % 360
        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
        self._persist()
        self._show_preview(r)
        self._update_crop_undo_button(r)

    def _parse_valor_field(self) -> float | None:
        raw = self.ent_valor.get().strip()
        if not raw:
            return None
        raw = raw.replace("R$", "").replace(" ", "")
        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            raise ValueError("Valor inválido. Use formato 1234,56")

    def _apply_edits(self) -> None:
        r = self._get_receipt()
        if not r:
            return
        try:
            valor = self._parse_valor_field()
        except ValueError as e:
            messagebox.showerror("Valor inválido", str(e))
            return

        data = self.ent_data.get().strip() or None
        if data:
            try:
                datetime.strptime(data, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Data inválida", "Use o formato AAAA-MM-DD.")
                return

        hora_raw = self.ent_hora.get().strip()
        hora = ""
        if hora_raw:
            hora_n = normalizar_hora(hora_raw)
            if not hora_n:
                messagebox.showerror("Hora inválida", "Use o formato HH:MM (ex.: 18:48).")
                return
            hora = hora_n

        r.valor = valor
        r.data = data
        r.hora = hora
        r.estabelecimento = self.ent_estab.get().strip()
        r.categoria = self.cmb_cat.get()
        r.observacoes = self.txt_obs.get("1.0", "end").strip()
        r.fonte_extracao = r.fonte_extracao or "manual"
        if r.fonte_extracao == "falha":
            r.fonte_extracao = "manual"
        r.confianca = max(r.confianca, 1.0) if valor is not None and data else r.confianca
        atualizar_hash_conteudo(r)
        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
        self._persist()
        self._refresh_list()
        self._refresh_totais()
        self._select_receipt(r.id)
        if r.id in self._dup_ids:
            self._set_status("Correções aplicadas — atenção: nota marcada como duplicata.")
        else:
            self._set_status("Correções aplicadas.")

    def _mark_conferida(self) -> None:
        self._apply_edits()
        r = self._get_receipt()
        if not r:
            return
        if r.valor is None or not r.data:
            if not messagebox.askyesno(
                "Campos incompletos",
                "Valor ou data estão vazios. Marcar como conferida mesmo assim?",
            ):
                return
        r.status = ReceiptStatus.CONFERIDA
        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
        self._persist()
        self._refresh_list()
        self._select_receipt(r.id)
        self._set_status("Nota marcada como conferida.")

    def _unmark_conferida(self) -> None:
        r = self._get_receipt()
        if not r:
            return
        if r.status != ReceiptStatus.CONFERIDA:
            return
        r.status = ReceiptStatus.PENDENTE
        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
        self._persist()
        self._refresh_list()
        self._refresh_totais()
        self._select_receipt(r.id)
        self._set_status("Conferência removida — nota voltou para revisão.")

    def _widget_em_edicao_de_texto(self, widget) -> bool:
        """True se Del deve apagar caracteres (Entry/Text), não excluir a nota."""
        if widget is None:
            return False
        tipos_ctk = (ctk.CTkEntry, ctk.CTkTextbox, ctk.CTkComboBox)
        w = widget
        for _ in range(8):
            if w is None:
                break
            if isinstance(w, (tk.Entry, tk.Text, *tipos_ctk)):
                return True
            try:
                cls = w.winfo_class()
            except Exception:
                cls = ""
            if cls in {"Entry", "Text", "TEntry", "TCombobox"}:
                return True
            try:
                w = w.master
            except Exception:
                break
        return False

    def _on_tecla_delete(self, event=None) -> str | None:
        """Atalho Del: mesma ação do botão «Excluir nota»."""
        if self._busy:
            return "break"
        try:
            foco = self.focus_get()
        except Exception:
            foco = None
        if self._widget_em_edicao_de_texto(foco):
            return None  # deixa o campo de texto tratar o Del
        # Só na aba de notas, com nota ativa selecionada
        try:
            if self.tabview.get() != "Notas e relatório":
                return None
        except Exception:
            return None
        r = self._get_receipt()
        if not r or r.status == ReceiptStatus.EXCLUIDA:
            return "break"
        self._exclude()
        return "break"

    def _exclude(self) -> None:
        r = self._get_receipt()
        if not r:
            return
        if r.status == ReceiptStatus.EXCLUIDA:
            return
        if not messagebox.askyesno(
            "Excluir",
            "Excluir esta nota do relatório?\n\n"
            "Ela será movida para a área Excluídas e para a pasta notas/excluidas/.",
        ):
            return

        # Limpa preview ANTES de mover o arquivo (evita imagem ligada ao path antigo)
        self.selected_id = None
        self._clear_preview("Movendo nota…")
        self.update_idletasks()

        try:
            dest = move_receipt_to_excluded(r, self.config_data, pasta_notas=self.trip.pasta_notas)
        except Exception as e:
            messagebox.showerror("Erro ao excluir", str(e))
            self._select_receipt(r.id)
            return
        self._persist()
        self._list_scope.set("ativas")
        self._refresh_list()
        self._refresh_totais()
        self._clear_form_if_needed()
        self._set_status(f"Nota movida para excluídas: {Path(dest).name}")

    def _restore(self) -> None:
        r = self._get_receipt()
        if not r or r.status != ReceiptStatus.EXCLUIDA:
            return
        if not messagebox.askyesno(
            "Restaurar",
            "Restaurar esta nota para a lista ativa?\nO arquivo voltará da pasta excluidas/.",
        ):
            return
        try:
            dest = restore_receipt_from_excluded(r, self.config_data, pasta_notas=self.trip.pasta_notas)
        except Exception as e:
            messagebox.showerror("Erro ao restaurar", str(e))
            return
        self._persist()
        self._list_scope.set("ativas")
        self._refresh_list()
        self._refresh_totais()
        self._select_receipt(r.id)
        self._set_status(f"Nota restaurada: {Path(dest).name}")

    def _scan_folder(self) -> None:
        if self._busy:
            messagebox.showinfo("Ocupado", "Aguarde a extração ou o relatório em curso.")
            return
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Escanear", "Abra ou crie um projeto antes de escanear notas.")
            return
        novos = self._sync_novas_notas()
        de_pdf = sum(1 for r in novos if r.origem_pdf)
        extra = f" ({de_pdf} separada(s) de PDF)" if de_pdf else ""
        self._set_status(f"Varredura concluída. {len(novos)} novo(s) arquivo(s){extra}.")

    def _extract_pending(self) -> None:
        if self._busy:
            return
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Extrair", "Abra ou crie um projeto antes de extrair.")
            return
        # Inclui só o que foi colocado na pasta desde a última vez
        novos = self._sync_novas_notas(silent=True)
        if novos:
            self._set_status(f"{len(novos)} nova(s) nota(s) incluída(s). Extraindo só as novas…")
            self._run_extraction(novos)
            return

        pendentes = [
            r
            for r in self.trip.receipts
            if r.status != ReceiptStatus.EXCLUIDA and (not r.fonte_extracao or r.fonte_extracao == "falha")
        ]
        if not pendentes:
            pendentes = [
                r
                for r in self.trip.receipts
                if r.status == ReceiptStatus.PENDENTE and r.valor is None and not r.data
            ]
        if not pendentes:
            ativas = [r for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
            if not ativas:
                messagebox.showinfo("Extrair", "Não há notas ativas para extrair.")
                return
            if not messagebox.askyesno(
                "Extrair novamente",
                f"Todas as {len(ativas)} nota(s) ativa(s) já foram extraídas.\n\n"
                "Deseja extrair novamente?\n"
                "(Útil para atualizar hora e demais campos.)",
            ):
                return
            self._run_extraction(ativas)
            return
        self._run_extraction(pendentes)

    def _reextract_selected(self) -> None:
        r = self._get_receipt()
        if not r:
            return
        self._run_extraction([r])

    def _ver_conversa_ia(self) -> None:
        r = self._get_receipt()
        if not r:
            messagebox.showinfo(
                "Conversa com a IA",
                "Selecione uma nota para ver a conversa com a IA.",
            )
            return
        IaConversaDialog(self, r)

    def _run_extraction(self, receipts: list[Receipt]) -> None:
        if self._busy:
            return
        self._busy = True
        self.progress.set(0)
        motor = (load_user_settings().get("motor") or "local").strip().lower()
        workers = parallel_extraction_workers(motor)
        workers = max(1, min(workers, len(receipts)))
        paralelo_txt = (
            f", {workers} em paralelo"
            if workers > 1
            else ""
        )
        self._set_status(
            f"Extraindo {len(receipts)} nota(s) ({motor_label(motor)}{paralelo_txt})…"
        )

        cfg = deepcopy(self.config_data)
        if motor in {"gemini", "auto", "visao", "vision"}:
            visao = cfg.setdefault("visao", {})
            key = get_api_key("gemini")
            if key:
                try:
                    visao["modelo_gemini"] = resolver_modelo_gemini(
                        visao.get("modelo_gemini"), api_key=key
                    )
                except Exception:
                    pass

        def worker() -> None:
            total = len(receipts)
            gemini_keys = (
                list_api_keys("gemini")
                if motor in {"gemini", "auto", "visao", "vision"}
                else []
            )
            done_count = 0
            progress_lock = threading.Lock()
            erros: list[str] = []

            def process_one(index: int, r: Receipt) -> None:
                nonlocal done_count
                assigned = gemini_keys[index % len(gemini_keys)] if gemini_keys else None
                if assigned:
                    prefer_api_key("gemini", assigned)
                try:
                    path = self._resolve_path(r.arquivo)
                    try:
                        result = extract_receipt(path, cfg)
                        r.valor = result.get("valor")
                        r.data = result.get("data")
                        r.hora = result.get("hora") or ""
                        r.estabelecimento = result.get("estabelecimento") or ""
                        r.cnpj = result.get("cnpj") or ""
                        r.categoria = result.get("categoria") or "Outros"
                        r.moeda = result.get("moeda") or "BRL"
                        r.confianca = float(result.get("confianca") or 0)
                        r.fonte_extracao = result.get("fonte_extracao") or ""
                        r.ia_prompt = result.get("ia_prompt") or ""
                        r.ia_resposta = result.get("ia_resposta") or ""
                        r.ia_provedor = result.get("ia_provedor") or ""
                        r.ia_modelo = result.get("ia_modelo") or ""
                        r.ia_em = (
                            datetime.now().isoformat(timespec="seconds")
                            if (r.ia_prompt or r.ia_resposta)
                            else ""
                        )
                        r.status = ReceiptStatus.PENDENTE
                        atualizar_hash_conteudo(r)
                        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
                    except Exception as e:
                        r.fonte_extracao = "falha"
                        r.observacoes = f"Erro na extração: {e}"
                        r.hash_conteudo = ""
                        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
                        with progress_lock:
                            erros.append(f"{Path(r.arquivo).name}: {e}")
                finally:
                    clear_preferred_api_key("gemini")

                with progress_lock:
                    done_count += 1
                    atual = done_count
                self.after(0, lambda d=atual, t=total: self._on_extract_progress(d, t))
                self.after(0, self._persist_extract_parcial)

            if workers <= 1 or total <= 1:
                for i, r in enumerate(receipts):
                    process_one(i, r)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(process_one, i, r) for i, r in enumerate(receipts)
                    ]
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception:
                            pass
            falhas = list(erros)
            self.after(0, lambda f=falhas: self._on_extract_done(f))

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()

    def _persist_extract_parcial(self) -> None:
        if not self.trip.pasta_raiz:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            with self._persist_lock:
                save_trip(self.trip)
        except Exception:
            pass

    def _generate_reports(self) -> None:
        if self._busy:
            messagebox.showinfo("Ocupado", "Aguarde a extração ou o relatório em curso.")
            return
        if not self.trip.pasta_raiz:
            messagebox.showinfo("Relatório", "Abra ou crie um projeto antes de gerar o relatório.")
            return
        self._sync_meta_from_fields()
        ativos = [r for r in self.trip.receipts if r.status != ReceiptStatus.EXCLUIDA]
        if not ativos:
            messagebox.showwarning("Relatório", "Não há notas para incluir no relatório.")
            return

        pendentes = [r for r in ativos if r.status != ReceiptStatus.CONFERIDA]
        if pendentes:
            if not messagebox.askyesno(
                "Notas pendentes",
                f"Há {len(pendentes)} nota(s) ainda não conferida(s).\n"
                "Deseja continuar mesmo assim?",
            ):
                return

        n_dup = len(ids_duplicados(ativos))
        if n_dup:
            if not messagebox.askyesno(
                "Notas duplicadas",
                f"Há {n_dup} nota(s) com o mesmo conteúdo fiscal "
                "(data+hora+valor+estabelecimento).\n"
                "Imagens diferentes podem ser a mesma nota fotografada duas vezes.\n\n"
                "Deseja continuar mesmo assim?",
            ):
                return

        incompletas = [r for r in ativos if r.valor is None or not r.data]
        if incompletas:
            if not messagebox.askyesno(
                "Dados incompletos",
                f"{len(incompletas)} nota(s) sem valor ou data.\n"
                "Notas sem data NÃO entram em relatórios mensais ou por intervalo.\n"
                "Continuar?",
            ):
                return

        escopos = self._dialog_escopo_relatorio(ativos)
        if not escopos:
            return

        out_root = resolve_path(self.trip.pasta_saida)
        trip_snap = deepcopy(self.trip)
        cfg = deepcopy(self.config_data)
        self._busy = True
        self._set_status("Gerando relatório…")

        def worker() -> None:
            gerados: list[Path] = []
            erros: list[str] = []
            try:
                out_root.mkdir(parents=True, exist_ok=True)
                for i, escopo in enumerate(escopos, start=1):
                    rotulo = escopo.rotulo()
                    self.after(
                        0,
                        lambda i=i, n=len(escopos), r=rotulo: self._set_status(
                            f"Gerando {i}/{n}: {r}…"
                        ),
                    )
                    try:
                        pasta = exportar_pacote(trip_snap, out_root, escopo, cfg)
                        gerados.append(pasta)
                    except Exception as e:
                        erros.append(f"{rotulo}: {e}")
            except Exception as e:
                erros.append(str(e))
            self.after(0, lambda g=gerados, e=erros: self._on_generate_done(g, e, out_root))

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()

    def _on_generate_done(self, gerados: list[Path], erros: list[str], out_root: Path) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._busy = False
        try:
            self._persist()
        except Exception:
            pass
        pasta_idx = pasta_relatorios(out_root)
        if gerados and not erros:
            self._set_status(f"{len(gerados)} relatório(s) em {pasta_idx}")
            linhas = "\n".join(f"• {p.name}" for p in gerados)
            messagebox.showinfo(
                "Concluído",
                f"{len(gerados)} relatório(s) gerado(s).\n\n"
                f"{linhas}\n\n"
                f"Pasta: {pasta_idx}",
            )
            if messagebox.askyesno("Abrir pasta", "Abrir a pasta de relatórios agora?"):
                abrir_pasta_no_explorer(gerados[-1] if len(gerados) == 1 else pasta_idx)
        elif gerados and erros:
            self._set_status(f"Parcial: {len(gerados)} ok, {len(erros)} erro(s)")
            messagebox.showwarning(
                "Parcialmente concluído",
                f"Gerados: {len(gerados)}\nFalhas:\n" + "\n".join(erros),
            )
        else:
            messagebox.showerror("Erro ao gerar", "\n".join(erros) or "Falha desconhecida.")
            self._set_status("Erro ao gerar relatório")

    def _dialog_escopo_relatorio(self, ativos: list[Receipt]) -> list[EscopoRelatorio] | None:
        """Pergunta o tipo de relatório e retorna a lista de escopos a gerar."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Gerar relatório")
        dlg.geometry("640x560")
        dlg.minsize(560, 480)
        dlg.transient(self)
        dlg.grab_set()

        resultado: list[EscopoRelatorio] | None = None
        modo_var = tk.StringVar(value="completo")
        resumos = resumo_por_mes(ativos)
        mes_vars: dict[str, tk.BooleanVar] = {
            am: tk.BooleanVar(value=True) for am, _q, _t in resumos
        }

        frame = ctk.CTkFrame(dlg, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            frame,
            text="Escolha o tipo de relatório",
            font=FONT_TITULO,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ctk.CTkLabel(
            frame,
            text=(
                f"{len(ativos)} nota(s) ativas · Total projeto {format_brl(self.trip.total_geral())}.\n"
                "Pastas em output/ (ex.: RDV_2026-04-04_a_2026-04-10). Regenerar o mesmo período substitui essa pasta."
            ),
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        opcoes = ctk.CTkFrame(frame, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        opcoes.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for texto, val in (
            ("Completo — todas as notas (independente das datas)", "completo"),
            ("Por mês — um relatório por mês selecionado", "mes"),
            ("Entre datas — intervalo específico", "intervalo"),
        ):
            ctk.CTkRadioButton(
                opcoes,
                text=texto,
                variable=modo_var,
                value=val,
                font=FONT_INTERFACE,
                command=lambda: atualizar_paineis(),
            ).pack(anchor="w", padx=14, pady=8)

        painel = ctk.CTkFrame(frame, fg_color=COR_FUNDO_CARD, corner_radius=RAIO_BORDA)
        painel.grid(row=3, column=0, sticky="nsew", pady=4)
        painel.grid_columnconfigure(0, weight=1)
        painel.grid_rowconfigure(0, weight=1)

        # --- painel mês ---
        painel_mes = ctk.CTkFrame(painel, fg_color="transparent")
        painel_mes.grid_columnconfigure(0, weight=1)
        if not resumos:
            ctk.CTkLabel(
                painel_mes,
                text="Não há notas com data válida para relatório mensal.\n"
                "Corrija as datas ou use o relatório completo.",
                text_color=COR_ERRO,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=12, pady=12)
        else:
            ctk.CTkLabel(
                painel_mes,
                text="Selecione o(s) mês(es). Cada mês gera uma pasta separada.",
                font=FONT_DICA,
                text_color=COR_TEXTO_SECUNDARIO,
                anchor="w",
            ).pack(fill="x", padx=12, pady=(12, 6))
            scroll_m = ctk.CTkScrollableFrame(painel_mes, fg_color="transparent")
            scroll_m.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            for am, qtd, tot in resumos:
                linha = ctk.CTkFrame(scroll_m, fg_color="transparent")
                linha.pack(fill="x", pady=3)
                ctk.CTkCheckBox(
                    linha,
                    text=f"{rotulo_mes(am)}  —  {qtd} nota(s)  ·  {format_brl(tot)}",
                    variable=mes_vars[am],
                    font=FONT_INTERFACE,
                ).pack(side="left", padx=4)
            btns_m = ctk.CTkFrame(painel_mes, fg_color="transparent")
            btns_m.pack(fill="x", padx=12, pady=(0, 10))
            ctk.CTkButton(
                btns_m,
                text="Todos",
                width=80,
                command=lambda: [v.set(True) for v in mes_vars.values()],
            ).pack(side="left", padx=2)
            ctk.CTkButton(
                btns_m,
                text="Nenhum",
                width=80,
                command=lambda: [v.set(False) for v in mes_vars.values()],
            ).pack(side="left", padx=2)

        # --- painel intervalo ---
        painel_int = ctk.CTkFrame(painel, fg_color="transparent")
        painel_int.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            painel_int,
            text="Informe as datas (AAAA-MM-DD). Notas sem data ficam de fora.",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))
        ctk.CTkLabel(painel_int, text="De:", anchor="e").grid(row=1, column=0, sticky="e", padx=(12, 8), pady=6)
        ent_de = ctk.CTkEntry(painel_int, **opcoes_campo_entrada_ctk(largura=160))
        ent_de.grid(row=1, column=1, sticky="w", pady=6)
        ctk.CTkLabel(painel_int, text="Até:", anchor="e").grid(row=2, column=0, sticky="e", padx=(12, 8), pady=6)
        ent_ate = ctk.CTkEntry(painel_int, **opcoes_campo_entrada_ctk(largura=160))
        ent_ate.grid(row=2, column=1, sticky="w", pady=6)
        # Sugestão: extremos das notas
        datas = sorted(r.data for r in ativos if r.data)
        if datas:
            ent_de.insert(0, datas[0])
            ent_ate.insert(0, datas[-1])
            ctk.CTkLabel(
                painel_int,
                text=f"Sugestão com base nas notas: {datas[0]} a {datas[-1]}",
                font=FONT_DICA,
                text_color=COR_TEXTO_SECUNDARIO,
                anchor="w",
            ).grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 12))

        # --- painel completo ---
        painel_comp = ctk.CTkFrame(painel, fg_color="transparent")
        n_sem = len(notas_sem_data(ativos))
        txt_comp = (
            f"Inclui todas as {len(ativos)} nota(s) ativas, com ou sem data.\n"
            "Ideal para o consolidado de reembolso (com adiantamento)."
        )
        if n_sem:
            txt_comp += f"\nAtenção: {n_sem} nota(s) sem data — confira antes de enviar."
        ctk.CTkLabel(
            painel_comp,
            text=txt_comp,
            justify="left",
            anchor="w",
            font=FONT_INTERFACE,
        ).pack(fill="x", padx=12, pady=16)

        def atualizar_paineis() -> None:
            for p in (painel_comp, painel_mes, painel_int):
                p.grid_forget()
            m = modo_var.get()
            if m == "completo":
                painel_comp.grid(row=0, column=0, sticky="nsew")
            elif m == "mes":
                painel_mes.grid(row=0, column=0, sticky="nsew")
            else:
                painel_int.grid(row=0, column=0, sticky="nsew")

        atualizar_paineis()

        rodape = ctk.CTkFrame(frame, fg_color="transparent")
        rodape.grid(row=4, column=0, sticky="ew", pady=(12, 0))

        def cancelar() -> None:
            nonlocal resultado
            resultado = None
            dlg.destroy()

        def confirmar() -> None:
            nonlocal resultado
            m = modo_var.get()
            if m == "completo":
                resultado = [EscopoRelatorio(modo="completo")]
            elif m == "mes":
                escolhidos = [am for am, var in mes_vars.items() if var.get()]
                if not escolhidos:
                    messagebox.showwarning(
                        "Mês",
                        "Selecione pelo menos um mês, ou use o relatório completo.",
                        parent=dlg,
                    )
                    return
                # Confirma resumo antes de gerar (reduz erro com muitos meses)
                linhas = []
                for am in escolhidos:
                    notes = selecionar_receipts(ativos, EscopoRelatorio(modo="mes", ano_mes=am))
                    tot = sum(float(r.valor) for r in notes if r.valor is not None)
                    linhas.append(f"• {rotulo_mes(am)}: {len(notes)} nota(s) · {format_brl(tot)}")
                if not messagebox.askyesno(
                    "Confirmar meses",
                    f"Serão gerados {len(escolhidos)} relatório(s):\n\n"
                    + "\n".join(linhas)
                    + "\n\nCada um em output/RDV_AAAA-MM-DD_a_AAAA-MM-DD.",
                    parent=dlg,
                ):
                    return
                resultado = [EscopoRelatorio(modo="mes", ano_mes=am) for am in escolhidos]
            else:
                di = ent_de.get().strip()
                df = ent_ate.get().strip()
                try:
                    esc = EscopoRelatorio(modo="intervalo", data_inicio=di, data_fim=df)
                    notes = selecionar_receipts(ativos, esc)
                except ValueError as e:
                    messagebox.showerror("Intervalo", str(e), parent=dlg)
                    return
                if not notes:
                    messagebox.showwarning(
                        "Intervalo",
                        "Nenhuma nota com data nesse intervalo.",
                        parent=dlg,
                    )
                    return
                # Confirmação com contagem
                tot = sum(float(r.valor) for r in notes if r.valor is not None)
                if not messagebox.askyesno(
                    "Confirmar intervalo",
                    f"{len(notes)} nota(s) · {format_brl(tot)}\n"
                    f"De {di} até {df}.\n\nGerar este relatório?",
                    parent=dlg,
                ):
                    return
                resultado = [esc]
            dlg.destroy()

        ctk.CTkButton(rodape, text="Cancelar", width=110, command=cancelar).pack(side="right", padx=4)
        ctk.CTkButton(
            rodape,
            text="Gerar",
            width=120,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=confirmar,
        ).pack(side="right", padx=4)

        dlg.protocol("WM_DELETE_WINDOW", cancelar)
        dlg.wait_window()
        return resultado

    def _on_extract_progress(self, done: int, total: int) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.progress.set(done / max(total, 1))
        self._set_status(f"Extraindo… {done}/{total}")

    def _on_extract_done(self, erros: list[str] | None = None) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._busy = False
        self._persist()
        self._refresh_list()
        self._refresh_totais()
        if self.selected_id:
            self._select_receipt(self.selected_id)
        n_dup = len(self._dup_ids)
        if erros:
            amostra = "\n".join(erros[:8])
            extra = f"\n… e mais {len(erros) - 8}" if len(erros) > 8 else ""
            messagebox.showwarning(
                "Extração com falhas",
                f"{len(erros)} nota(s) falharam:\n\n{amostra}{extra}",
            )
        if n_dup:
            self._set_status(f"Extração concluída. Atenção: {n_dup} nota(s) duplicada(s) detectada(s).")
        elif erros:
            self._set_status(f"Extração concluída com {len(erros)} falha(s).")
        else:
            self._set_status("Extração concluída. Confira os valores.")
        self.progress.set(1)


def run() -> None:
    inicializar_tema()
    app = App()
    app.mainloop()
