"""
Aero - Automacao de Operacoes Aereas
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
from tkinter import messagebox, scrolledtext
from datetime import datetime, timedelta
from typing import List, Optional

import customtkinter as ctk

try:
    from tkcalendar import DateEntry
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

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

# ========= CUSTOMTKINTER CONFIG =========
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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

    def __init__(self):
        self.janela = ctk.CTk()
        self.janela.title("Aero")
        self.janela.geometry("900x720")
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

        # Datas padrao (hoje)
        hoje = datetime.now().strftime("%d/%m/%Y")
        self.data_inicial.set(hoje)
        self.data_final.set(hoje)

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
        """Interface minimalista dark com CustomTkinter."""
        # Header
        header = ctk.CTkFrame(self.janela, fg_color="transparent", height=50)
        header.pack(fill="x", padx=24, pady=(18, 6))

        ctk.CTkLabel(header, text="AERO",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkLabel(header, text="Operacoes Aereas",
                     font=ctk.CTkFont(size=11),
                     text_color="#6b7280").pack(side="left", padx=12)

        # Tabview (abas)
        self.tabview = ctk.CTkTabview(self.janela, corner_radius=8)
        self.tabview.pack(fill="both", expand=True, padx=24, pady=(6, 18))

        # --- ABA VOOS ---
        self.tabview.add("Voos")
        self._criar_aba_voos(self.tabview.tab("Voos"))

        # --- ABA CREDENCIAIS ---
        self.tabview.add("Config")
        self._criar_aba_credenciais(self.tabview.tab("Config"))

        # --- ABA LOG ---
        self.tabview.add("Log")
        self._criar_aba_log(self.tabview.tab("Log"))

    def _criar_aba_voos(self, parent):
        """Aba principal com busca de voos e checkboxes."""
        # --- Barra de busca ---
        frame_busca = ctk.CTkFrame(parent, corner_radius=8)
        frame_busca.pack(fill="x", padx=8, pady=(8, 4))

        inner = ctk.CTkFrame(frame_busca, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(inner, text="Periodo:",
                     font=ctk.CTkFont(size=12)).pack(side="left")

        if HAS_CALENDAR:
            # DateEntry com calendario clicavel (widget tkinter, funciona dentro do CTk)
            self.de_ini = DateEntry(inner, width=10, date_pattern="dd/MM/yyyy",
                                    background="#1f2937", foreground="#e5e7eb",
                                    headersbackground="#111827",
                                    headersforeground="#60a5fa",
                                    selectbackground="#3b82f6",
                                    selectforeground="#fff",
                                    font=("Segoe UI", 10))
            self.de_ini.set_date(datetime.now())
            self.de_ini.pack(side="left", padx=(10, 6))

            ctk.CTkLabel(inner, text="a",
                         font=ctk.CTkFont(size=12),
                         text_color="#6b7280").pack(side="left")

            self.de_fim = DateEntry(inner, width=10, date_pattern="dd/MM/yyyy",
                                    background="#1f2937", foreground="#e5e7eb",
                                    headersbackground="#111827",
                                    headersforeground="#60a5fa",
                                    selectbackground="#3b82f6",
                                    selectforeground="#fff",
                                    font=("Segoe UI", 10))
            self.de_fim.set_date(datetime.now())
            self.de_fim.pack(side="left", padx=(6, 16))
        else:
            # Fallback: CTkEntry se tkcalendar nao instalado
            e1 = ctk.CTkEntry(inner, textvariable=self.data_inicial, width=110,
                              placeholder_text="dd/mm/aaaa",
                              font=ctk.CTkFont(size=12))
            e1.pack(side="left", padx=(10, 6))

            ctk.CTkLabel(inner, text="a",
                         font=ctk.CTkFont(size=12),
                         text_color="#6b7280").pack(side="left")

            e2 = ctk.CTkEntry(inner, textvariable=self.data_final, width=110,
                              placeholder_text="dd/mm/aaaa",
                              font=ctk.CTkFont(size=12))
            e2.pack(side="left", padx=(6, 16))

        ctk.CTkButton(inner, text="BUSCAR", command=self._buscar_voos_thread,
                      width=100, height=32,
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        # --- Lista de voos com checkboxes ---
        frame_lista = ctk.CTkFrame(parent, fg_color="transparent")
        frame_lista.pack(fill="both", expand=True, padx=8, pady=4)

        # Header da lista
        frame_lista_header = ctk.CTkFrame(frame_lista, fg_color="transparent")
        frame_lista_header.pack(fill="x", pady=(0, 4))

        self.lbl_voos_count = ctk.CTkLabel(frame_lista_header,
                                            text="Nenhum voo encontrado",
                                            font=ctk.CTkFont(size=11),
                                            text_color="#6b7280")
        self.lbl_voos_count.pack(side="left")

        ctk.CTkButton(frame_lista_header, text="Selecionar todos",
                      command=self._selecionar_todos_voos,
                      width=110, height=26,
                      fg_color="transparent", border_width=1,
                      text_color="#60a5fa",
                      font=ctk.CTkFont(size=11)).pack(side="right")

        ctk.CTkButton(frame_lista_header, text="Nenhum",
                      command=self._desmarcar_todos_voos,
                      width=70, height=26,
                      fg_color="transparent", border_width=1,
                      text_color="#6b7280",
                      font=ctk.CTkFont(size=11)).pack(side="right", padx=(0, 8))

        # Scrollable frame para os voos (substitui Canvas+Scrollbar)
        self.frame_voos_scroll = ctk.CTkScrollableFrame(frame_lista, corner_radius=6)
        self.frame_voos_scroll.pack(fill="both", expand=True, pady=4)

        # --- Botao Iniciar ---
        frame_bottom = ctk.CTkFrame(parent, fg_color="transparent")
        frame_bottom.pack(fill="x", padx=8, pady=(4, 8))

        self.btn_iniciar = ctk.CTkButton(
            frame_bottom, text="INICIAR PROCESSAMENTO",
            command=self._iniciar_processamento_thread,
            width=220, height=40,
            fg_color="#22c55e", hover_color="#16a34a",
            text_color="#000000",
            font=ctk.CTkFont(size=13, weight="bold"))
        self.btn_iniciar.pack(side="right")

    def _criar_aba_credenciais(self, parent):
        """Aba de configuracao / credenciais."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
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
        self._field(frame, "Timeout (s):", self.timeout_var, 10, width=100)

        # Salvar
        ctk.CTkButton(frame, text="SALVAR", command=self._salvar_e_confirmar,
                      width=140, height=36,
                      font=ctk.CTkFont(size=12, weight="bold")
                      ).grid(row=12, column=0, columnspan=2, pady=25)

    def _section_label(self, frame, text, row):
        ctk.CTkLabel(frame, text=text,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#60a5fa").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(18, 6))

    def _field(self, frame, label, var, row, show="", width=220):
        ctk.CTkLabel(frame, text=label,
                     font=ctk.CTkFont(size=12),
                     text_color="#9ca3af").grid(
            row=row, column=0, sticky="e", padx=(0, 12), pady=4)

        entry = ctk.CTkEntry(frame, textvariable=var, width=width,
                             show=show if show else "",
                             font=ctk.CTkFont(size=12))
        entry.grid(row=row, column=1, sticky="w", pady=4)

    def _salvar_e_confirmar(self):
        self._salvar_credenciais()
        self._log("Credenciais salvas!")

    def _criar_aba_log(self, parent):
        self.log_widget = scrolledtext.ScrolledText(
            parent, height=20, font=("Consolas", 9),
            bg="#0a0a0a", fg="#b0b0b0", insertbackground="white",
            relief="flat", borderwidth=0
        )
        self.log_widget.pack(fill="both", expand=True, padx=8, pady=8)

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
            for widget in self.frame_voos_scroll.winfo_children():
                widget.destroy()
            self._voos_checkboxes.clear()

            if not voos:
                self.lbl_voos_count.configure(text="Nenhum voo encontrado")
                return

            self.lbl_voos_count.configure(text=f"{len(voos)} voo(s) encontrado(s)")

            for i, v in enumerate(voos):
                var = tk.BooleanVar(value=True)  # Todos marcados por padrao
                self._voos_checkboxes.append(var)

                # Frame do voo (card)
                row = ctk.CTkFrame(self.frame_voos_scroll, corner_radius=6)
                row.pack(fill="x", pady=3, padx=2)

                inner_row = ctk.CTkFrame(row, fg_color="transparent")
                inner_row.pack(fill="x", padx=12, pady=8)

                # Checkbox com numero do voo
                cb = ctk.CTkCheckBox(inner_row, variable=var,
                                     text=v.numero_controle,
                                     font=ctk.CTkFont(size=12, weight="bold"),
                                     width=24)
                cb.pack(side="left")

                # Origem/Destino
                ctk.CTkLabel(inner_row, text=v.etapas,
                             font=ctk.CTkFont(size=11),
                             text_color="#60a5fa").pack(side="left", padx=(16, 0))

                # Data/Hora
                ctk.CTkLabel(inner_row, text=v.data_chegada,
                             font=ctk.CTkFont(size=11),
                             text_color="#6b7280").pack(side="left", padx=(16, 0))

        self.janela.after(0, _update)

    def _selecionar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(True)
        # Update checkbox widgets
        for widget in self.frame_voos_scroll.winfo_children():
            for child in widget.winfo_children():
                for cb in child.winfo_children():
                    if isinstance(cb, ctk.CTkCheckBox):
                        cb.select()

    def _desmarcar_todos_voos(self):
        for var in self._voos_checkboxes:
            var.set(False)
        # Update checkbox widgets
        for widget in self.frame_voos_scroll.winfo_children():
            for child in widget.winfo_children():
                for cb in child.winfo_children():
                    if isinstance(cb, ctk.CTkCheckBox):
                        cb.deselect()

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

        # Pega datas do DateEntry ou do campo texto
        if HAS_CALENDAR:
            data_ini = self.de_ini.get()
            data_fim = self.de_fim.get()
        else:
            data_ini = self.data_inicial.get().strip()
            data_fim = self.data_final.get().strip()

        if not data_ini or not data_fim:
            messagebox.showwarning("Aviso", "Preencha as datas.")
            return

        self._log(f"Buscando voos de {data_ini} ate {data_fim}...")
        self._processando = True

        try:
            browser = NexlogBrowser()
            # Headless = navegador invisivel (mais rapido, sem janela)
            # Se o Nexlog der problema com headless, mude para browser.iniciar()
            browser.iniciar(headless=True)
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

        # Pega datas
        if HAS_CALENDAR:
            data_ini = self.de_ini.get()
            data_fim = self.de_fim.get()
        else:
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
