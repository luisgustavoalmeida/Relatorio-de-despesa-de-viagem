from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from app.categorias_usuario import (
    adicionar_categoria,
    listar_custom,
    proxima_linha_livre,
    remover_categoria,
    resumo_vagas,
    rotulo_regiao,
)
from app.gui.tema import (
    COR_ERRO,
    COR_PRIMARIA,
    COR_PRIMARIA_HOVER,
    COR_TEXTO_SECUNDARIO,
    FONT_DICA,
    FONT_INTERFACE,
    FONT_TITULO,
    opcoes_campo_entrada_ctk,
    resolver_cor,
)
from app.projects import abrir_pasta_no_explorer
from app.reports.excel_export import PASTA_MODELO_RDV


class CategoriasDialog(ctk.CTkToplevel):
    """Gerencia categorias extras nas regiões Diversas e Outros do RDV."""

    def __init__(self, master, on_changed=None) -> None:
        super().__init__(master)
        self.title("Categorias extras")
        self.geometry("600x580")
        self.minsize(520, 460)
        self.transient(master)
        self.grab_set()
        self.on_changed = on_changed
        self.var_regiao = ctk.StringVar(value="diversas")
        self._build()
        self._atualizar_lista()
        self.after(50, self._focus)

    def _focus(self) -> None:
        try:
            self.focus_force()
            self.ent_nome.focus_set()
        except Exception:
            pass

    def _build(self) -> None:
        ctk.CTkLabel(self, text="Categorias extras", font=FONT_TITULO).pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        ctk.CTkLabel(
            self,
            text=(
                "Crie categorias além das padrão. Escolha se elas entram na região "
                "Diversas ou Outros do Excel. Elas aparecem no combo da nota e no relatório "
                "(nome + valor na linha reservada)."
            ),
            wraplength=560,
            justify="left",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        ).pack(anchor="w", padx=16, pady=(0, 10))

        regiao = ctk.CTkFrame(self, fg_color="transparent")
        regiao.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(regiao, text="Região no Excel:", font=FONT_INTERFACE).pack(
            side="left", padx=(0, 10)
        )
        self.seg_regiao = ctk.CTkSegmentedButton(
            regiao,
            values=["Diversas", "Outros"],
            command=self._on_troca_regiao,
        )
        self.seg_regiao.set("Diversas")
        self.seg_regiao.pack(side="left")

        criar = ctk.CTkFrame(self, fg_color="transparent")
        criar.pack(fill="x", padx=16, pady=(0, 8))
        criar.grid_columnconfigure(0, weight=1)

        self.ent_nome = ctk.CTkEntry(
            criar,
            placeholder_text="Nome da nova categoria (ex.: Internet, Material)",
            **opcoes_campo_entrada_ctk(),
        )
        self.ent_nome.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.ent_nome.bind("<Return>", lambda _e: self._adicionar())

        ctk.CTkButton(
            criar,
            text="Adicionar",
            width=110,
            command=self._adicionar,
            fg_color=COR_PRIMARIA,
            hover_color=COR_PRIMARIA_HOVER,
        ).grid(row=0, column=1)

        self.lbl_vagas = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            font=FONT_DICA,
            text_color=COR_TEXTO_SECUNDARIO,
        )
        self.lbl_vagas.pack(anchor="w", padx=16, pady=(0, 6))

        ctk.CTkLabel(self, text="Categorias criadas", font=FONT_INTERFACE).pack(
            anchor="w", padx=16, pady=(4, 4)
        )

        self.lista = ctk.CTkScrollableFrame(self, height=260)
        self.lista.pack(fill="both", expand=True, padx=16, pady=4)

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(
            rodape,
            text="Abrir pasta do modelo",
            width=160,
            command=lambda: abrir_pasta_no_explorer(PASTA_MODELO_RDV),
        ).pack(side="left")
        ctk.CTkButton(rodape, text="Fechar", width=100, command=self.destroy).pack(side="right")

    def _regiao_atual(self) -> str:
        texto = (self.seg_regiao.get() or "Diversas").strip().lower()
        return "outros" if texto.startswith("outro") else "diversas"

    def _on_troca_regiao(self, _valor: str | None = None) -> None:
        self.var_regiao.set(self._regiao_atual())
        self._atualizar_vagas()

    def _atualizar_vagas(self) -> None:
        info = resumo_vagas()
        partes = []
        for chave in ("diversas", "outros"):
            bloco = info[chave]
            if bloco["proxima"] is None:
                partes.append(f"{bloco['rotulo']}: {bloco['usadas']}/{bloco['total']} (cheia)")
            else:
                partes.append(
                    f"{bloco['rotulo']}: {bloco['usadas']}/{bloco['total']} "
                    f"(próxima linha {bloco['proxima']})"
                )
        atual = rotulo_regiao(self._regiao_atual())
        self.lbl_vagas.configure(text=f"Selecionada: {atual}  ·  " + "  |  ".join(partes))

    def _atualizar_lista(self) -> None:
        for w in self.lista.winfo_children():
            w.destroy()

        self._atualizar_vagas()
        itens = listar_custom()

        if not itens:
            ctk.CTkLabel(
                self.lista,
                text="Nenhuma categoria extra ainda.\nEscolha a região, digite o nome e clique em Adicionar.",
                font=FONT_DICA,
                text_color=COR_TEXTO_SECUNDARIO,
                justify="left",
            ).pack(anchor="w", padx=4, pady=12)
            return

        for item in itens:
            nome = str(item.get("nome") or "")
            linha = item.get("linha_excel")
            rotulo = item.get("celula_rotulo") or f"D{linha}"
            regiao = rotulo_regiao(item.get("regiao"))
            row = ctk.CTkFrame(self.lista, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row,
                text=f"{nome}",
                font=FONT_INTERFACE,
                anchor="w",
                width=140,
            ).pack(side="left", padx=(4, 8))
            ctk.CTkLabel(
                row,
                text=f"{regiao} → {rotulo} (valor G{linha})",
                font=FONT_DICA,
                text_color=COR_TEXTO_SECUNDARIO,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row,
                text="Remover",
                width=80,
                fg_color=resolver_cor(COR_ERRO),
                hover_color="#b3261e",
                command=lambda n=nome: self._remover(n),
            ).pack(side="right", padx=4)

    def _adicionar(self) -> None:
        nome = self.ent_nome.get().strip()
        regiao = self._regiao_atual()
        try:
            item = adicionar_categoria(nome, regiao=regiao)
        except ValueError as e:
            messagebox.showwarning("Categoria", str(e), parent=self)
            return
        except Exception as e:
            messagebox.showerror("Categoria", str(e), parent=self)
            return
        self.ent_nome.delete(0, "end")
        self._atualizar_lista()
        self._avisar_mudanca()
        messagebox.showinfo(
            "Categoria",
            f"«{item['nome']}» criada em {rotulo_regiao(item.get('regiao'))}.\n"
            f"Excel: rótulo em {item['celula_rotulo']}, valor em G{item['linha_excel']}.",
            parent=self,
        )

    def _remover(self, nome: str) -> None:
        if not messagebox.askyesno(
            "Remover categoria",
            f"Remover a categoria «{nome}»?\n\n"
            "Notas que já usam esse nome continuam com o texto; "
            "no Excel, voltarão a somar em Diversas/Outros até você reclassificá-las.",
            parent=self,
        ):
            return
        try:
            remover_categoria(nome)
        except Exception as e:
            messagebox.showerror("Categoria", str(e), parent=self)
            return
        self._atualizar_lista()
        self._avisar_mudanca()

    def _avisar_mudanca(self) -> None:
        if self.on_changed:
            try:
                self.on_changed()
            except Exception:
                pass
