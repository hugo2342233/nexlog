"""
Automacao SEFAZ - Retirada de Voos
Orquestrador principal + Interface grafica.

Fluxo completo:
1. Buscar voos por data no Nexlog
2. Para cada voo:
   a) Extrair chave MDF-e (Integracao MDFe)
   b) Baixar manifesto (PDF) -> extrair AWBs RETIRA/ENTREGA
   c) Verificar Outlook se SEFAZ respondeu
   d) Se tem termos -> consultar site SEFAZ ou usar PDF do email
   e) Para cada CTe com termo -> buscar AWB -> adicionar comentario critico
   f) Liberar AWBs RETIRA sem termo na tela de Retencao
3. Proximo voo...
"""

import os
import sys
import time
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime, timedelta
from typing import List, Optional

# Adiciona o diretorio ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, PASTA_CONFIG
from models.termo import (
    Voo, DadosVoo, ConsultaMDFe, ManifestoVoo,
    RespostaEmail, ResultadoProcessamento, StatusMDFe,
)
from modules.browser import NexlogBrowser
from modules.parser import parsear_relatorio_pdf, parsear_manifesto_pdf
from modules.nexlog_voos import NexlogVoos
from modules.nexlog_cte import NexlogCTeOperacoes
from modules.nexlog_liberar import NexlogLiberar
from modules.outlook import OutlookWeb
from modules.sefaz import SefazConsulta

# ========= LOGGING =========
LOG_FILE = PASTA_CONFIG / "execucao.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


class AppAutomacao:
    """Interface principal da automacao."""

    # Cores do tema dark minimalista
    BG = "#0f0f0f"
    BG_CARD = "#1a1a1a"
    BG_INPUT = "#252525"
    FG = "#e0e0e0"
    FG_DIM = "#707070"
    ACCENT = "#4fc3f7"
    ACCENT_HOVER = "#81d4fa"
    DANGER = "#ef5350"
    SUCCESS = "#66bb6a"
    BORDER = "#333333"

    def __init__(self):
        self.janela = tk.Tk()
        self.janela.title("Retirada de Voos")
        self.janela.geometry("900x720")
        self.janela.configure(bg=self.BG)
        self.janela.resizable(True, True)

        # Variaveis
        self.nexlog_user = tk.StringVar()
        self.nexlog_senha = tk.StringVar()
        self.nexlog_base = tk.StringVar(value="MCZ")
        self.sefaz_user = tk.StringVar()
        self.sefaz_senha = tk.StringVar()
        self.data_inicial = tk.StringVar()
        self.data_final = tk.StringVar()
        self.timeout_var = tk.IntVar(value=20)

        # Estado
        self._voos_encontrados: List[Voo] = []
        self._voos_checkboxes: List[tk.BooleanVar] = []
        self._processando = False

        # Datas padrao (ontem)
        ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
        self.data_inicial.set(ontem)
        self.data_final.set(ontem)

        # Carrega credenciais
        self._carregar_credenciais()
        self._criar_interface()

    def _carregar_credenciais(self):
        if config.carregar():
            self.nexlog_user.set(config.nexlog.usuario)
            self.nexlog_senha.set(config.nexlog.senha)
            self.nexlog_base.set(config.nexlog.base)
            self.sefaz_user.set(config.sefaz.usuario)
            self.sefaz_senha.set(config.sefaz.senha)
            self.timeout_var.set(config.timeout_padrao)

    def _salvar_credenciais(self):
        config.nexlog.usuario = self.nexlog_user.get()
        config.nexlog.senha = self.nexlog_senha.get()
        config.nexlog.base = self.nexlog_base.get()
        config.sefaz.usuario = self.sefaz_user.get()
        config.sefaz.senha = self.sefaz_senha.get()
        config.timeout_padrao = self.timeout_var.get()
        config.salvar()

    def _criar_interface(self):
        """Interface minimalista dark."""
        # Configura estilo ttk
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.BG_CARD, foreground=self.FG,
                       padding=[14, 6], font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", self.BG_INPUT)],
                  foreground=[("selected", self.ACCENT)])

        # Header
        header = tk.Frame(self.janela, bg=self.BG, height=50)
        header.pack(fill="x", padx=20, pady=(15, 5))
        tk.Label(header, text="RETIRADA DE VOOS", bg=self.BG, fg=self.FG,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(header, text="SEFAZ-AL", bg=self.BG, fg=self.FG_DIM,
                 font=("Segoe UI", 10)).pack(side="left", padx=10, pady=4)

        # Separador
        tk.Frame(self.janela, bg=self.BORDER, height=1).pack(fill="x", padx=20)

        # Notebook (abas)
        notebook = ttk.Notebook(self.janela)
        notebook.pack(fill="both", expand=True, padx=20, pady=10)

        # --- ABA VOOS ---
        aba_voos = tk.Frame(notebook, bg=self.BG)
        notebook.add(aba_voos, text="  Voos  ")
        self._criar_aba_voos(aba_voos)

        # --- ABA CREDENCIAIS ---
        aba_cred = tk.Frame(notebook, bg=self.BG)
        notebook.add(aba_cred, text="  Config  ")
        self._criar_aba_credenciais(aba_cred)

        # --- ABA LOG ---
        aba_log = tk.Frame(notebook, bg=self.BG)
        notebook.add(aba_log, text="  Log  ")
        self._criar_aba_log(aba_log)

    def _criar_aba_voos(self, parent):
        """Aba principal com busca de voos e checkboxes."""
        # --- Barra de busca ---
        frame_busca = tk.Frame(parent, bg=self.BG_CARD, highlightthickness=1,
                               highlightbackground=self.BORDER)
        frame_busca.pack(fill="x", padx=10, pady=(10, 5))

        inner = tk.Frame(frame_busca, bg=self.BG_CARD)
        inner.pack(fill="x", padx=15, pady=12)

        tk.Label(inner, text="Periodo:", bg=self.BG_CARD, fg=self.FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        e1 = tk.Entry(inner, textvariable=self.data_inicial, width=11,
                      bg=self.BG_INPUT, fg=self.FG, insertbackground=self.FG,
                      relief="flat", font=("Segoe UI", 10))
        e1.pack(side="left", padx=(8, 4))
        tk.Label(inner, text="a", bg=self.BG_CARD, fg=self.FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        e2 = tk.Entry(inner, textvariable=self.data_final, width=11,
                      bg=self.BG_INPUT, fg=self.FG, insertbackground=self.FG,
                      relief="flat", font=("Segoe UI", 10))
        e2.pack(side="left", padx=(4, 15))

        btn_buscar = tk.Button(inner, text="BUSCAR", command=self._buscar_voos_thread,
                               bg=self.ACCENT, fg="#000", font=("Segoe UI", 9, "bold"),
                               relief="flat", cursor="hand2", padx=16, pady=2)
        btn_buscar.pack(side="left")

        # --- Lista de voos com checkboxes ---
        frame_lista = tk.Frame(parent, bg=self.BG)
        frame_lista.pack(fill="both", expand=True, padx=10, pady=5)

        # Header da lista
        frame_lista_header = tk.Frame(frame_lista, bg=self.BG)
        frame_lista_header.pack(fill="x")

        self.lbl_voos_count = tk.Label(frame_lista_header, text="Nenhum voo encontrado",
                                        bg=self.BG, fg=self.FG_DIM, font=("Segoe UI", 9))
        self.lbl_voos_count.pack(side="left")

        btn_todos = tk.Button(frame_lista_header, text="Selecionar todos",
                              command=self._selecionar_todos_voos,
                              bg=self.BG, fg=self.ACCENT, relief="flat",
                              font=("Segoe UI", 8), cursor="hand2")
        btn_todos.pack(side="right")

        btn_nenhum = tk.Button(frame_lista_header, text="Nenhum",
                               command=self._desmarcar_todos_voos,
                               bg=self.BG, fg=self.FG_DIM, relief="flat",
                               font=("Segoe UI", 8), cursor="hand2")
        btn_nenhum.pack(side="right", padx=(0, 8))

        # Scrollable frame para os voos
        self.canvas_voos = tk.Canvas(frame_lista, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(frame_lista, orient="vertical", command=self.canvas_voos.yview)
        self.frame_voos_inner = tk.Frame(self.canvas_voos, bg=self.BG)

        self.frame_voos_inner.bind("<Configure>",
            lambda e: self.canvas_voos.configure(scrollregion=self.canvas_voos.bbox("all")))

        self.canvas_voos.create_window((0, 0), window=self.frame_voos_inner, anchor="nw")
        self.canvas_voos.configure(yscrollcommand=scrollbar.set)

        self.canvas_voos.pack(side="left", fill="both", expand=True, pady=5)
        scrollbar.pack(side="right", fill="y", pady=5)

        # Bind scroll do mouse
        self.canvas_voos.bind_all("<MouseWheel>",
            lambda e: self.canvas_voos.yview_scroll(int(-1*(e.delta/120)), "units"))

        # --- Botao Iniciar ---
        frame_bottom = tk.Frame(parent, bg=self.BG)
        frame_bottom.pack(fill="x", padx=10, pady=(5, 10))

        self.btn_iniciar = tk.Button(frame_bottom, text="INICIAR PROCESSAMENTO",
                                     command=self._iniciar_processamento_thread,
                                     bg=self.SUCCESS, fg="#000",
                                     font=("Segoe UI", 11, "bold"),
                                     relief="flat", cursor="hand2", padx=20, pady=8)
        self.btn_iniciar.pack(side="right")

    def _criar_aba_credenciais(self, parent):
        """Aba de configuracao / credenciais."""
        frame = tk.Frame(parent, bg=self.BG)
        frame.pack(fill="both", expand=True, padx=30, pady=20)

        # Nexlog
        self._section_label(frame, "NEXLOG", 0)
        self._field(frame, "CPF:", self.nexlog_user, 1)
        self._field(frame, "Senha:", self.nexlog_senha, 2, show="*")
        self._field(frame, "Base:", self.nexlog_base, 3)

        # SEFAZ
        self._section_label(frame, "SEFAZ-AL", 5)
        self._field(frame, "Usuario:", self.sefaz_user, 6)
        self._field(frame, "Senha:", self.sefaz_senha, 7, show="*")

        # Config
        self._section_label(frame, "GERAL", 9)
        self._field(frame, "Timeout (s):", self.timeout_var, 10, width=8)

        # Salvar
        tk.Button(frame, text="SALVAR", command=self._salvar_e_confirmar,
                  bg=self.ACCENT, fg="#000", font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=20, pady=4
                  ).grid(row=12, column=0, columnspan=2, pady=25)

    def _section_label(self, frame, text, row):
        tk.Label(frame, text=text, bg=self.BG, fg=self.ACCENT,
                 font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(15, 5))

    def _field(self, frame, label, var, row, show="", width=25):
        tk.Label(frame, text=label, bg=self.BG, fg=self.FG_DIM,
                 font=("Segoe UI", 9)).grid(row=row, column=0, sticky="e", padx=(0, 10), pady=3)
        entry = tk.Entry(frame, textvariable=var, width=width, show=show,
                         bg=self.BG_INPUT, fg=self.FG, insertbackground=self.FG,
                         relief="flat", font=("Segoe UI", 10))
        entry.grid(row=row, column=1, sticky="w", pady=3)

    def _salvar_e_confirmar(self):
        self._salvar_credenciais()
        self._log("Credenciais salvas!")

    def _criar_aba_log(self, parent):
        self.log_widget = scrolledtext.ScrolledText(
            parent, height=20, font=("Consolas", 9),
            bg="#0a0a0a", fg="#b0b0b0", insertbackground="white",
            relief="flat", borderwidth=0
        )
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)

    def _log(self, mensagem: str):
        """Adiciona mensagem ao log visual (thread-safe)."""
        def _update():
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_widget.insert(tk.END, f"[{timestamp}] {mensagem}\n")
            self.log_widget.see(tk.END)
        self.janela.after(0, _update)
        logger.info(mensagem)

    # ========= SELECAO DE VOOS =========

    def _atualizar_lista_voos(self, voos: List[Voo]):
        """Recria a lista de checkboxes com os voos encontrados."""
        def _update():
            # Limpa lista anterior
            for widget in self.frame_voos_inner.winfo_children():
                widget.destroy()
            self._voos_checkboxes.clear()

            if not voos:
                self.lbl_voos_count.config(text="Nenhum voo encontrado")
                return

            self.lbl_voos_count.config(text=f"{len(voos)} voo(s) encontrado(s)")

            for i, v in enumerate(voos):
                var = tk.BooleanVar(value=True)  # Todos marcados por padrao
                self._voos_checkboxes.append(var)

                # Frame do voo
                row = tk.Frame(self.frame_voos_inner, bg=self.BG_CARD,
                               highlightthickness=1, highlightbackground=self.BORDER)
                row.pack(fill="x", pady=2)

                inner_row = tk.Frame(row, bg=self.BG_CARD)
                inner_row.pack(fill="x", padx=10, pady=8)

                # Checkbox
                cb = tk.Checkbutton(inner_row, variable=var, bg=self.BG_CARD,
                                    activebackground=self.BG_CARD, selectcolor=self.BG_INPUT)
                cb.pack(side="left")

                # Numero do voo
                tk.Label(inner_row, text=v.numero_controle, bg=self.BG_CARD,
                         fg=self.FG, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(5, 15))

                # Origem/Destino
                tk.Label(inner_row, text=v.etapas, bg=self.BG_CARD,
                         fg=self.ACCENT, font=("Segoe UI", 9)).pack(side="left", padx=(0, 15))

                # Data/Hora
                tk.Label(inner_row, text=v.data_chegada, bg=self.BG_CARD,
                         fg=self.FG_DIM, font=("Segoe UI", 9)).pack(side="left")

        self.janela.after(0, _update)

    def _selecionar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(True)

    def _desmarcar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(False)

    def _obter_voos_selecionados(self) -> List[Voo]:
        """Retorna apenas os voos marcados com checkbox."""
        selecionados = []
        for i, var in enumerate(self._voos_checkboxes):
            if var.get() and i < len(self._voos_encontrados):
                selecionados.append(self._voos_encontrados[i])
        return selecionados

    # ========= ACOES =========

    def _buscar_voos_thread(self):
        """Busca voos em thread separada para nao travar a interface."""
        if self._processando:
            messagebox.showwarning("Aviso", "Ja existe um processo em andamento.")
            return
        threading.Thread(target=self._buscar_voos, daemon=True).start()

    def _buscar_voos(self):
        """Busca voos no Nexlog pela data selecionada."""
        self._salvar_credenciais()
        data_ini = self.data_inicial.get().strip()
        data_fim = self.data_final.get().strip()

        if not data_ini or not data_fim:
            messagebox.showwarning("Aviso", "Preencha as datas.")
            return

        self._log(f"Buscando voos de {data_ini} ate {data_fim}...")
        self._processando = True

        try:
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            voos_mod = NexlogVoos(browser)
            voos = voos_mod.pesquisar_voos(data_ini, data_fim)

            self._voos_encontrados = voos
            self._atualizar_lista_voos(voos)

            self._log(f"Encontrados {len(voos)} voos!")
            browser.fechar()

        except Exception as e:
            self._log(f"ERRO ao buscar voos: {e}")
            messagebox.showerror("Erro", str(e))
        finally:
            self._processando = False

    def _iniciar_processamento_thread(self):
        """Inicia processamento completo em thread separada."""
        if self._processando:
            messagebox.showwarning("Aviso", "Ja existe um processo em andamento.")
            return

        if not self._voos_encontrados:
            messagebox.showwarning("Aviso", "Busque os voos primeiro.")
            return

        # Pega apenas os voos selecionados via checkbox
        voos_selecionados = self._obter_voos_selecionados()
        if not voos_selecionados:
            messagebox.showwarning("Aviso", "Selecione pelo menos um voo.")
            return

        confirma = messagebox.askyesno(
            "Confirmar",
            f"Processar {len(voos_selecionados)} voo(s) selecionado(s)?\n\n"
            "Isso vai:\n"
            "1. Verificar termos na SEFAZ\n"
            "2. Adicionar comentarios criticos\n"
            "3. Liberar AWBs sem termo\n\n"
            "Deseja continuar?"
        )
        if not confirma:
            return

        threading.Thread(target=self._processar_voos, daemon=True).start()

    def _processar_voos(self):
        """Processamento completo dos voos selecionados."""
        self._salvar_credenciais()
        self._processando = True

        # Usa apenas os voos marcados via checkbox
        voos = self._obter_voos_selecionados()

        self._log("=" * 60)
        self._log(f"INICIANDO PROCESSAMENTO DE {len(voos)} VOOS")
        self._log("=" * 60)

        try:
            # Inicia navegador
            browser = NexlogBrowser()
            browser.iniciar()
            browser.login_nexlog()

            # Modulos
            voos_mod = NexlogVoos(browser)
            cte_mod = NexlogCTeOperacoes(browser)
            liberar_mod = NexlogLiberar(browser)
            outlook = OutlookWeb(browser.driver)
            sefaz = SefazConsulta(browser.driver)

            # Processa cada voo
            for i, voo in enumerate(voos):
                self._log(f"\n{'='*40}")
                self._log(f"VOO {i+1}/{len(voos)}: {voo.numero_controle} ({voo.etapas})")
                self._log(f"{'='*40}")

                try:
                    resultado = self._processar_um_voo(
                        voo, browser, voos_mod, cte_mod, liberar_mod, outlook, sefaz
                    )
                    self._log(resultado.resumo())
                except Exception as e:
                    self._log(f"ERRO no voo {voo.numero_controle}: {e}")

                self._log("")

            # Finaliza
            outlook.fechar_aba()
            sefaz.fechar_aba()
            browser.fechar()

            self._log("=" * 60)
            self._log("PROCESSAMENTO CONCLUIDO!")
            self._log("=" * 60)

            messagebox.showinfo("Concluido", "Processamento finalizado!")

        except Exception as e:
            self._log(f"ERRO CRITICO: {e}")
            messagebox.showerror("Erro", str(e))
        finally:
            self._processando = False

    def _processar_um_voo(self, voo: Voo, browser, voos_mod, cte_mod, liberar_mod, outlook, sefaz) -> ResultadoProcessamento:
        """Processa um unico voo completo."""
        resultado = ResultadoProcessamento(voo=voo)
        data_ini = self.data_inicial.get().strip()
        data_fim = self.data_final.get().strip()

        # --- ETAPA 1: Buscar chave MDF-e ---
        self._log("  [1/6] Buscando chave MDF-e...")
        browser.navegar_operacoes_gerenciar_rotas()
        voos_mod.pesquisar_voos(data_ini, data_fim)
        time.sleep(2)
        chave = voos_mod.extrair_chave_mdfe(voo)

        if not chave:
            resultado.erros.append("Chave MDF-e nao encontrada")
            self._log("  ERRO: Chave MDF-e nao encontrada")
            return resultado

        self._log(f"  Chave: {chave[:20]}...")

        # --- ETAPA 2: Baixar manifesto (RETIRA/ENTREGA) ---
        self._log("  [2/6] Baixando manifesto...")
        # Garante que estamos na pagina de Operacoes > Gerenciar Rotas com a tabela visivel
        # (a etapa 1 pode ter aberto/fechado modais que prejudicam a tabela)
        browser.navegar_operacoes_gerenciar_rotas()
        voos_mod.pesquisar_voos(data_ini, data_fim)
        time.sleep(2)
        caminho_manifesto = voos_mod.baixar_manifesto(voo)
        manifesto = None
        if caminho_manifesto:
            manifesto = parsear_manifesto_pdf(caminho_manifesto)
            self._log(f"  Manifesto: {len(manifesto.awbs_retira)} RETIRA | {len(manifesto.awbs_entrega)} ENTREGA")
        else:
            self._log("  AVISO: Nao conseguiu baixar manifesto")

        # --- ETAPA 3: Verificar Outlook (tenta primeiro) ---
        self._log("  [3/6] Verificando Outlook...")
        consulta = None
        resposta = RespostaEmail.INDEFINIDO
        usou_outlook = False

        try:
            outlook.abrir_outlook()
            resposta = outlook.buscar_por_chave(chave)
            self._log(f"  Resposta email: {resposta.value}")

            if resposta == RespostaEmail.SEM_TERMOS:
                # Email confirma sem termos — confiavel
                self._log("  Email diz SEM termos - voo liberado")
                usou_outlook = True
            elif resposta == RespostaEmail.COM_TERMOS:
                # Tenta baixar PDF do email
                caminho_pdf = outlook.baixar_anexo_pdf()
                if caminho_pdf:
                    consulta = parsear_relatorio_pdf(caminho_pdf)
                    # Valida que o PDF e do voo correto
                    if consulta.chave and chave and consulta.chave != chave:
                        self._log(f"  AVISO: PDF e de outro voo - descartando")
                        consulta = None
                    elif consulta.total_termos > 0:
                        self._log(f"  Relatorio do email: {consulta.total_termos} termos")
                        usou_outlook = True
                    else:
                        # PDF sem termos mas email dizia COM — inconsistente
                        self._log("  AVISO: PDF sem termos (inconsistente) - consultando site")
                        consulta = None
                else:
                    self._log("  Email sem anexo PDF - consultando site SEFAZ")
            # Se NAO_RESPONDEU ou INDEFINIDO -> vai pro site
        except Exception as e:
            self._log(f"  Outlook erro: {e}")

        outlook.voltar_para_nexlog()

        # --- ETAPA 4: Consultar site SEFAZ (se Outlook nao resolveu) ---
        if not usou_outlook and consulta is None and resposta != RespostaEmail.SEM_TERMOS:
            self._log("  [4/6] Consultando site SEFAZ...")
            try:
                sefaz.abrir_sefaz()
                if not sefaz.logado:
                    sefaz.login()
                sefaz.navegar_consulta_analise_mdfe()
                consulta = sefaz.consultar_chave_mdfe(chave)

                if consulta:
                    # SEGURANCA: Bloqueia APENAS se relatorio NAO foi extraido
                    # (status desconhecido + 0 termos + lista vazia)
                    # Se o parser retornou status real (liberado, sem pendencias, etc)
                    # com 0 termos, e CONFIAVEL — pode liberar.
                    relatorio_nao_extraido = (
                        consulta.status.value == "desconhecido"
                        and consulta.total_termos == 0
                        and not consulta.termos
                        and not consulta.numero_mdfe
                    )
                    if relatorio_nao_extraido:
                        self._log("  Site SEFAZ: INCONCLUSIVO (relatorio nao extraido)")
                        self._log("  SEGURANCA: NAO libera este voo - consultar manualmente")
                        resultado.erros.append("SEFAZ inconclusivo - consultar manualmente")
                        sefaz.voltar_para_nexlog()
                        return resultado
                    else:
                        self._log(f"  Site SEFAZ: {consulta.total_termos} termos "
                                 f"(status: {consulta.status.value})")
                else:
                    self._log("  Site SEFAZ: sem resultado")
                    # Nao respondeu no email E nao tem no site — pula voo
                    if resposta == RespostaEmail.NAO_RESPONDEU:
                        self._log("  SEFAZ nao respondeu e site sem resultado - pulando voo")
                        resultado.erros.append("SEFAZ sem resposta")
                        sefaz.voltar_para_nexlog()
                        return resultado

                sefaz.voltar_para_nexlog()
            except Exception as e:
                self._log(f"  Erro SEFAZ: {e}")
                try:
                    sefaz.voltar_para_nexlog()
                except Exception:
                    pass
        else:
            self._log("  [4/6] Outlook resolveu - pulando site SEFAZ")

        # --- ETAPA 5: Adicionar comentarios nos CTes retidos ---
        mapa_cte_awb = {}  # Mapeamento CTe -> AWB (usado na etapa 6 para filtrar)

        if consulta and consulta.termos:
            self._log(f"  [5/6] Adicionando comentarios ({len(consulta.ctes_retidos)} CTes)...")

            for cte in set(consulta.ctes_retidos):
                comentario = consulta.comentario_para_cte(cte)
                awb = cte_mod.buscar_awb_do_cte(cte)

                if awb:
                    mapa_cte_awb[cte] = awb  # Salva mapeamento para etapa 6

                    # Verifica se o AWB e servico MELI/Meli Belly
                    # AWBs MELI nao precisam de comentario de retido
                    servico_awb = cte_mod.verificar_servico_awb(awb)
                    if servico_awb and "MELI" in servico_awb.upper():
                        self._log(f"    AWB {awb} (CTe {cte}): servico MELI - pula comentario")
                        continue

                    sucesso = cte_mod.adicionar_comentario_critico(awb, comentario)
                    if sucesso:
                        resultado.comentarios_adicionados += 1
                    else:
                        resultado.comentarios_falha += 1
                else:
                    resultado.comentarios_falha += 1
                    self._log(f"    CTe {cte}: AWB nao encontrado")

            self._log(f"  Comentarios: {resultado.comentarios_adicionados} OK / {resultado.comentarios_falha} falhas")
        else:
            self._log("  [5/6] Sem termos para comentar")

        # --- ETAPA 6: Liberar AWBs ---
        self._log("  [6/6] Liberando AWBs...")

        # Calcula quais AWBs liberar
        awbs_para_liberar = set()
        awbs_com_termo = set()

        if manifesto:
            if resposta == RespostaEmail.SEM_TERMOS:
                # Libera todos RETIRA (nao tem termos)
                awbs_para_liberar = manifesto.awbs_retira.copy()
            elif consulta and consulta.termos:
                # TEM termos — precisa excluir AWBs retidos pela SEFAZ
                # Usa o mapeamento CTe->AWB da etapa de comentarios
                # para saber quais AWBs NÃO devem ser liberados
                ctes_retidos = set(consulta.ctes_retidos)

                # Coleta AWBs que tem termo (foram mapeados na etapa 5)
                for cte in ctes_retidos:
                    awb = mapa_cte_awb.get(cte, "")
                    if awb:
                        awbs_com_termo.add(awb)
                        self._log(f"    AWB {awb} (CTe {cte}) -> RETIDO (nao libera)")

                # Libera apenas RETIRA que NAO tem termo
                awbs_para_liberar = manifesto.awbs_retira - awbs_com_termo

                if awbs_com_termo:
                    self._log(f"  {len(awbs_com_termo)} AWB(s) com termo (nao libera)")
                    resultado.awbs_retidos = list(awbs_com_termo)
            else:
                # Sem consulta ou consulta sem termos — libera todos RETIRA
                awbs_para_liberar = manifesto.awbs_retira.copy()

            resultado.awbs_domicilio = list(manifesto.awbs_entrega)
        else:
            self._log("  AVISO: Sem manifesto - nao pode liberar")

        if awbs_para_liberar:
            self._log(f"  Liberando {len(awbs_para_liberar)} AWBs...")
            res_lib = liberar_mod.liberar_awbs(awbs_para_liberar, data_ini, data_fim)
            resultado.awbs_liberados = res_lib.get("selecionados", [])
            resultado.awbs_ja_liberados = res_lib.get("ja_liberados", [])
        else:
            self._log("  Nenhum AWB para liberar")

        return resultado

    def executar(self):
        """Inicia a interface."""
        self.janela.mainloop()


# ========= ENTRY POINT =========
if __name__ == "__main__":
    app = AppAutomacao()
    app.executar()
