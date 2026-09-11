from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from app.atomic_io import atomic_write_text
from app.gui.tema import (
    COR_PRIMARIA,
    COR_PRIMARIA_HOVER,
    COR_TEXTO_SECUNDARIO,
    FONT_DICA,
    FONT_TITULO,
    opcoes_caixa_texto_ctk,
)
from app.projects import abrir_pasta_no_explorer
from app.reports.excel_export import (
    ARQUIVO_MAPEAMENTO,
    PASTA_MODELO_RDV,
    _template_path,
    reload_mapeamento_rdv,
)


def _abrir_arquivo(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class MapeamentoRdvDialog(ctk.CTkToplevel):
    """Mostra o JSON de mapeamento para edição e atalhos do modelo RDV."""

    def __init__(self, master, on_saved=None) -> None:
        super().__init__(master)
        self.title("Modelo RDV — mapeamento de células")
        self.geometry("720x640")
        self.minsize(560, 480)
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved

        self._build()
        self._carregar_json()
        self.after(50, self._focus)

    def _focus(self) -> None:
        try:
            self.focus_force()
        except Exception:
            pass

    def _build(self) -> None:
        ctk.CTkLabel(self, text="Mapeamento de células do Excel (RDV)", font=FONT_TITULO).pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        ctk.CTkLabel(
            self,
            text=(
                "Edite o JSON abaixo (endereços das células e linhas das categorias) e salve. "
                "A planilha modelo fica em assets/rdv — use os botões para abrir a pasta ou o Excel."
            ),
            wraplength=660,
            justify="left",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        atalhos = ctk.CTkFrame(self, fg_color="transparent")
        atalhos.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkButton(
            atalhos,
            text="Mostrar pasta do template",
            width=190,
            command=self._mostrar_pasta_template,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            atalhos,
            text="Abrir planilha Excel",
            width=150,
            command=self._abrir_excel,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            atalhos,
            text="Abrir JSON no Bloco de Notas",
            width=190,
            command=self._abrir_json_externo,
        ).pack(side="left")

        mapeamento = reload_mapeamento_rdv()
        template = _template_path(mapeamento)
        ctk.CTkLabel(
            self,
            text=f"Pasta do template: {PASTA_MODELO_RDV}\nPlanilha: {template.name}",
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            wraplength=660,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(4, 2))
        ctk.CTkLabel(
            self,
            text=f"JSON: {ARQUIVO_MAPEAMENTO}",
            anchor="w",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
            wraplength=660,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 6))

        self.txt_json = ctk.CTkTextbox(self, **opcoes_caixa_texto_ctk(altura_px=400))
        self.txt_json.pack(fill="both", expand=True, padx=16, pady=4)

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(rodape, text="Recarregar", width=100, command=self._carregar_json).pack(
            side="left"
        )
        ctk.CTkButton(rodape, text="Fechar", width=100, command=self.destroy).pack(
            side="right", padx=(6, 0)
        )
        ctk.CTkButton(
            rodape,
            text="Salvar",
            width=100,
            command=self._salvar,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        ).pack(side="right")

    def _carregar_json(self) -> None:
        try:
            if ARQUIVO_MAPEAMENTO.is_file():
                texto = ARQUIVO_MAPEAMENTO.read_text(encoding="utf-8")
            else:
                texto = "{\n}\n"
        except OSError as e:
            messagebox.showerror("JSON", f"Não foi possível ler o arquivo:\n{e}", parent=self)
            return
        self.txt_json.delete("1.0", "end")
        self.txt_json.insert("1.0", texto)

    def _mostrar_pasta_template(self) -> None:
        """Abre o Explorer na pasta do modelo, com a planilha selecionada se existir."""
        PASTA_MODELO_RDV.mkdir(parents=True, exist_ok=True)
        mapeamento = reload_mapeamento_rdv()
        template = _template_path(mapeamento)
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

    def _abrir_excel(self) -> None:
        try:
            mapeamento = reload_mapeamento_rdv()
            path = _template_path(mapeamento)
            if not path.exists():
                messagebox.showwarning(
                    "Planilha",
                    f"Modelo não encontrado:\n{path}\n\nColoque o arquivo RDV-PADRÃO.xlsx em assets/rdv.",
                    parent=self,
                )
                return
            _abrir_arquivo(path)
        except Exception as e:
            messagebox.showerror("Planilha", str(e), parent=self)

    def _abrir_json_externo(self) -> None:
        try:
            if not ARQUIVO_MAPEAMENTO.exists():
                messagebox.showwarning(
                    "JSON",
                    f"Arquivo não encontrado:\n{ARQUIVO_MAPEAMENTO}",
                    parent=self,
                )
                return
            _abrir_arquivo(ARQUIVO_MAPEAMENTO)
        except Exception as e:
            messagebox.showerror("JSON", str(e), parent=self)

    def _salvar(self) -> None:
        bruto = self.txt_json.get("1.0", "end").strip()
        if not bruto:
            messagebox.showerror("Validação", "O JSON está vazio.", parent=self)
            return
        try:
            data = json.loads(bruto)
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "JSON inválido",
                f"Corrija o JSON antes de salvar.\n\n{e}",
                parent=self,
            )
            return
        if not isinstance(data, dict):
            messagebox.showerror("JSON inválido", "O conteúdo precisa ser um objeto { … }.", parent=self)
            return
        try:
            PASTA_MODELO_RDV.mkdir(parents=True, exist_ok=True)
            texto = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(ARQUIVO_MAPEAMENTO, texto)
        except OSError as e:
            messagebox.showerror("Salvar", f"Não foi possível gravar o JSON:\n{e}", parent=self)
            return

        reload_mapeamento_rdv()
        self.txt_json.delete("1.0", "end")
        self.txt_json.insert("1.0", texto)
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass
        messagebox.showinfo(
            "Salvo",
            "JSON atualizado.\nA próxima geração de relatório usará este mapeamento.",
            parent=self,
        )
