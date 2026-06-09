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

    def __init__(self):
        self.janela = tk.Tk()
        self.janela.title("Automacao SEFAZ - Retirada de Voos")
        self.janela.geometry("850x700")
        self.janela.configure(bg="#1a1a2e")
        self.janela.resizable(True, True)

        # Variaveis
        self.nexlog_user = tk.StringVar()
        self.nexlog_senha = tk.StringVar()
        self.nexlog_base = tk.StringVar(value="MCZ")
        self.sefaz_user = tk.StringVar()
        self.sefaz_senha = tk.StringVar()
        self.data_inicial = tk.StringVar()
        self.data_final = tk.StringVar()
        self.usar_a_partir_de = tk.BooleanVar(value=False)
        self.voo_selecionado = tk.StringVar()
        self.timeout_var = tk.IntVar(value=20)

        # Estado
        self._voos_encontrados: List[Voo] = []
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
        self._log("Credenciais salvas!")

    def _criar_interface(self):
        """Cria a interface completa."""
        # Titulo
        titulo = tk.Label(
            self.janela, text="RETIRADA DE VOOS - SEFAZ",
            bg="#1a1a2e", fg="#00ffe1", font=("Segoe UI", 18, "bold"),
        )
        titulo.pack(pady=10)

        # Notebook (abas)
        notebook = ttk.Notebook(self.janela)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # --- ABA CREDENCIAIS ---
        aba_cred = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_cred, text="  Credenciais  ")
        self._criar_aba_credenciais(aba_cred)

        # --- ABA VOOS ---
        aba_voos = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_voos, text="  Voos  ")
        self._criar_aba_voos(aba_voos)

        # --- ABA LOG ---
        aba_log = tk.Frame(notebook, bg="#16213e")
        notebook.add(aba_log, text="  Log  ")
        self._criar_aba_log(aba_log)

    def _criar_aba_credenciais(self, parent):
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

        # Timeout
        tk.Label(frame, text="CONFIGURACAO", bg="#16213e", fg="#00ffe1",
                 font=("Segoe UI", 12, "bold")).grid(row=9, column=0, columnspan=2, pady=(20, 10))
        tk.Label(frame, text="Timeout (s):", bg="#16213e", fg="white").grid(row=10, column=0, sticky="e", padx=5)
        tk.Entry(frame, textvariable=self.timeout_var, width=10).grid(row=10, column=1, sticky="w", pady=3)
        tk.Label(frame, text="(aumente para PC/internet lenta)", bg="#16213e", fg="#888",
                 font=("Segoe UI", 8)).grid(row=11, column=1, sticky="w")

        # Botao salvar
        tk.Button(frame, text="SALVAR", command=self._salvar_credenciais,
                  bg="#0f3460", fg="white", font=("Segoe UI", 10, "bold"),
                  relief="flat", cursor="hand2").grid(row=13, column=0, columnspan=2, pady=20)

    def _criar_aba_voos(self, parent):
        frame = tk.Frame(parent, bg="#16213e")
        frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Selecao de datas
        frame_datas = tk.Frame(frame, bg="#16213e")
        frame_datas.pack(fill="x", pady=10)

        tk.Label(frame_datas, text="Data inicial:", bg="#16213e", fg="white").pack(side="left", padx=5)
        tk.Entry(frame_datas, textvariable=self.data_inicial, width=12).pack(side="left", padx=5)
        tk.Label(frame_datas, text="Data final:", bg="#16213e", fg="white").pack(side="left", padx=15)
        tk.Entry(frame_datas, textvariable=self.data_final, width=12).pack(side="left", padx=5)

        # Checkbox "A partir de um voo"
        frame_apartir = tk.Frame(frame, bg="#16213e")
        frame_apartir.pack(fill="x", pady=5)

        tk.Checkbutton(frame_apartir, text="A partir de um voo especifico:",
                       variable=self.usar_a_partir_de, bg="#16213e", fg="white",
                       selectcolor="#0f3460", activebackground="#16213e",
                       activeforeground="white").pack(side="left")

        self.combo_voos = ttk.Combobox(frame_apartir, textvariable=self.voo_selecionado,
                                        width=40, state="readonly")
        self.combo_voos.pack(side="left", padx=10)

        # Botoes
        frame_btns = tk.Frame(frame, bg="#16213e")
        frame_btns.pack(fill="x", pady=15)

        tk.Button(frame_btns, text="BUSCAR VOOS", command=self._buscar_voos_thread,
                  bg="#0f3460", fg="white", font=("Segoe UI", 10, "bold"),
                  relief="flat", cursor="hand2").pack(side="left", padx=5)

        tk.Button(frame_btns, text="INICIAR PROCESSAMENTO",
                  command=self._iniciar_processamento_thread,
                  bg="#e94560", fg="white", font=("Segoe UI", 11, "bold"),
                  relief="flat", cursor="hand2").pack(side="left", padx=20)

        # Lista de voos encontrados
        tk.Label(frame, text="Voos encontrados:", bg="#16213e", fg="white",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 2))

        self.lista_voos = tk.Listbox(frame, height=8, font=("Consolas", 9),
                                      bg="#0d1117", fg="#c9d1d9", selectmode="single")
        self.lista_voos.pack(fill="both", expand=True)

    def _criar_aba_log(self, parent):
        self.log_widget = scrolledtext.ScrolledText(
            parent, height=20, font=("Consolas", 9), bg="#0d1117", fg="#c9d1d9",
            insertbackground="white"
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

    def _atualizar_lista_voos(self, voos: List[Voo]):
        """Atualiza a lista visual de voos."""
        def _update():
            self.lista_voos.delete(0, tk.END)
            opcoes_combo = []
            for v in voos:
                texto = f"{v.numero_controle} | {v.etapas} | {v.data_chegada} | {v.assinado}"
                self.lista_voos.insert(tk.END, texto)
                opcoes_combo.append(f"{v.numero_controle} - {v.etapas} {v.hora_chegada}")
            self.combo_voos['values'] = opcoes_combo
        self.janela.after(0, _update)

    def _iniciar_processamento_thread(self):
        """Inicia processamento completo em thread separada."""
        if self._processando:
            messagebox.showwarning("Aviso", "Ja existe um processo em andamento.")
            return

        if not self._voos_encontrados:
            messagebox.showwarning("Aviso", "Busque os voos primeiro.")
            return

        confirma = messagebox.askyesno(
            "Confirmar",
            f"Iniciar processamento de {len(self._voos_encontrados)} voos?\n\n"
            "Isso vai:\n"
            "1. Verificar emails da SEFAZ\n"
            "2. Adicionar comentarios criticos\n"
            "3. Liberar AWBs sem termo\n\n"
            "Deseja continuar?"
        )
        if not confirma:
            return

        threading.Thread(target=self._processar_voos, daemon=True).start()

    def _processar_voos(self):
        """Processamento completo de todos os voos."""
        self._salvar_credenciais()
        self._processando = True

        # Determina quais voos processar
        voos = self._voos_encontrados[:]

        # Se "a partir de um voo" esta marcado, filtra
        if self.usar_a_partir_de.get() and self.voo_selecionado.get():
            voo_inicio = self.voo_selecionado.get().split(" - ")[0].strip()
            encontrou = False
            voos_filtrados = []
            for v in voos:
                if v.numero_controle == voo_inicio:
                    encontrou = True
                if encontrou:
                    voos_filtrados.append(v)
            voos = voos_filtrados

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
