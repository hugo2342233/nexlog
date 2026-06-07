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
        O anexo geralmente se chama "MDF-E [numero].pdf" ou similar.

        Estrategia:
        1. Tenta encontrar o card/link do anexo no painel de leitura
        2. Clica nele para abrir opcoes
        3. Clica em "Baixar" / "Download"
        4. Se nao achar botao download, tenta via menu contextual (3 pontos)
        5. Verifica pasta de downloads para confirmar

        Returns:
            Caminho do arquivo baixado, ou "" se falhar
        """
        try:
            # Registra arquivos existentes antes do download
            pasta = config.pasta_downloads
            os.makedirs(pasta, exist_ok=True)
            arquivos_antes = set(os.listdir(pasta)) if os.path.exists(pasta) else set()

            # --- ESTRATEGIA 1: Encontrar o card do anexo ---
            anexo_encontrado = self._encontrar_anexo()

            if not anexo_encontrado:
                logger.warning("Outlook: Anexo PDF nao encontrado no email")
                return ""

            # --- ESTRATEGIA 2: Baixar via clique direto ou menu ---
            download_iniciado = self._iniciar_download_anexo(anexo_encontrado)

            if not download_iniciado:
                # Tenta abordagem alternativa: botao "Baixar tudo"
                download_iniciado = self._baixar_todos_anexos()

            if not download_iniciado:
                logger.warning("Outlook: Nao conseguiu iniciar download do anexo")
                return ""

            # --- ESTRATEGIA 3: Aguardar e encontrar o arquivo baixado ---
            caminho = self._aguardar_download_pdf(arquivos_antes, timeout=30)

            if caminho:
                logger.info(f"Outlook: Anexo baixado com sucesso: {os.path.basename(caminho)}")
            else:
                logger.warning("Outlook: Download aparentemente nao completou")

            return caminho

        except Exception as e:
            logger.error(f"Outlook: Erro ao baixar anexo: {e}")
            return ""

    def _encontrar_anexo(self):
        """
        Encontra o elemento do anexo PDF no email aberto.
        Tenta multiplas estrategias para diferentes versoes do Outlook Web.
        """
        estrategias = [
            # 1. Card de anexo com texto PDF ou MDF (Outlook moderno)
            "//div[contains(@class,'attachment') or contains(@class,'Attachment')]"
            "[contains(.,'pdf') or contains(.,'PDF') or contains(.,'MDF') or contains(.,'mdf')]",

            # 2. Elemento com aria-label contendo PDF
            "//*[contains(@aria-label,'pdf') or contains(@aria-label,'PDF') "
            "or contains(@aria-label,'MDF')]"
            "[contains(@class,'attachment') or contains(@role,'button') or contains(@role,'listitem')]",

            # 3. Link com .pdf na URL ou texto
            "//a[contains(@href,'.pdf') or contains(.,'pdf') or contains(.,'PDF')]"
            "[ancestor::*[contains(@class,'attachment') or contains(@class,'ReadingPane') "
            "or contains(@class,'ItemBody')]]",

            # 4. Botao/div com icone de arquivo (generico Outlook)
            "//div[contains(@class,'AttachmentCard') or contains(@class,'attachmentCard')]",

            # 5. Qualquer elemento clicavel com "pdf" no texto dentro da area de leitura
            "//div[contains(@class,'ReadingPane') or contains(@class,'reading')]"
            "//*[contains(.,'pdf') or contains(.,'PDF')]"
            "[self::a or self::button or self::div[contains(@class,'attachment') "
            "or contains(@role,'button')]]",

            # 6. Span ou div com nome do arquivo (.pdf)
            "//*[contains(text(),'.pdf') or contains(text(),'.PDF')]"
            "[ancestor::*[contains(@class,'attachment') or contains(@class,'Attachment')]]",

            # 7. Fallback: qualquer coisa com class attachment
            "//*[contains(@class,'ttachment')]"
            "[.//span[contains(text(),'pdf') or contains(text(),'PDF') "
            "or contains(text(),'MDF') or contains(text(),'Relat')]]",
        ]

        for xpath in estrategias:
            try:
                elementos = self.driver.find_elements(By.XPATH, xpath)
                visiveis = [e for e in elementos if e.is_displayed()]
                if visiveis:
                    logger.debug(f"Outlook: Anexo encontrado via: {xpath[:60]}...")
                    return visiveis[0]
            except Exception:
                continue

        # Ultima tentativa: busca qualquer coisa com "pdf" visivel na area de leitura
        try:
            page_source = self.driver.page_source.lower()
            if '.pdf' in page_source:
                logger.debug("Outlook: '.pdf' encontrado no HTML mas nenhum elemento clicavel")
        except Exception:
            pass

        return None

    def _iniciar_download_anexo(self, elemento_anexo) -> bool:
        """
        Tenta iniciar o download do anexo encontrado.
        Tenta: clique direto -> menu 3 pontos -> download.
        """
        try:
            # Tenta clique direto no anexo (pode abrir preview ou baixar)
            try:
                elemento_anexo.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", elemento_anexo)
            time.sleep(2)

            # Verifica se abriu um menu/popup com opcao de download
            botao_download = self._encontrar_botao_download()
            if botao_download:
                try:
                    botao_download.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", botao_download)
                time.sleep(3)
                return True

            # Se nao achou botao download, pode ter aberto preview
            # Tenta encontrar botao download no preview/viewer
            botao_download_preview = self._encontrar_botao_download_preview()
            if botao_download_preview:
                try:
                    botao_download_preview.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", botao_download_preview)
                time.sleep(3)
                return True

            # Tenta via menu contextual (3 pontos / mais opcoes)
            return self._download_via_menu_contextual(elemento_anexo)

        except Exception as e:
            logger.debug(f"Outlook: Erro ao iniciar download: {e}")
            return False

    def _encontrar_botao_download(self):
        """Encontra botao de download no popup/menu do anexo."""
        xpaths = [
            "//button[contains(.,'Baixar') or contains(.,'Download') or contains(.,'baixar')]",
            "//a[contains(.,'Baixar') or contains(.,'Download') or contains(.,'baixar')]",
            "//button[contains(@aria-label,'Baixar') or contains(@aria-label,'Download')]",
            "//*[contains(@class,'download') or contains(@class,'Download')]"
            "[self::button or self::a]",
            "//button[.//i[contains(@class,'download') or contains(@class,'Download')]]",
            "//span[contains(.,'Baixar') or contains(.,'Download')]/ancestor::button",
        ]

        for xpath in xpaths:
            try:
                elementos = self.driver.find_elements(By.XPATH, xpath)
                visiveis = [e for e in elementos if e.is_displayed() and e.is_enabled()]
                if visiveis:
                    logger.debug(f"Outlook: Botao download encontrado via: {xpath[:50]}")
                    return visiveis[0]
            except Exception:
                continue
        return None

    def _encontrar_botao_download_preview(self):
        """Encontra botao download dentro de um viewer/preview de PDF."""
        xpaths = [
            # Barra de ferramentas do viewer
            "//div[contains(@class,'viewer') or contains(@class,'preview')]"
            "//button[contains(@aria-label,'Baixar') or contains(@aria-label,'Download')]",
            # Icone de download generico
            "//button[contains(@class,'download') or @data-icon-name='Download']",
            # Toolbar de documento
            "//*[contains(@class,'CommandBar') or contains(@class,'toolbar')]"
            "//button[contains(@aria-label,'ownload') or contains(.,'ownload')]",
        ]

        for xpath in xpaths:
            try:
                elementos = self.driver.find_elements(By.XPATH, xpath)
                visiveis = [e for e in elementos if e.is_displayed()]
                if visiveis:
                    return visiveis[0]
            except Exception:
                continue
        return None

    def _download_via_menu_contextual(self, elemento_anexo) -> bool:
        """Tenta baixar via botao de 3 pontos (mais opcoes) no card do anexo."""
        try:
            # Hover no anexo para mostrar botoes
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).move_to_element(elemento_anexo).perform()
            time.sleep(1)

            # Busca botao de mais opcoes (3 pontos / chevron)
            botao_mais = None
            xpaths_mais = [
                ".//button[contains(@aria-label,'Mais') or contains(@aria-label,'More') "
                "or contains(@aria-label,'Op')]",
                ".//button[contains(@class,'more') or contains(@class,'More')]",
                ".//i[contains(@class,'MoreVertical') or contains(@class,'ChevronDown')]/..",
                "//button[contains(@aria-label,'a') and contains(@class,'action')]",
            ]

            # Primeiro tenta dentro do elemento do anexo
            for xpath in xpaths_mais:
                try:
                    btns = elemento_anexo.find_elements(By.XPATH, xpath)
                    visiveis = [b for b in btns if b.is_displayed()]
                    if visiveis:
                        botao_mais = visiveis[0]
                        break
                except Exception:
                    continue

            if not botao_mais:
                # Tenta na area proxima ao anexo
                for xpath in xpaths_mais:
                    try:
                        btns = self.driver.find_elements(By.XPATH, xpath)
                        visiveis = [b for b in btns if b.is_displayed()]
                        if visiveis:
                            botao_mais = visiveis[-1]  # Ultimo visivel (provavelmente o do anexo)
                            break
                    except Exception:
                        continue

            if botao_mais:
                botao_mais.click()
                time.sleep(1)

                # Agora busca "Baixar" no menu que abriu
                botao_download = self._encontrar_botao_download()
                if botao_download:
                    botao_download.click()
                    time.sleep(3)
                    return True

            return False

        except Exception as e:
            logger.debug(f"Outlook: Erro no menu contextual: {e}")
            return False

    def _baixar_todos_anexos(self) -> bool:
        """Tenta usar botao 'Baixar todos' / 'Download all' se disponivel."""
        try:
            xpaths = [
                "//button[contains(.,'Baixar tudo') or contains(.,'Download all')]",
                "//a[contains(.,'Baixar tudo') or contains(.,'Download all')]",
                "//button[contains(@aria-label,'Baixar tudo') or contains(@aria-label,'Download all')]",
            ]

            for xpath in xpaths:
                try:
                    elementos = self.driver.find_elements(By.XPATH, xpath)
                    visiveis = [e for e in elementos if e.is_displayed()]
                    if visiveis:
                        visiveis[0].click()
                        time.sleep(5)
                        logger.info("Outlook: Clicou em 'Baixar tudo'")
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _aguardar_download_pdf(self, arquivos_antes: set, timeout: int = 30) -> str:
        """
        Aguarda um novo arquivo PDF aparecer na pasta de downloads.
        Ignora arquivos .crdownload e .tmp (downloads em progresso).
        """
        pasta = config.pasta_downloads
        tempo_inicio = time.time()

        while time.time() - tempo_inicio < timeout:
            time.sleep(2)

            if not os.path.exists(pasta):
                continue

            arquivos_atuais = set(os.listdir(pasta))
            novos = arquivos_atuais - arquivos_antes

            # Filtra: apenas PDFs completos (sem .crdownload, .tmp)
            pdfs_novos = [
                f for f in novos
                if f.lower().endswith('.pdf')
                and not f.endswith('.crdownload')
                and not f.endswith('.tmp')
            ]

            if pdfs_novos:
                # Retorna o mais recente
                arquivo = max(
                    pdfs_novos,
                    key=lambda f: os.path.getmtime(os.path.join(pasta, f))
                )
                caminho = os.path.join(pasta, arquivo)

                # Verifica se o arquivo tem conteudo (nao esta vazio)
                if os.path.getsize(caminho) > 100:
                    return caminho

        # Timeout - tenta buscar qualquer PDF recente na pasta
        try:
            todos_pdfs = [
                f for f in os.listdir(pasta)
                if f.lower().endswith('.pdf')
            ]
            if todos_pdfs:
                mais_recente = max(
                    todos_pdfs,
                    key=lambda f: os.path.getmtime(os.path.join(pasta, f))
                )
                caminho = os.path.join(pasta, mais_recente)
                # Se foi modificado nos ultimos 60 segundos, provavelmente e o nosso
                if time.time() - os.path.getmtime(caminho) < 60:
                    logger.info(f"Outlook: PDF recente encontrado (fallback): {mais_recente}")
                    return caminho
        except Exception:
            pass

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
