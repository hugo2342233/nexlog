"""
Modulo de consulta no site da SEFAZ-AL (transportadoras.sefaz.al.gov.br).
Realiza login, consulta Analise MDF-e por chave, e extrai relatorio.

Fluxo do site:
1. Acessa transportadoras.sefaz.al.gov.br/#/
2. Clica no menu "Conta" (dropdown no header direito)
3. Clica em "Entrar" (abre modal de autenticacao)
4. Preenche "Seu usuario" e "Sua senha"
5. Clica botao "Entrar" (azul, dentro do modal)
6. Apos login: 3 botoes (Consultar TADe, Consulta Analise MDF-e, Registro de Passagem)
7. Clica "Consulta Analise MDF-e"
8. Preenche campo "Chave/Numero do MDF-e"
9. Clica "Pesquisar"
10. Resultado: tabela com Status, Data, Posto, N MDF-e, Emitente, TAs emitidos
11. Clica "Imprimir Relatorio" -> abre/baixa PDF com detalhes dos termos
"""

import time
import logging
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from config import config
from models.termo import ConsultaMDFe, StatusMDFe
from modules.parser import parsear_relatorio_sefaz, parsear_relatorio_pdf

logger = logging.getLogger(__name__)


class SefazConsulta:
    """Gerencia consultas no site da SEFAZ-AL."""

    def __init__(self, driver: webdriver.Chrome):
        """Usa o mesmo driver do navegador principal."""
        self.driver = driver
        self.wait = WebDriverWait(driver, config.timeout_padrao)
        self._logado = False
        self._aba_sefaz = None
        self._aba_original = None

    def abrir_sefaz(self):
        """Abre o site da SEFAZ em uma nova aba."""
        self._aba_original = self.driver.current_window_handle

        # Abre nova aba
        self.driver.execute_script("window.open('');")
        time.sleep(1)
        abas = self.driver.window_handles
        self._aba_sefaz = abas[-1]
        self.driver.switch_to.window(self._aba_sefaz)

        self.driver.get(config.url_sefaz)
        time.sleep(5)

    def login(self, usuario: str = None, senha: str = None):
        """
        Faz login no portal de transportadoras da SEFAZ-AL.
        Fluxo: Conta -> Entrar -> Modal autenticacao -> Preenche -> Botao Entrar
        Se falhar, mostra pausa inteligente para login manual.
        """
        usuario = usuario or config.sefaz.usuario
        senha = senha or config.sefaz.senha

        if not usuario or not senha:
            raise ValueError("Credenciais da SEFAZ nao configuradas.")

        # Muda para aba da SEFAZ
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            # Tenta via link "login" no meio da pagina OU menu "Conta"
            try:
                # Primeiro tenta o link "login" direto na mensagem
                link_login = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'login') and not(contains(@class,'nav'))]"
                        " | //a[@href='#' and contains(.,'login')]"
                    ))
                )
                link_login.click()
                time.sleep(2)
            except TimeoutException:
                # Se nao achou, tenta via menu Conta
                menu_conta = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'Conta')]"
                        " | //li[contains(@class,'dropdown')]//a[contains(.,'Conta')]"
                    ))
                )
                menu_conta.click()
                time.sleep(1)

                opcao_entrar = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(.,'Entrar')]"
                        " | //div[contains(@class,'dropdown-menu')]//a[contains(.,'Entrar')]"
                    ))
                )
                opcao_entrar.click()
                time.sleep(2)

            # Aguarda modal de autenticacao abrir
            # Campo usuario com placeholder "Seu usuario"
            campo_usuario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@placeholder,'usu') or contains(@placeholder,'Usu')]"
                    " | //div[contains(@class,'modal')]//input[@type='text']"
                    " | //div[contains(@class,'modal')]//input[not(@type='password') "
                    "and not(@type='checkbox') and not(@type='hidden')]"
                ))
            )
            campo_usuario.clear()
            campo_usuario.send_keys(usuario)

            # Campo senha com placeholder "Sua senha"
            campo_senha = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[@type='password']"
                ))
            )
            campo_senha.clear()
            campo_senha.send_keys(senha)

            # Botao "Entrar" (azul, dentro do modal)
            botao_entrar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(.,'Entrar')]"
                    " | //button[contains(@class,'btn-primary') and contains(.,'Entrar')]"
                    " | //button[contains(.,'Entrar') and @type='submit']"
                ))
            )
            botao_entrar.click()
            time.sleep(5)

            # Verifica login bem sucedido
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        "//*[contains(.,'logado como')]"
                        " | //*[contains(.,'Consultar TADe')]"
                        " | //*[contains(.,'Consulta An')]"
                    ))
                )
                self._logado = True
                logger.info("SEFAZ: Login realizado com sucesso!")
            except TimeoutException:
                # Pode ter dado erro - tenta pausa inteligente
                self._aguardar_login_manual_sefaz()

        except Exception as e:
            logger.error(f"SEFAZ: Erro no login automatico: {e}")
            # Tenta pausa inteligente como fallback
            self._aguardar_login_manual_sefaz()

    def _aguardar_login_manual_sefaz(self):
        """
        Pausa inteligente para login manual na SEFAZ.
        Mostra popup e aguarda o usuario fazer login.
        """
        import tkinter as tk
        from tkinter import messagebox

        logger.warning("SEFAZ: Login automatico falhou - aguardando login manual")

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        messagebox.showinfo(
            "Login Necessario - SEFAZ",
            "O login automatico na SEFAZ falhou.\n\n"
            "Possiveis causas:\n"
            "- Senha alterada\n"
            "- Site fora do ar\n"
            "- Captcha ou verificacao\n\n"
            "Por favor, faca login manualmente na janela do Chrome\n"
            "e depois clique OK para continuar.",
            parent=root
        )
        root.destroy()

        # Verifica se logou
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(.,'logado como')]"
                    " | //*[contains(.,'Consultar TADe')]"
                    " | //*[contains(.,'Consulta An')]"
                ))
            )
            self._logado = True
            logger.info("SEFAZ: Login manual realizado com sucesso!")
        except TimeoutException:
            logger.error("SEFAZ: Login nao detectado apos intervencao manual")
            raise RuntimeError("Falha no login da SEFAZ")

    def navegar_consulta_analise_mdfe(self):
        """Clica no botao 'Consulta Analise MDF-e' (botao azul grande)."""
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            botao_mdfe = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(.,'lise MDF') and (self::a or self::button or self::div)]"
                    "[not(contains(.,'Painel'))]"
                    " | //a[contains(.,'lise MDF')]"
                    " | //div[contains(.,'Consulta') and contains(.,'MDF')]"
                    "/ancestor-or-self::a"
                ))
            )
            botao_mdfe.click()
            time.sleep(3)
            logger.info("SEFAZ: Na pagina de Consulta Analise MDF-e")
        except TimeoutException:
            # Tenta via URL direta
            self.driver.get("https://transportadoras.sefaz.al.gov.br/#/painel-mdfes-analisados")
            time.sleep(3)

    def consultar_chave_mdfe(self, chave: str) -> Optional[ConsultaMDFe]:
        """
        Consulta uma chave de MDF-e no site da SEFAZ.

        Returns:
            ConsultaMDFe com dados extraidos, ou None se deu erro/nao encontrou
        """
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)

        try:
            # Campo "Chave/Numero do MDF-e"
            campo_chave = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@placeholder,'have') or contains(@placeholder,'MDF') "
                    "or contains(@placeholder,'Chave')]"
                    " | //input[@type='text' and ancestor::*[contains(.,'Chave')]]"
                ))
            )
            campo_chave.click()
            campo_chave.send_keys(Keys.CONTROL, "a")
            campo_chave.send_keys(Keys.BACKSPACE)
            campo_chave.send_keys(chave)
            time.sleep(0.5)

            # Botao "Pesquisar" (azul)
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(6)

            # Verifica se deu erro "Nenhuma analise encontrada"
            try:
                msg_erro = self.driver.find_element(By.XPATH,
                    "//*[contains(.,'Nenhuma an') and contains(.,'lise encontrada')]"
                )
                if msg_erro.is_displayed():
                    logger.info(f"SEFAZ: Nenhuma analise encontrada para chave {chave[:20]}...")
                    resultado = ConsultaMDFe(chave=chave, status=StatusMDFe.SEM_ANALISE)
                    return resultado
            except Exception:
                pass

            # Resultado encontrado - clica em "Imprimir Relatorio"
            texto_relatorio = self._imprimir_relatorio()

            if texto_relatorio:
                from modules.parser import parsear_relatorio_sefaz
                resultado = parsear_relatorio_sefaz(texto_relatorio)
                if not resultado.chave:
                    resultado.chave = chave
                return resultado
            else:
                logger.warning("SEFAZ: Nao conseguiu extrair relatorio")
                return ConsultaMDFe(chave=chave, status=StatusMDFe.DESCONHECIDO)

        except Exception as e:
            logger.error(f"SEFAZ: Erro na consulta: {e}")
            return None

    def _imprimir_relatorio(self) -> str:
        """
        Clica no botao "Imprimir Relatorio" e extrai o texto.
        O relatorio pode abrir em nova aba como PDF ou ser baixado.
        """
        try:
            # Botao "Imprimir Relatorio" (azul, ao lado de Pesquisar)
            botao_imprimir = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Imprimir Relat')]"
                    " | //button[contains(.,'Imprimir')]"
                    " | //a[contains(.,'Imprimir Relat')]"
                ))
            )
            botao_imprimir.click()
            time.sleep(8)  # Pode demorar dependendo da qtd de termos

            # Verifica se abriu nova aba (PDF no navegador)
            abas = self.driver.window_handles
            if len(abas) > 2:  # Tem mais abas que as originais
                # Muda para a nova aba
                self.driver.switch_to.window(abas[-1])
                time.sleep(3)

                # Extrai texto
                texto = self.driver.find_element(By.TAG_NAME, "body").text

                # Fecha a aba do relatorio e volta
                self.driver.close()
                self.driver.switch_to.window(self._aba_sefaz)

                if texto and len(texto) > 50:
                    return texto

            # Se nao abriu nova aba, pode ter baixado o PDF
            # Tenta encontrar o PDF baixado
            pasta = config.pasta_downloads
            import os
            if os.path.exists(pasta):
                import glob
                pdfs = glob.glob(os.path.join(pasta, "*.pdf"))
                if pdfs:
                    mais_recente = max(pdfs, key=os.path.getmtime)
                    # Verifica se foi baixado recentemente (ultimos 30s)
                    import time as time_mod
                    if time_mod.time() - os.path.getmtime(mais_recente) < 30:
                        # Le o PDF
                        resultado = parsear_relatorio_pdf(mais_recente)
                        # Retorna o texto bruto para reprocessar
                        with open(mais_recente, 'rb') as f:
                            import pdfplumber
                            with pdfplumber.open(f) as pdf:
                                texto = ""
                                for page in pdf.pages:
                                    texto += (page.extract_text() or "") + "\n"
                                return texto

            # Tenta extrair da pagina atual
            texto = self.driver.find_element(By.TAG_NAME, "body").text
            return texto

        except TimeoutException:
            logger.warning("SEFAZ: Botao Imprimir nao encontrado")
            return ""
        except Exception as e:
            logger.error(f"SEFAZ: Erro ao imprimir relatorio: {e}")
            return ""

    def voltar_para_nexlog(self):
        """Volta para a aba principal do Nexlog."""
        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)

    def fechar_aba(self):
        """Fecha a aba da SEFAZ e volta para o Nexlog."""
        if self._aba_sefaz:
            self.driver.switch_to.window(self._aba_sefaz)
            self.driver.close()
            self._aba_sefaz = None
        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)

    @property
    def logado(self) -> bool:
        return self._logado
