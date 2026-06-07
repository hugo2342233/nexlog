"""
Modulo para automacao do Outlook Web (outlook.cloud.microsoft.com).
- Buscar email pela chave do MDF-e
- Verificar se a SEFAZ respondeu
- Identificar se tem termos ou nao
- Baixar anexo PDF do relatorio (quando necessario)

URL do Outlook: outlook.cloud.microsoft.com ou outlook.office.com
O usuario ja esta logado no navegador (usa perfil do Chrome).
"""

import time
import logging
import os
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from config import config
from models.termo import RespostaEmail
from modules.parser import detectar_resposta_email

logger = logging.getLogger(__name__)


class OutlookWeb:
    """Automacao do Outlook Web para verificar emails da SEFAZ."""

    def __init__(self, driver: webdriver.Chrome):
        """
        Usa o mesmo driver do Nexlog (ja logado no Chrome).
        O Outlook web requer que o usuario esteja logado via navegador.
        """
        self.driver = driver
        self.wait = WebDriverWait(driver, config.timeout_padrao)
        self._aba_outlook = None
        self._aba_original = None

    def abrir_outlook(self):
        """
        Abre o Outlook em uma nova aba do navegador.
        Se nao estiver logado, PAUSA e pede para o usuario fazer login manualmente.
        """
        # Se ja tem aba do Outlook aberta, reutiliza
        if self._aba_outlook and self._aba_outlook in self.driver.window_handles:
            self.driver.switch_to.window(self._aba_outlook)
            return

        self._aba_original = self.driver.current_window_handle

        # Abre nova aba
        self.driver.execute_script("window.open('');")
        time.sleep(1)

        # Muda para a nova aba
        abas = self.driver.window_handles
        self._aba_outlook = abas[-1]
        self.driver.switch_to.window(self._aba_outlook)

        # Navega para o Outlook
        self.driver.get(config.url_outlook)
        time.sleep(5)

        # Verifica se esta logado
        if not self._verificar_logado():
            # NAO esta logado - pausa inteligente
            self._aguardar_login_manual()

    def _verificar_logado(self) -> bool:
        """Verifica se o Outlook esta com sessao ativa."""
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.XPATH,
                    "//input[contains(@aria-label,'Pesquis') or "
                    "contains(@aria-label,'Search') or "
                    "contains(@placeholder,'Pesquis') or "
                    "contains(@placeholder,'Search')]"
                    " | //button[contains(@aria-label,'Nova')]"
                    " | //div[contains(@class,'mailList')]"
                ))
            )
            logger.info("Outlook: Sessao ativa detectada")
            return True
        except TimeoutException:
            return False

    def _aguardar_login_manual(self):
        """
        Pausa inteligente: mostra mensagem e espera o usuario fazer login.
        Fica verificando a cada 5 segundos se o login foi feito.
        Timeout maximo: 5 minutos.
        """
        import tkinter as tk
        from tkinter import messagebox

        logger.warning("Outlook: Sessao nao detectada - aguardando login manual")

        # Mostra popup para o usuario
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        messagebox.showinfo(
            "Login Necessario - Outlook",
            "O Outlook nao esta logado.\n\n"
            "Por favor, faca login na janela do Chrome que abriu\n"
            "e depois clique OK para continuar.\n\n"
            "DICA: Na proxima execucao ele ja vai lembrar o login!",
            parent=root
        )
        root.destroy()

        # Apos o usuario clicar OK, verifica se logou
        tempo_max = 300  # 5 minutos
        tempo_inicio = time.time()

        while time.time() - tempo_inicio < tempo_max:
            if self._verificar_logado():
                logger.info("Outlook: Login manual realizado com sucesso!")
                return

            # Ainda nao logou - espera mais um pouco
            time.sleep(5)

            # Verifica se passou muito tempo
            if time.time() - tempo_inicio > 60:
                # Mostra outro popup
                root2 = tk.Tk()
                root2.withdraw()
                root2.attributes("-topmost", True)

                resposta = messagebox.askretrycancel(
                    "Outlook - Aguardando Login",
                    "Ainda nao detectei o login no Outlook.\n\n"
                    "Ja fez login? Clique 'Repetir' para verificar novamente.\n"
                    "Ou 'Cancelar' para pular a verificacao do email.",
                    parent=root2
                )
                root2.destroy()

                if not resposta:
                    # Cancelou - pula a verificacao
                    logger.warning("Outlook: Login cancelado pelo usuario")
                    raise RuntimeError("Login do Outlook cancelado")

        logger.error("Outlook: Timeout aguardando login manual")
        raise RuntimeError("Timeout aguardando login do Outlook")

    def buscar_por_chave(self, chave_mdfe: str) -> RespostaEmail:
        """
        Busca no Outlook pela chave do MDF-e.
        Verifica se existe resposta da SEFAZ e analisa o conteudo.

        Args:
            chave_mdfe: Chave do MDF-e (44 digitos)

        Returns:
            RespostaEmail indicando se tem termos, nao tem, ou nao respondeu
        """
        try:
            # Muda para aba do Outlook
            if self._aba_outlook:
                self.driver.switch_to.window(self._aba_outlook)

            # Busca pela chave no campo de pesquisa
            campo_busca = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@aria-label,'Pesquis') or "
                    "contains(@aria-label,'Search') or "
                    "contains(@placeholder,'Pesquis') or "
                    "contains(@placeholder,'Search')]"
                ))
            )
            campo_busca.click()
            campo_busca.send_keys(Keys.CONTROL, "a")
            campo_busca.send_keys(Keys.BACKSPACE)
            campo_busca.send_keys(chave_mdfe)
            campo_busca.send_keys(Keys.ENTER)
            time.sleep(5)

            # Verifica se encontrou resposta da SEFAZ
            # Remetente: "PF Central de Transportadoras"
            # Assunto: "Re: MANIFESTO [numero] VOO [numero]"
            try:
                email_resposta = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        "//*[contains(.,'PF Central') or contains(.,'pf central') "
                        "or contains(.,'Re: MANIFESTO') or contains(.,'RE: MANIFESTO')]"
                        "[contains(@class,'item') or contains(@role,'option') "
                        "or ancestor::*[contains(@class,'listMessage')]]"
                    ))
                )
            except TimeoutException:
                logger.info(f"Outlook: Nenhuma resposta encontrada para chave {chave_mdfe[:20]}...")
                return RespostaEmail.NAO_RESPONDEU

            # Encontrou resposta - clica nela para abrir
            email_resposta.click()
            time.sleep(4)

            # Extrai texto do corpo do email
            corpo_email = self._extrair_corpo_email()

            if not corpo_email:
                logger.warning("Outlook: Nao conseguiu extrair corpo do email")
                return RespostaEmail.INDEFINIDO

            # Analisa o conteudo
            resultado = detectar_resposta_email(corpo_email)

            if resultado == "sem_termos":
                logger.info("Outlook: Email indica SEM termos - voo liberado!")
                return RespostaEmail.SEM_TERMOS
            elif resultado == "com_termos":
                logger.info("Outlook: Email indica COM termos - precisa consultar SEFAZ")
                return RespostaEmail.COM_TERMOS
            else:
                logger.info("Outlook: Nao conseguiu determinar - tratando como COM termos (seguro)")
                return RespostaEmail.COM_TERMOS  # Fallback seguro

        except Exception as e:
            logger.error(f"Outlook: Erro ao buscar email: {e}")
            return RespostaEmail.INDEFINIDO

    def _extrair_corpo_email(self) -> str:
        """Extrai o texto do corpo do email aberto."""
        try:
            # Tenta diversos seletores para o corpo do email
            seletores_corpo = [
                "//div[contains(@class,'ReadingPane')]",
                "//div[contains(@role,'document')]",
                "//div[contains(@class,'ItemBody')]",
                "//div[contains(@class,'messageBody')]",
                "//div[contains(@aria-label,'Corpo')]",
                "//div[contains(@aria-label,'Message body')]",
            ]

            for xpath in seletores_corpo:
                try:
                    elemento = self.driver.find_element(By.XPATH, xpath)
                    texto = elemento.text
                    if texto and len(texto) > 20:
                        return texto
                except Exception:
                    continue

            return ""

        except Exception:
            return ""

    def baixar_anexo_pdf(self) -> str:
        """
        Baixa o anexo PDF do email de resposta da SEFAZ.
        O anexo geralmente se chama "MDF-E [numero].pdf"

        Returns:
            Caminho do arquivo baixado, ou "" se falhar
        """
        try:
            # Procura o anexo (icone/link de download)
            anexo = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(.,'MDF') and contains(.,'pdf')]"
                    "[contains(@class,'attachment') or contains(@role,'button') "
                    "or self::a or self::button]"
                    " | //div[contains(@class,'attachment')]//a"
                    " | //*[contains(@class,'AttachmentCard')]"
                ))
            )
            anexo.click()
            time.sleep(2)

            # Tenta clicar em "Baixar" se aparecer menu
            try:
                botao_baixar = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//button[contains(.,'Baixar') or contains(.,'Download')]"
                        " | //a[contains(.,'Baixar') or contains(.,'Download')]"
                    ))
                )
                botao_baixar.click()
            except TimeoutException:
                # Pode ja ter iniciado o download direto
                pass

            # Aguarda download
            time.sleep(5)

            # Procura o arquivo baixado
            pasta = config.pasta_downloads
            if os.path.exists(pasta):
                arquivos = sorted(
                    [f for f in os.listdir(pasta) if f.endswith('.pdf') and 'MDF' in f.upper()],
                    key=lambda f: os.path.getmtime(os.path.join(pasta, f)),
                    reverse=True
                )
                if arquivos:
                    caminho = os.path.join(pasta, arquivos[0])
                    logger.info(f"Outlook: Anexo baixado: {arquivos[0]}")
                    return caminho

            logger.warning("Outlook: Nao encontrou anexo baixado")
            return ""

        except Exception as e:
            logger.error(f"Outlook: Erro ao baixar anexo: {e}")
            return ""

    def voltar_para_nexlog(self):
        """Volta para a aba principal do Nexlog."""
        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)

    def fechar_aba(self):
        """Fecha a aba do Outlook e volta para o Nexlog."""
        if self._aba_outlook:
            self.driver.switch_to.window(self._aba_outlook)
            self.driver.close()
            self._aba_outlook = None

        if self._aba_original:
            self.driver.switch_to.window(self._aba_original)
