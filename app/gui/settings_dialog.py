from __future__ import annotations

import threading
from tkinter import messagebox

import customtkinter as ctk

from app.gui.tema import (
    COR_PRIMARIA,
    COR_PRIMARIA_HOVER,
    COR_TEXTO_SECUNDARIO,
    FONT_DICA,
    FONT_INTERFACE,
    FONT_TITULO,
    opcoes_campo_entrada_ctk,
    opcoes_combo_ctk,
)
from app.ocr.providers import (
    extrair_modelo_sugerido_erro,
    listar_modelos_gemini,
    test_provider_connection,
)
from app.settings import (
    DEFAULT_MODELS,
    MOTORS,
    PROVIDER_API_ENV,
    ROTULO_GEMINI_AUTOMATICO,
    escolher_modelo_estavel,
    get_api_key,
    id_modelo_combo,
    list_api_keys,
    load_user_settings,
    mask_api_key,
    modelos_para_combo,
    motor_label,
    normalizar_modelo,
    peek_api_keys_status,
    persist_motor,
    rotulo_modelo_combo,
    save_user_settings,
    settings_key_modelo,
)


class SettingsDialog(ctk.CTkToplevel):
    """Janela para escolher motor de OCR/IA e gravar chaves de API."""

    _TITULOS_CHAVE = {
        "gemini": "Google Gemini",
        "openai": "OpenAI",
        "anthropic": "Anthropic Claude",
    }

    def __init__(self, master, on_saved=None) -> None:
        super().__init__(master)
        self.title("Configurações de reconhecimento")
        self.geometry("640x760")
        self.minsize(560, 680)
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved
        self._fechando = False
        self._testando: set[str] = set()
        self._master_ref = master

        self.settings = load_user_settings()
        self._motor_ids = [m[0] for m in MOTORS]
        self._motor_labels = [m[1] for m in MOTORS]
        self._label_to_id = {lab: mid for mid, lab in MOTORS}

        self.key_entries: dict[str, ctk.CTkEntry] = {}
        self.gemini_key_entries: list[ctk.CTkEntry] = []
        self._gemini_keys_host: ctk.CTkFrame | None = None
        self.key_status: dict[str, ctk.CTkLabel] = {}
        self.btn_testar: dict[str, ctk.CTkButton] = {}
        self.cmb_modelos: dict[str, ctk.CTkComboBox] = {}
        self.lbl_modelo_info: dict[str, ctk.CTkLabel] = {}
        self.btn_atualizar_modelos: dict[str, ctk.CTkButton] = {}
        self._modelos_api: dict[str, list[str]] = {}
        self._atualizando_modelos: set[str] = set()

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(50, self._focus)
        # Carrega lista real da API se já houver chave Gemini
        self.after(300, lambda: self._atualizar_modelos_api("gemini", silencioso=True))

    def _janela_viva(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _parent_msg(self):
        """Parent seguro para messagebox (dialog pode ter sido fechado no meio do teste)."""
        if self._janela_viva():
            return self
        master = self._master_ref
        try:
            if master is not None and master.winfo_exists():
                return master
        except Exception:
            pass
        return None

    def _mostrar_msg(self, tipo: str, titulo: str, texto: str) -> bool | None:
        parent = self._parent_msg()
        kwargs = {"parent": parent} if parent is not None else {}
        if tipo == "info":
            messagebox.showinfo(titulo, texto, **kwargs)
            return None
        if tipo == "warning":
            messagebox.showwarning(titulo, texto, **kwargs)
            return None
        if tipo == "error":
            messagebox.showerror(titulo, texto, **kwargs)
            return None
        if tipo == "yesno":
            return bool(messagebox.askyesno(titulo, texto, **kwargs))
        return None

    def _focus(self) -> None:
        try:
            self.focus_force()
        except Exception:
            pass

    def _build(self) -> None:
        ctk.CTkLabel(
            self,
            text="Reconhecimento de imagem",
            font=FONT_TITULO,
        ).pack(anchor="w", padx=16, pady=(16, 4))

        ctk.CTkLabel(
            self,
            text="Escolha o motor, confira as chaves e o modelo. No Gemini, várias chaves = extração em paralelo. Use Testar para validar. Tudo é salvo ao fechar.",
            wraplength=580,
            justify="left",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        outer = ctk.CTkScrollableFrame(self)
        outer.pack(fill="both", expand=True, padx=16, pady=8)
        outer.grid_columnconfigure(0, weight=1)

        # --- Motor ---
        motor_frame = ctk.CTkFrame(outer)
        motor_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        motor_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(motor_frame, text="Motor", font=FONT_INTERFACE).grid(
            row=0, column=0, sticky="w", padx=10, pady=10
        )
        current_motor = (self.settings.get("motor") or "local").strip().lower()
        current_label = motor_label(current_motor)
        if current_label not in self._motor_labels:
            current_label = self._motor_labels[0]
        self._motor_pronto = False
        self.cmb_motor = ctk.CTkComboBox(
            motor_frame,
            values=self._motor_labels,
            **opcoes_combo_ctk(largura=360),
        )
        self.cmb_motor.grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        self._aplicar_motor_combo(current_label)
        self.after(50, lambda: self._aplicar_motor_combo(current_label))
        self.after(150, lambda lbl=current_label: self._concluir_init_motor(lbl))

        self.lbl_motor_hint = ctk.CTkLabel(
            motor_frame,
            text="",
            wraplength=520,
            justify="left",
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        self.lbl_motor_hint.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))

        # --- Provedores: chave + testar + modelo ---
        row = 1
        for provider, env_name in PROVIDER_API_ENV.items():
            bloco = self._build_bloco_provedor(outer, provider, env_name)
            bloco.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            row += 1

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(btns, text="Fechar", width=100, fg_color="gray", command=self._fechar).pack(
            side="right", padx=4
        )
        ctk.CTkButton(
            btns,
            text="Salvar",
            width=120,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=self._save,
        ).pack(side="right", padx=4)

        self._atualizar_dica_motor(current_motor)

    def _build_bloco_provedor(self, parent, provider: str, env_name: str) -> ctk.CTkFrame:
        if provider == "gemini":
            return self._build_bloco_gemini(parent)

        titulo = self._TITULOS_CHAVE.get(provider, provider)
        frame = ctk.CTkFrame(parent)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=titulo, font=FONT_INTERFACE).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 4)
        )

        ctk.CTkLabel(frame, text="Chave", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO).grid(
            row=1, column=0, sticky="w", padx=10, pady=4
        )
        ent = ctk.CTkEntry(
            frame, placeholder_text=env_name, **opcoes_campo_entrada_ctk()
        )
        ent.grid(row=1, column=1, sticky="ew", padx=(4, 6), pady=4)
        existing = get_api_key(provider)
        if existing:
            ent.insert(0, existing)
        ent.bind("<FocusOut>", lambda _e, p=provider: self._on_chave_focus_out(p))
        self.key_entries[provider] = ent

        btn = ctk.CTkButton(
            frame,
            text="Testar",
            width=80,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=lambda p=provider: self._testar_provedor(p),
        )
        btn.grid(row=1, column=2, sticky="e", padx=(0, 10), pady=4)
        self.btn_testar[provider] = btn

        st = ctk.CTkLabel(
            frame,
            text=self._texto_status_chave(existing),
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        st.grid(row=2, column=1, columnspan=2, sticky="ew", padx=4, pady=(0, 4))
        self.key_status[provider] = st

        ctk.CTkLabel(frame, text="Modelo", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO).grid(
            row=3, column=0, sticky="w", padx=10, pady=(4, 4)
        )
        self._montar_combo_modelo(frame, provider, row=3)
        ctk.CTkLabel(frame, text="", height=6).grid(row=4, column=0)

        return frame

    def _build_bloco_gemini(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="Google Gemini", font=FONT_INTERFACE).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 2)
        )
        ctk.CTkLabel(
            frame,
            text="Adicione quantas chaves quiser — o app usa em paralelo (uma requisição por chave) e faz rodízio se alguma atingir o limite.",
            wraplength=560,
            justify="left",
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

        host = ctk.CTkFrame(frame, fg_color="transparent")
        host.grid(row=2, column=0, sticky="ew", padx=6, pady=2)
        host.grid_columnconfigure(0, weight=1)
        self._gemini_keys_host = host
        self.gemini_key_entries = []

        salvas = list_api_keys("gemini")
        if not salvas:
            salvas = [""]
        for chave in salvas:
            self._gemini_add_row(chave, focar=False)

        acoes = ctk.CTkFrame(frame, fg_color="transparent")
        acoes.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 2))
        acoes.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            acoes,
            text="+ Adicionar chave",
            width=140,
            fg_color="gray",
            command=self._gemini_adicionar_chave,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        btn = ctk.CTkButton(
            acoes,
            text="Testar",
            width=80,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=lambda: self._testar_provedor("gemini"),
        )
        btn.grid(row=0, column=2, sticky="e")
        self.btn_testar["gemini"] = btn

        st = ctk.CTkLabel(
            frame,
            text=peek_api_keys_status("gemini"),
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        st.grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 4))
        self.key_status["gemini"] = st

        modelo_row = ctk.CTkFrame(frame, fg_color="transparent")
        modelo_row.grid(row=5, column=0, sticky="ew", padx=6, pady=(4, 2))
        modelo_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            modelo_row, text="Modelo", font=FONT_DICA, text_color=COR_TEXTO_SECUNDARIO
        ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self._montar_combo_modelo(modelo_row, "gemini", row=0, com_atualizar=True)

        info = ctk.CTkLabel(
            frame,
            text=(
                f"«{ROTULO_GEMINI_AUTOMATICO}» escolhe o Flash estável "
                f"(hoje: {escolher_modelo_estavel()}). Use «Atualizar lista» para ver o que suas chaves aceitam."
            ),
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            wraplength=560,
            justify="left",
        )
        info.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.lbl_modelo_info["gemini"] = info
        return frame

    def _montar_combo_modelo(
        self,
        parent,
        provider: str,
        *,
        row: int,
        com_atualizar: bool = False,
    ) -> None:
        chave_modelo = settings_key_modelo(provider)
        atual_id = normalizar_modelo(
            provider,
            (self.settings.get(chave_modelo) or DEFAULT_MODELS.get(provider) or "").strip(),
        )
        valores = modelos_para_combo(provider, atual_id)
        cmb = ctk.CTkComboBox(parent, values=valores, **opcoes_combo_ctk())
        cmb.grid(row=row, column=1, sticky="ew", padx=(4, 6), pady=4)
        rotulo_atual = rotulo_modelo_combo(provider, atual_id)
        if rotulo_atual in valores:
            cmb.set(rotulo_atual)
        elif valores:
            cmb.set(valores[0])
        self.cmb_modelos[provider] = cmb
        self.after(
            80,
            lambda c=cmb, v=rotulo_atual if rotulo_atual in valores else (valores[0] if valores else ""): self._set_combo(
                c, v
            ),
        )
        if com_atualizar:
            btn_lista = ctk.CTkButton(
                parent,
                text="Atualizar lista",
                width=110,
                fg_color="gray",
                command=lambda: self._atualizar_modelos_api("gemini", silencioso=False),
            )
            btn_lista.grid(row=row, column=2, sticky="e", padx=(0, 4), pady=4)
            self.btn_atualizar_modelos[provider] = btn_lista

    def _gemini_add_row(self, valor: str = "", *, focar: bool = False) -> None:
        host = self._gemini_keys_host
        if host is None:
            return
        row = ctk.CTkFrame(host, fg_color="transparent")
        row.pack(fill="x", pady=2)
        row.grid_columnconfigure(0, weight=1)

        idx = len(self.gemini_key_entries)
        ent = ctk.CTkEntry(
            row,
            placeholder_text=f"GEMINI_API_KEY #{idx + 1}",
            **opcoes_campo_entrada_ctk(),
        )
        ent.grid(row=0, column=0, sticky="ew", padx=(4, 6))
        if valor:
            ent.insert(0, valor)
        ent.bind("<FocusOut>", lambda _e: self._on_gemini_keys_changed())
        self.gemini_key_entries.append(ent)

        def remover(r=row, e=ent) -> None:
            self._gemini_remover_entry(e, r)

        ctk.CTkButton(
            row,
            text="−",
            width=36,
            fg_color="gray",
            command=remover,
        ).grid(row=0, column=1, padx=(0, 4))

        if focar:
            try:
                ent.focus_set()
            except Exception:
                pass

    def _gemini_adicionar_chave(self) -> None:
        self._gemini_add_row("", focar=True)
        self._atualizar_status_chave("gemini")

    def _gemini_remover_entry(self, ent: ctk.CTkEntry, row: ctk.CTkFrame) -> None:
        if ent in self.gemini_key_entries:
            self.gemini_key_entries.remove(ent)
        try:
            row.destroy()
        except Exception:
            pass
        if not self.gemini_key_entries:
            self._gemini_add_row("", focar=True)
        self._on_gemini_keys_changed()

    def _coletar_gemini_keys(self) -> list[str]:
        chaves: list[str] = []
        for ent in self.gemini_key_entries:
            try:
                v = ent.get().strip()
            except Exception:
                continue
            if v and v not in chaves:
                chaves.append(v)
        return chaves

    def _primeira_chave_gemini_ui(self) -> str:
        for ent in self.gemini_key_entries:
            try:
                v = ent.get().strip()
            except Exception:
                continue
            if v:
                return v
        return get_api_key("gemini")

    def _on_gemini_keys_changed(self) -> None:
        try:
            chaves = self._coletar_gemini_keys()
            save_user_settings(
                motor=self._selected_motor_id(),
                api_keys_lists={"gemini": chaves},
                modelos={settings_key_modelo("gemini"): self._modelo_selecionado("gemini")},
            )
            self._atualizar_status_chave("gemini")
            if chaves:
                self._atualizar_modelos_api("gemini", silencioso=True)
        except Exception as exc:
            messagebox.showerror(
                "Erro ao salvar chaves",
                f"Não foi possível gravar as chaves Gemini:\n{exc}",
                parent=self,
            )

    def _sincronizar_gemini_entries(self) -> None:
        """Recarrega campos a partir do .env (após save)."""
        salvas = list_api_keys("gemini")
        atuais = self._coletar_gemini_keys()
        if atuais == salvas:
            return
        host = self._gemini_keys_host
        if host is None:
            return
        for child in list(host.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self.gemini_key_entries = []
        for chave in salvas or [""]:
            self._gemini_add_row(chave, focar=False)

    @staticmethod
    def _set_combo(cmb: ctk.CTkComboBox, valor: str) -> None:
        if not valor:
            return
        try:
            cmb.set(valor)
        except Exception:
            pass

    @staticmethod
    def _texto_status_chave(chave: str) -> str:
        if not chave:
            return "Nenhuma chave salva"
        return f"Salva ({len(chave)} caracteres): {mask_api_key(chave)}"

    def _atualizar_status_chave(self, provider: str) -> None:
        lbl = self.key_status.get(provider)
        if lbl is None:
            return
        if provider == "gemini":
            n = len(self._coletar_gemini_keys()) or len(list_api_keys("gemini"))
            if n == 0:
                texto = "Nenhuma chave salva"
            elif n == 1:
                k = self._primeira_chave_gemini_ui()
                texto = f"1 chave em uso: {mask_api_key(k)}" if k else "Nenhuma chave salva"
            else:
                texto = f"{n} chaves em paralelo — até {n} notas ao mesmo tempo (com rodízio se uma falhar)"
            try:
                lbl.configure(text=texto)
            except Exception:
                pass
            return
        chave = get_api_key(provider)
        try:
            lbl.configure(text=self._texto_status_chave(chave))
        except Exception:
            pass

    def _modelo_selecionado(self, provider: str) -> str:
        cmb = self.cmb_modelos.get(provider)
        if cmb is None:
            return DEFAULT_MODELS.get(provider, "")
        return id_modelo_combo(provider, cmb.get() or "") or DEFAULT_MODELS.get(provider, "")

    def _on_chave_focus_out(self, provider: str) -> None:
        if provider == "gemini":
            self._on_gemini_keys_changed()
            return
        ent = self.key_entries.get(provider)
        if ent is None:
            return
        valor = ent.get().strip()
        if not valor:
            return
        try:
            save_user_settings(
                motor=self._selected_motor_id(),
                api_keys={provider: valor},
                modelos={settings_key_modelo(provider): self._modelo_selecionado(provider)},
            )
            self._atualizar_status_chave(provider)
            if ent.get().strip() != valor:
                ent.delete(0, "end")
                ent.insert(0, valor)
        except Exception as exc:
            messagebox.showerror(
                "Erro ao salvar chave",
                f"Não foi possível gravar a chave de {provider}:\n{exc}",
                parent=self,
            )
    def _atualizar_modelos_api(self, provider: str, *, silencioso: bool = False) -> None:
        if provider != "gemini" or provider in self._atualizando_modelos:
            return
        chave = self._primeira_chave_gemini_ui()
        if not chave:
            if not silencioso:
                messagebox.showwarning(
                    "Chave necessária",
                    "Informe ao menos uma chave Gemini para buscar os modelos na API.",
                    parent=self,
                )
            return

        self._atualizando_modelos.add(provider)
        btn = self.btn_atualizar_modelos.get(provider)
        info = self.lbl_modelo_info.get(provider)
        if btn is not None:
            btn.configure(state="disabled", text="…")
        if info is not None:
            info.configure(text="Consultando modelos disponíveis na API…")

        def trabalhador() -> None:
            lista, erro = listar_modelos_gemini(chave)

            def agendar() -> None:
                self._fim_atualizar_modelos(provider, lista, erro, silencioso)

            try:
                if self._janela_viva():
                    self.after(0, agendar)
                else:
                    master = self._master_ref
                    if master is not None and master.winfo_exists():
                        master.after(0, agendar)
                    else:
                        agendar()
            except Exception:
                try:
                    agendar()
                except Exception:
                    pass

        threading.Thread(target=trabalhador, daemon=True).start()

    def _fim_atualizar_modelos(
        self,
        provider: str,
        lista: list[str],
        erro: str | None,
        silencioso: bool,
    ) -> None:
        self._atualizando_modelos.discard(provider)
        viva = self._janela_viva()

        btn = self.btn_atualizar_modelos.get(provider)
        if viva and btn is not None:
            try:
                btn.configure(state="normal", text="Atualizar lista")
            except Exception:
                pass

        info = self.lbl_modelo_info.get(provider)
        cmb = self.cmb_modelos.get(provider)
        if erro or not lista:
            if viva and info is not None:
                try:
                    info.configure(
                        text=erro
                        or "Não foi possível obter a lista. Mantidos os modelos locais de fallback."
                    )
                except Exception:
                    pass
            if not silencioso:
                self._mostrar_msg(
                    "warning",
                    "Lista de modelos",
                    erro or "A API não retornou modelos.",
                )
            return

        self._modelos_api[provider] = lista
        preferencia = "automatico"
        rotulo = ROTULO_GEMINI_AUTOMATICO
        if viva:
            try:
                preferencia = normalizar_modelo(provider, self._modelo_selecionado(provider))
            except Exception:
                preferencia = "automatico"
            valores = modelos_para_combo(provider, preferencia, lista_api=lista)
            rotulo = rotulo_modelo_combo(provider, preferencia)
            if rotulo not in valores:
                rotulo = valores[0] if valores else ROTULO_GEMINI_AUTOMATICO
                preferencia = id_modelo_combo(provider, rotulo)
            if cmb is not None:
                try:
                    cmb.configure(values=valores)
                    cmb.set(rotulo)
                except Exception:
                    pass
            estavel = escolher_modelo_estavel(lista)
            if info is not None:
                try:
                    info.configure(
                        text=(
                            f"{len(lista)} modelo(s) na sua chave. "
                            f"«{ROTULO_GEMINI_AUTOMATICO}» → {estavel}."
                        )
                    )
                except Exception:
                    pass
        else:
            estavel = escolher_modelo_estavel(lista)
            preferencia = normalizar_modelo(provider, preferencia)

        try:
            # Salva preferência mesmo se a janela fechou (teste/atualização em andamento)
            motor = "local"
            if viva:
                try:
                    motor = self._selected_motor_id()
                except Exception:
                    motor = load_user_settings().get("motor") or "local"
            else:
                motor = load_user_settings().get("motor") or "local"
            save_user_settings(
                motor=motor,
                modelos={settings_key_modelo(provider): preferencia},
            )
        except Exception:
            pass
        if not silencioso:
            self._mostrar_msg(
                "info",
                "Lista de modelos",
                f"Encontrados {len(lista)} modelos úteis para leitura de notas.\n"
                f"Automático usaria: {estavel}\n"
                f"Selecionado agora: {rotulo}",
            )

    def _testar_provedor(self, provider: str) -> None:
        if provider in self._testando:
            return
        if provider == "gemini":
            chave = self._primeira_chave_gemini_ui()
            chaves_lista = self._coletar_gemini_keys()
        else:
            ent = self.key_entries.get(provider)
            chave = (ent.get().strip() if ent else "") or get_api_key(provider)
            chaves_lista = [chave] if chave else []
        modelo = normalizar_modelo(provider, self._modelo_selecionado(provider))
        if not chave:
            self._mostrar_msg(
                "warning",
                "Chave necessária",
                f"Informe a chave de {self._TITULOS_CHAVE.get(provider, provider)} antes de testar.",
            )
            return

        cmb = self.cmb_modelos.get(provider)
        if cmb is not None:
            try:
                rotulo = rotulo_modelo_combo(provider, modelo)
                valores = list(cmb.cget("values") or [])
                if rotulo not in valores:
                    valores = [rotulo] + valores
                    cmb.configure(values=valores)
                cmb.set(rotulo)
            except Exception:
                pass

        try:
            if provider == "gemini":
                save_user_settings(
                    motor=self._selected_motor_id(),
                    api_keys_lists={"gemini": chaves_lista},
                    modelos={settings_key_modelo(provider): modelo},
                )
            else:
                save_user_settings(
                    motor=self._selected_motor_id(),
                    api_keys={provider: chave},
                    modelos={settings_key_modelo(provider): modelo},
                )
            self._atualizar_status_chave(provider)
        except Exception as exc:
            self._mostrar_msg("error", "Erro ao salvar", str(exc))
            return

        btn = self.btn_testar.get(provider)
        self._testando.add(provider)
        if btn is not None:
            btn.configure(state="disabled", text="…")

        n_chaves = len(chaves_lista)

        def trabalhador() -> None:
            ok, msg = test_provider_connection(provider, chave, modelo)
            if ok and provider == "gemini" and n_chaves > 1:
                msg = f"Testada 1 de {n_chaves} chaves (rodízio ativo).\n" + msg
            try:
                if self._janela_viva():
                    self.after(0, lambda: self._fim_teste(provider, ok, msg))
                else:
                    master = self._master_ref
                    if master is not None and master.winfo_exists():
                        master.after(0, lambda: self._fim_teste(provider, ok, msg))
                    else:
                        self._fim_teste(provider, ok, msg)
            except Exception:
                try:
                    self._fim_teste(provider, ok, msg)
                except Exception:
                    pass

        threading.Thread(target=trabalhador, daemon=True).start()

    def _fim_teste(self, provider: str, ok: bool, msg: str) -> None:
        self._testando.discard(provider)
        viva = self._janela_viva()
        btn = self.btn_testar.get(provider)
        if viva and btn is not None:
            try:
                btn.configure(state="normal", text="Testar")
            except Exception:
                pass
        titulo = self._TITULOS_CHAVE.get(provider, provider)
        if ok:
            self._mostrar_msg("info", f"Teste — {titulo}", msg)
            return

        # Se a API sugeriu outro modelo, ofereceerece aplicar (só se a janela ainda existir)
        sugerido = extrair_modelo_sugerido_erro(msg) if provider == "gemini" else None
        if sugerido and viva:
            aplicar = self._mostrar_msg(
                "yesno",
                f"Teste — {titulo}",
                f"{msg}\n\nDeseja selecionar automaticamente «{sugerido}»?",
            )
            if aplicar:
                cmb = self.cmb_modelos.get(provider)
                if cmb is not None:
                    try:
                        rotulo = rotulo_modelo_combo(provider, sugerido)
                        valores = list(cmb.cget("values") or [])
                        if rotulo not in valores:
                            valores = [rotulo] + [v for v in valores if v != rotulo]
                            cmb.configure(values=valores)
                        cmb.set(rotulo)
                    except Exception:
                        pass
                try:
                    save_user_settings(
                        motor=self._selected_motor_id(),
                        modelos={settings_key_modelo(provider): sugerido},
                    )
                except Exception:
                    pass
            return
        self._mostrar_msg("error", f"Teste — {titulo}", msg)

    def _aplicar_motor_combo(self, label: str) -> None:
        try:
            if self.cmb_motor.get() != label:
                self.cmb_motor.set(label)
        except Exception:
            try:
                self.cmb_motor.set(label)
            except Exception:
                pass

    def _concluir_init_motor(self, label: str) -> None:
        self._aplicar_motor_combo(label)
        try:
            self.cmb_motor.configure(command=self._on_motor_change)
        except Exception:
            pass
        self._motor_pronto = True

    def _selected_motor_id(self) -> str:
        return self._label_to_id.get(self.cmb_motor.get(), "local")

    def _atualizar_dica_motor(self, motor: str) -> None:
        hints = {
            "local": "Usa RapidOCR no seu computador. Não precisa de chave nem internet.",
            "gemini": "Requer chave em https://aistudio.google.com/apikey — docs: https://ai.google.dev/gemini-api/docs/get-started",
            "openai": "Requer chave OpenAI (pago). https://platform.openai.com/api-keys",
            "anthropic": "Requer chave Anthropic. https://console.anthropic.com/",
            "auto": "Usa a primeira IA com chave configurada; se nenhuma, usa OCR local.",
        }
        self.lbl_motor_hint.configure(text=hints.get(motor, ""))

    def _on_motor_change(self, label: str | None = None) -> None:
        if not getattr(self, "_motor_pronto", False):
            return
        motor = self._label_to_id.get(label or self.cmb_motor.get(), "local")
        self._atualizar_dica_motor(motor)
        try:
            persist_motor(motor)
        except Exception:
            pass
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass

    def _coletar_api_keys(self) -> dict[str, str]:
        out = {pid: ent.get().strip() for pid, ent in self.key_entries.items()}
        # Gemini usa lista dedicada; mantém a 1ª só para checagem de motor
        g = self._coletar_gemini_keys()
        if g:
            out["gemini"] = g[0]
        return out

    def _coletar_api_keys_lists(self) -> dict[str, list[str]]:
        return {"gemini": self._coletar_gemini_keys()}

    def _coletar_modelos(self) -> dict[str, str]:
        return {
            settings_key_modelo(pid): self._modelo_selecionado(pid)
            for pid in self.cmb_modelos
        }

    def _persistir_tudo(self, *, exigir_chave_se_ia: bool) -> bool:
        motor = self._selected_motor_id()
        api_keys = self._coletar_api_keys()
        api_keys_lists = self._coletar_api_keys_lists()

        if exigir_chave_se_ia and motor in PROVIDER_API_ENV:
            if motor == "gemini":
                key_now = (api_keys_lists.get("gemini") or [None])[0] or get_api_key("gemini")
            else:
                key_now = api_keys.get(motor) or get_api_key(motor)
            if not key_now:
                messagebox.showwarning(
                    "Chave necessária",
                    f"O motor '{motor_label(motor)}' precisa de uma chave de API.\n"
                    "Informe a chave no campo correspondente ou escolha OCR local.",
                    parent=self,
                )
                return False

        try:
            save_user_settings(
                motor=motor,
                api_keys=api_keys,
                api_keys_lists=api_keys_lists,
                modelos=self._coletar_modelos(),
            )
        except Exception as exc:
            messagebox.showerror(
                "Erro ao salvar",
                f"Não foi possível gravar as configurações:\n{exc}",
                parent=self,
            )
            return False

        self._atualizar_status_chave("gemini")
        for provider in self.key_entries:
            self._atualizar_status_chave(provider)
            salva = get_api_key(provider)
            ent = self.key_entries[provider]
            if salva and ent.get().strip() != salva:
                ent.delete(0, "end")
                ent.insert(0, salva)
        return True

    def _save(self) -> None:
        if not self._persistir_tudo(exigir_chave_se_ia=True):
            return
        if self.on_saved:
            self.on_saved()
        self._fechando = True
        self.destroy()

    def _fechar(self) -> None:
        if self._fechando:
            self.destroy()
            return
        self._fechando = True
        ok = self._persistir_tudo(exigir_chave_se_ia=False)
        if ok and self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass
        self.destroy()
