"""Diálogo para inspecionar a última troca texto↔IA de uma nota."""

from __future__ import annotations

import json
from tkinter import messagebox

import customtkinter as ctk

from app.gui.tema import (
    COR_PRIMARIA,
    COR_PRIMARIA_HOVER,
    COR_TEXTO_SECUNDARIO,
    FONT_DICA,
    FONT_INTERFACE,
    FONT_TITULO,
)
from app.models.receipt import Receipt


def _formatar_json(texto: str) -> str:
    bruto = (texto or "").strip()
    if not bruto:
        return "(vazio)"
    try:
        return json.dumps(json.loads(bruto), ensure_ascii=False, indent=2)
    except Exception:
        return bruto


class IaConversaDialog(ctk.CTkToplevel):
    """Mostra o prompt enviado e o JSON bruto devolvido pela IA."""

    def __init__(self, master, receipt: Receipt) -> None:
        super().__init__(master)
        self.title("Conversa com a IA")
        self.geometry("780x640")
        self.minsize(640, 480)
        self.transient(master)
        self.grab_set()
        self.receipt = receipt
        self._build()
        self.after(50, self._focus)

    def _focus(self) -> None:
        try:
            self.focus_force()
        except Exception:
            pass

    def _build(self) -> None:
        r = self.receipt
        nome = (r.arquivo or "").replace("\\", "/").split("/")[-1] or r.id

        ctk.CTkLabel(self, text="Conversa com a IA", font=FONT_TITULO).pack(
            anchor="w", padx=16, pady=(16, 4)
        )

        meta_partes = []
        if r.ia_provedor:
            meta_partes.append(r.ia_provedor)
        if r.ia_modelo:
            meta_partes.append(r.ia_modelo)
        if getattr(r, "ia_em", None):
            meta_partes.append(r.ia_em)
        meta = " · ".join(meta_partes) if meta_partes else "sem metadados"

        ctk.CTkLabel(
            self,
            text=f"Nota: {nome}\n{meta}\nA imagem do comprovante foi enviada junto (não exibida aqui).",
            justify="left",
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        if not (r.ia_prompt or r.ia_resposta):
            ctk.CTkLabel(
                self,
                text=(
                    "Não há conversa salva para esta nota.\n"
                    "Reextraia com motor Gemini/OpenAI/Claude para gravar prompt e resposta."
                ),
                justify="left",
                font=FONT_INTERFACE,
            ).pack(anchor="w", padx=16, pady=20)
            ctk.CTkButton(self, text="Fechar", width=100, command=self.destroy).pack(
                pady=12
            )
            return

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(body, text="Você → IA (prompt)", font=FONT_INTERFACE, anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(0, 2)
        )
        self.txt_prompt = ctk.CTkTextbox(body, wrap="word", font=FONT_DICA)
        self.txt_prompt.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.txt_prompt.insert("1.0", r.ia_prompt or "(vazio)")
        self.txt_prompt.configure(state="disabled")

        ctk.CTkLabel(body, text="IA → Você (JSON)", font=FONT_INTERFACE, anchor="w").grid(
            row=2, column=0, sticky="ew", pady=(0, 2)
        )
        self.txt_resp = ctk.CTkTextbox(body, wrap="word", font=FONT_DICA)
        self.txt_resp.grid(row=3, column=0, sticky="nsew")
        self.txt_resp.insert("1.0", _formatar_json(r.ia_resposta))
        self.txt_resp.configure(state="disabled")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(
            btns,
            text="Copiar JSON",
            width=120,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
            command=self._copiar_json,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns,
            text="Copiar tudo",
            width=110,
            fg_color="gray",
            command=self._copiar_tudo,
        ).pack(side="left")
        ctk.CTkButton(btns, text="Fechar", width=100, fg_color="gray", command=self.destroy).pack(
            side="right"
        )

    def _copiar_json(self) -> None:
        texto = _formatar_json(self.receipt.ia_resposta)
        self.clipboard_clear()
        self.clipboard_append(texto)
        messagebox.showinfo("Copiado", "JSON da resposta copiado.", parent=self)

    def _copiar_tudo(self) -> None:
        r = self.receipt
        bloco = (
            f"=== PROMPT ({r.ia_provedor} / {r.ia_modelo}) ===\n"
            f"{r.ia_prompt}\n\n"
            f"=== RESPOSTA ===\n"
            f"{_formatar_json(r.ia_resposta)}\n"
        )
        self.clipboard_clear()
        self.clipboard_append(bloco)
        messagebox.showinfo("Copiado", "Prompt e resposta copiados.", parent=self)
