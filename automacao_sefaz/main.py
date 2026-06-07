"""
Orquestrador principal - Automacao Retirada de Voos SEFAZ.
Interface grafica + controle do fluxo completo.
"""

import os
import sys
import time
import logging
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from typing import List, Optional

# Adiciona o diretorio atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, PASTA_CONFIG
from models.termo import (
    ConsultaMDFe,
    CTeProcesado,
    TipoEntrega,
    TermoApreensao,
)
from modules.browser import NexlogBrowser
from modules.parser import parsear_relatorio_sefaz
from modules.sefaz import SefazConsulta
from modules.nexlog_cte import NexlogCTeOperacoes
from modules.nexlog_liberar import NexlogLiberar

# ========= LOGGING =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(PASTA_CONFIG / "execucao.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


class AppAutomacao:
    """Interface principal da automacao."""

    def __init__(self):
        self.janela = tk.Tk()
        self.janela.title("Automacao SEFAZ - Retirada de Voos")
        self.janela.geometry("800x700")
        self.janela.configure(bg="#1a1a2e")
        self.janela.resizable(True, True)

        # Variaveis
        self.nexlog_user = tk.StringVar()
        self.nexlog_senha = tk.StringVar()
        self.nexlog_base = tk.StringVar(value="MCZ")
        self.sefaz_user = tk.StringVar()
        self.sefaz_senha = tk.StringVar()
        self.chave_mdfe = tk.StringVar()

        # Carrega credenciais salvas
        self._carregar_credenciais()

        # Monta interface
        self._criar_interface()

    def _carregar_credenciais(self):
        """Carrega credenciais salvas."""
        if config.carregar():
            self.nexlog_user.set(config.nexlog.usuario)
            self.nexlog_senha.set(config.nexlog.senha)
            self.nexlog_base.set(config.nexlog.base)
            self.sefaz_user.set(config.sefaz.usuario)
            self.sefaz_senha.set(config.sefaz.senha)

    def _salvar_credenciais(self):
        """Salva credenciais no arquivo local."""
        config.nexlog.usuario = self.nexlog_user.get()
        config.nexlog.senha = self.nexlog_senha.get()
        config.nexlog.base = self.nexlog_base.get()
        config.sefaz.usuario = self.sefaz_user.get()
        config.sefaz.senha = self.sefaz_senha.get()
        config.salvar()

    def _criar_interface(self):
        """Cria toda a interface grafica."""
        # ===== TITULO =====
        titulo = tk.Label(
            self.janela,
            text="RETIRADA DE VOOS - SEFAZ",
            bg="#1a1a2e",
            fg="#00ffe1",
            font=("Segoe UI", 18, "bold"),
        )
        titulo.pack(pady=10)

        # ===== NOTEBOOK (ABAS) =====
        style = ttk.Style()
        style.configure("TNotebook", background="#1a1a2e")
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"))

        notebook = ttk.Notebook(self.janela)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # --- ABA 1: CREDENCIAIS ---
        aba_cred = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_cred, text="  Credenciais  ")
        self._criar_aba_credenciais(aba_cred)

        # --- ABA 2: CONSULTA SEFAZ ---
        aba_sefaz = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_sefaz, text="  Consulta SEFAZ  ")
        self._criar_aba_sefaz(aba_sefaz)

        # --- ABA 3: EXECUTAR ---
        aba_exec = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_exec, text="  Executar  ")
        self._criar_aba_executar(aba_exec)

        # --- ABA 4: LOG ---
        aba_log = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_log, text="  Log  ")
        self._criar_aba_log(aba_log)

    def _criar_aba_credenciais(self, parent):
        """Aba de configuracao de credenciais."""
        frame = tk.Frame(parent, bg="#16213e")
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        # Nexlog
        tk.Label(frame, text="NEXLOG", bg="#16213e", fg="#00ffe1",
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, pady=10)

        tk.Label(frame, text="Usuario:", bg="#16213e", fg="white").grid(row=1, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.nexlog_user, width=30).grid(row=1, column=1, pady=3)

        tk.Label(frame, text="Senha:", bg="#16213e", fg="white").grid(row=2, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.nexlog_senha, show="*", width=30).grid(row=2, column=1, pady=3)

        tk.Label(frame, text="Base:", bg="#16213e", fg="white").grid(row=3, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.nexlog_base, width=30).grid(row=3, column=1, pady=3)

        # SEFAZ
        tk.Label(frame, text="SEFAZ-AL", bg="#16213e", fg="#00ffe1",
                 font=("Segoe UI", 12, "bold")).grid(row=5, column=0, columnspan=2, pady=(20, 10))

        tk.Label(frame, text="Usuario:", bg="#16213e", fg="white").grid(row=6, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.sefaz_user, width=30).grid(row=6, column=1, pady=3)

        tk.Label(frame, text="Senha:", bg="#16213e", fg="white").grid(row=7, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.sefaz_senha, show="*", width=30).grid(row=7, column=1, pady=3)

        # Botao salvar
        tk.Button(
            frame, text="SALVAR CREDENCIAIS", command=self._salvar_credenciais,
            bg="#0f3460", fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2"
        ).grid(row=9, column=0, columnspan=2, pady=20)

    def _criar_aba_sefaz(self, parent):
        """Aba de consulta manual na SEFAZ (colar relatorio ou inserir chave)."""
        frame = tk.Frame(parent, bg="#16213e")
        frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Opcao 1: Chave MDF-e
        tk.Label(frame, text="Chave do MDF-e (44 digitos):", bg="#16213e", fg="white",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 2))
        tk.Entry(frame, textvariable=self.chave_mdfe, width=60,
                 font=("Consolas", 10)).pack(fill="x")

        tk.Button(
            frame, text="CONSULTAR NO SITE SEFAZ", command=self._consultar_sefaz_site,
            bg="#0f3460", fg="white", font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2"
        ).pack(pady=10)

        # Separador
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=10)

        # Opcao 2: Colar relatorio manualmente
        tk.Label(frame, text="OU cole o relatorio da SEFAZ abaixo:", bg="#16213e",
                 fg="white", font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 2))

        self.campo_relatorio = scrolledtext.ScrolledText(
            frame, height=12, font=("Consolas", 9), wrap="word"
        )
        self.campo_relatorio.pack(fill="both", expand=True)

        tk.Button(
            frame, text="PROCESSAR RELATORIO COLADO", command=self._processar_relatorio_colado,
            bg="#e94560", fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2"
        ).pack(pady=10)

    def _criar_aba_executar(self, parent):
        """Aba de execucao do fluxo completo."""
        frame = tk.Frame(parent, bg="#16213e")
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(
            frame, text="Apos processar o relatorio da SEFAZ,\n"
                        "clique abaixo para executar o fluxo completo:",
            bg="#16213e", fg="white", font=("Segoe UI", 11), justify="center"
        ).pack(pady=20)

        # Resumo (sera preenchido apos parse)
        self.label_resumo = tk.Label(
            frame, text="Nenhum relatorio processado ainda.",
            bg="#16213e", fg="#aaa", font=("Segoe UI", 10), justify="left"
        )
        self.label_resumo.pack(pady=10)

        # Botoes de acao
        frame_btns = tk.Frame(frame, bg="#16213e")
        frame_btns.pack(pady=20)

        tk.Button(
            frame_btns, text="1. ADICIONAR COMENTARIOS\n(CTes retidos)",
            command=self._executar_comentarios,
            bg="#e94560", fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", width=25, height=3
        ).grid(row=0, column=0, padx=10)

        tk.Button(
            frame_btns, text="2. LIBERAR CTes\n(sem termo + RETIRA)",
            command=self._executar_liberacao,
            bg="#0f3460", fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", width=25, height=3
        ).grid(row=0, column=1, padx=10)

        tk.Button(
            frame_btns, text="EXECUTAR TUDO\n(Comentarios + Liberacao)",
            command=self._executar_fluxo_completo,
            bg="#00ffe1", fg="#1a1a2e", font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", width=30, height=3
        ).grid(row=1, column=0, columnspan=2, pady=20)

    def _criar_aba_log(self, parent):
        """Aba de log de execucao."""
        self.log_widget = scrolledtext.ScrolledText(
            parent, height=20, font=("Consolas", 9), bg="#0d1117", fg="#c9d1d9",
            insertbackground="white"
        )
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)

    def _log(self, mensagem: str):
        """Adiciona mensagem ao log visual."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.insert(tk.END, f"[{timestamp}] {mensagem}\n")
        self.log_widget.see(tk.END)
        self.janela.update_idletasks()
        logger.info(mensagem)

    # ========= ACOES =========

    def _consultar_sefaz_site(self):
        """Consulta a chave no site da SEFAZ via navegador."""
        chave = self.chave_mdfe.get().strip().replace(" ", "")
        if not chave or len(chave) != 44:
            messagebox.showwarning("Aviso", "Insira uma chave valida (44 digitos).")
            return

        self._salvar_credenciais()
        self._log(f"Iniciando consulta SEFAZ para chave: {chave[:20]}...")

        try:
            sefaz = SefazConsulta()
            sefaz.iniciar()
            sefaz.login()
            sefaz.navegar_consulta_analise_mdfe()
            resultado = sefaz.consultar_chave_mdfe(chave)

            if resultado:
                self._consulta_atual = resultado
                self._atualizar_resumo(resultado)
                self._log(f"Consulta OK: {resultado.total_termos} termos encontrados")
                messagebox.showinfo(
                    "Sucesso",
                    f"Consulta realizada!\n"
                    f"MDF-e: {resultado.numero_mdfe}\n"
                    f"Termos: {resultado.total_termos}\n"
                    f"CTes retidos: {len(resultado.ctes_retidos)}"
                )
            else:
                self._log("Consulta SEFAZ: nenhum resultado")
                messagebox.showwarning("Aviso", "Nenhum resultado encontrado.")

            sefaz.fechar()

        except Exception as e:
            self._log(f"ERRO na consulta SEFAZ: {e}")
            messagebox.showerror("Erro", str(e))

    def _processar_relatorio_colado(self):
        """Processa o relatorio colado manualmente pelo usuario."""
        texto = self.campo_relatorio.get("1.0", tk.END).strip()
        if not texto:
            messagebox.showwarning("Aviso", "Cole o relatorio da SEFAZ no campo.")
            return

        self._log("Processando relatorio colado...")

        try:
            resultado = parsear_relatorio_sefaz(texto)
            self._consulta_atual = resultado
            self._atualizar_resumo(resultado)

            self._log(
                f"Relatorio parseado: MDF-e {resultado.numero_mdfe} | "
                f"{resultado.total_termos} termos | "
                f"CTes: {', '.join(resultado.ctes_retidos)}"
            )

            messagebox.showinfo(
                "Relatorio Processado",
                f"MDF-e: {resultado.numero_mdfe}\n"
                f"Total de termos: {resultado.total_termos}\n"
                f"CTes com termo: {len(resultado.ctes_retidos)}\n\n"
                f"Agora va na aba 'Executar' para processar no Nexlog."
            )

        except Exception as e:
            self._log(f"ERRO ao parsear relatorio: {e}")
            messagebox.showerror("Erro", f"Erro ao processar relatorio:\n{e}")

    def _atualizar_resumo(self, consulta: ConsultaMDFe):
        """Atualiza o resumo na aba de execucao."""
        ctes = ", ".join(consulta.ctes_retidos) or "Nenhum"
        texto = (
            f"MDF-e: {consulta.numero_mdfe}\n"
            f"Status: {consulta.status.value}\n"
            f"Total termos: {consulta.total_termos}\n"
            f"CTes retidos: {ctes}\n"
        )
        self.label_resumo.config(text=texto, fg="#00ffe1")

    def _executar_comentarios(self):
        """Executa apenas a adicao de comentarios no Nexlog."""
        if not hasattr(self, '_consulta_atual'):
            messagebox.showwarning("Aviso", "Processe um relatorio primeiro.")
            return

        self._salvar_credenciais()
        consulta = self._consulta_atual

        if not consulta.termos:
            messagebox.showinfo("Info", "Nenhum termo para processar.")
            return

        self._log("Iniciando adicao de comentarios no Nexlog...")

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            operacoes = NexlogCTeOperacoes(browser)
            processados = operacoes.processar_termos(consulta.termos)

            # Salva para uso na liberacao
            self._ctes_processados = processados

            # Resumo
            ok = sum(1 for p in processados if p.comentario_adicionado)
            self._log(f"Comentarios: {ok}/{len(processados)} adicionados com sucesso")

            browser.fechar()

            messagebox.showinfo(
                "Concluido",
                f"Comentarios adicionados: {ok}/{len(processados)}"
            )

        except Exception as e:
            self._log(f"ERRO nos comentarios: {e}")
            messagebox.showerror("Erro", str(e))

    def _executar_liberacao(self):
        """Executa apenas a liberacao dos CTes sem termo (RETIRA)."""
        if not hasattr(self, '_ctes_processados'):
            messagebox.showwarning(
                "Aviso",
                "Execute os comentarios primeiro para identificar os CTes."
            )
            return

        self._log("Iniciando liberacao de CTes...")

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            liberador = NexlogLiberar(browser)
            resultado = liberador.liberar_apenas_retira(self._ctes_processados)

            self._log(
                f"Liberacao: {len(resultado['liberados'])} liberados | "
                f"{len(resultado['retidos'])} retidos | "
                f"{len(resultado['erros_liberacao'])} erros"
            )

            browser.fechar()

            messagebox.showinfo(
                "Liberacao Concluida",
                f"Liberados: {len(resultado['liberados'])}\n"
                f"Retidos (com motivo): {len(resultado['retidos'])}\n"
                f"Erros: {len(resultado['erros_liberacao'])}"
            )

        except Exception as e:
            self._log(f"ERRO na liberacao: {e}")
            messagebox.showerror("Erro", str(e))

    def _executar_fluxo_completo(self):
        """Executa o fluxo completo: comentarios + liberacao."""
        if not hasattr(self, '_consulta_atual'):
            messagebox.showwarning("Aviso", "Processe um relatorio primeiro.")
            return

        confirma = messagebox.askyesno(
            "Confirmar",
            "Isso vai:\n"
            "1. Adicionar comentarios criticos nos CTes retidos\n"
            "2. Liberar os CTes sem termo (apenas RETIRA)\n\n"
            "Deseja continuar?"
        )
        if not confirma:
            return

        self._salvar_credenciais()
        consulta = self._consulta_atual
        self._log("=" * 50)
        self._log("FLUXO COMPLETO INICIADO")
        self._log("=" * 50)

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            # ETAPA 1: Comentarios
            self._log("--- ETAPA 1: Adicionando comentarios ---")
            operacoes = NexlogCTeOperacoes(browser)
            processados = operacoes.processar_termos(consulta.termos)

            ok = sum(1 for p in processados if p.comentario_adicionado)
            self._log(f"Comentarios: {ok}/{len(processados)} OK")

            # ETAPA 2: Identificar CTes para liberar
            # CTes que estavam no manifesto MAS nao tiveram termo
            # Precisamos consultar todos os CTes do manifesto (nao so os com termo)
            # Por enquanto, libera os que ja foram processados e podem ser liberados
            self._log("--- ETAPA 2: Liberacao ---")
            liberador = NexlogLiberar(browser)
            resultado = liberador.liberar_apenas_retira(processados)

            self._log(
                f"Liberados: {len(resultado['liberados'])} | "
                f"Retidos: {len(resultado['retidos'])} | "
                f"Erros: {len(resultado['erros_liberacao'])}"
            )

            self._log("=" * 50)
            self._log("FLUXO COMPLETO FINALIZADO")
            self._log("=" * 50)

            browser.fechar()

            messagebox.showinfo(
                "Fluxo Completo",
                f"RESULTADO:\n\n"
                f"Comentarios: {ok}/{len(processados)}\n"
                f"Liberados: {len(resultado['liberados'])}\n"
                f"Retidos: {len(resultado['retidos'])}\n"
                f"Erros: {len(resultado['erros_liberacao'])}"
            )

        except Exception as e:
            self._log(f"ERRO CRITICO: {e}")
            messagebox.showerror("Erro", str(e))

    def executar(self):
        """Inicia a interface."""
        self._consulta_atual = None
        self._ctes_processados = []
        self.janela.mainloop()


# ========= ENTRY POINT =========
if __name__ == "__main__":
    app = AppAutomacao()
    app.executar()
