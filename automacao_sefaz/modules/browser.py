"""
Modulo de login compartilhado no Nexlog.
Gerencia sessao do navegador de forma reutilizavel por todos os modulos.
Usa Selenium (compativel com o setup atual do usuario).
"""

import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains

from config import config

logger = logging.getLogger(__name__)


class NexlogBrowser:
    """Gerencia a sessao do navegador no Nexlog."""

    def __init__(self):
        self.driver = None
        self.wait = None
        self.wait_curto = None
        self._logado = False

    def iniciar(self, headless: bool = False):
        """Inicia o navegador Chrome."""
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")

        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, config.timeout_padrao)
        self.wait_curto = WebDriverWait(self.driver, config.timeout_curto)
        logger.info("Navegador iniciado")

    def login_nexlog(self, usuario: str = None, senha: str = None, base: str = None):
        """Faz login no Nexlog. Usa credenciais do config se nao fornecidas."""
        usuario = usuario or config.nexlog.usuario
        senha = senha or config.nexlog.senha
        base = base or config.nexlog.base

        if not usuario or not senha:
            raise ValueError("Credenciais do Nexlog nao configuradas.")

        self.driver.get(config.url_nexlog)
        time.sleep(3)

        # Usuario
        campo_user = self.wait.until(
            EC.element_to_be_clickable((By.ID, "user"))
        )
        campo_user.clear()
        campo_user.send_keys(usuario)

        # Senha
        campo_senha = self.wait.until(
            EC.element_to_be_clickable((By.ID, "password"))
        )
        campo_senha.clear()
        campo_senha.send_keys(senha)

        # Base/Franquia
        campo_base = self.wait.until(
            EC.element_to_be_clickable((By.ID, "franchise"))
        )
        campo_base.clear()
        campo_base.send_keys(base)

        # Botao login
        self.driver.find_element(
            By.XPATH, "//*[@id='div-btns']/button"
        ).click()

        # Tratar mensagem de sessao ativa
        self._tratar_sessao_ativa()

        time.sleep(3)
        self._logado = True
        logger.info(f"Login Nexlog realizado - base: {base}")

    def _tratar_sessao_ativa(self):
        """Clica em 'Continuar' se aparecer mensagem de sessao ativa."""
        try:
            botao = WebDriverWait(self.driver, 4).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "/html/body/div/div/div/button[2]")
                )
            )
            botao.click()
            logger.info("Sessao ativa detectada - continuando")
        except TimeoutException:
            pass

    def busca_rapida(self, codigo: str):
        """Usa o campo de busca rapida para pesquisar um codigo (CTe, AWB, etc)."""
        self._fechar_modais()
        time.sleep(1)

        campo = self.wait.until(
            EC.visibility_of_element_located((By.ID, "quickSearch"))
        )
        campo.click()
        campo.clear()
        time.sleep(0.3)
        campo.send_keys(codigo)

        botao = self.wait.until(
            EC.element_to_be_clickable((By.ID, "quickTracking-icon"))
        )
        botao.click()
        time.sleep(3)

    def _fechar_modais(self):
        """Fecha qualquer modal aberto que possa bloquear a interacao."""
        try:
            # ESC para fechar modais
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)

            # Remove backdrops via JavaScript
            self.driver.execute_script("""
                var backdrops = document.getElementsByClassName('modal-backdrop');
                while(backdrops.length > 0){ backdrops[0].parentNode.removeChild(backdrops[0]); }
                document.body.classList.remove('modal-open');
                var modals = document.getElementsByClassName('modal');
                for(var i=0; i<modals.length; i++) { modals[i].style.display = 'none'; }
            """)
        except Exception:
            pass

    def obter_tipo_entrega(self) -> str:
        """
        Apos buscar um CTe, verifica o tipo de entrega na tela.
        Retorna 'retira' ou 'domicilio' ou 'desconhecido'.
        """
        try:
            # Busca o texto 'Local de entrega' na pagina
            page_text = self.driver.page_source.upper()

            if "TECA" in page_text or "AEROPORTO" in page_text:
                return "retira"
            elif "ENTREGA DOMIC" in page_text:
                return "domicilio"
            else:
                return "desconhecido"
        except Exception:
            return "desconhecido"

    def obter_awb_do_cte(self) -> str:
        """
        Apos buscar um CTe, extrai o numero do AWB atrelado.
        Retorna o numero do AWB ou string vazia se nao encontrar.
        """
        try:
            # Tenta encontrar o campo AWB na tela de rastreio
            # O AWB geralmente aparece na secao de detalhes do CTe
            elementos = self.driver.find_elements(
                By.XPATH,
                "//td[contains(text(),'AWB') or contains(text(),'awb')]"
                "/following-sibling::td"
            )
            if elementos:
                return elementos[0].text.strip()

            # Alternativa: buscar por padrao numerico de AWB (ex: 127XXXXXXX)
            import re
            page_text = self.driver.page_source
            match = re.search(r'\b(127\d{7,})\b', page_text)
            if match:
                return match.group(1)

            return ""
        except Exception:
            return ""

    def fechar(self):
        """Fecha o navegador."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._logado = False
            logger.info("Navegador fechado")

    @property
    def logado(self) -> bool:
        return self._logado

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, *args):
        self.fechar()
