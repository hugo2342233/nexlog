"""
Modulo para automacao do Outlook Web (outlook.office.com).
- Checar se a SEFAZ respondeu ao email do manifesto
- Enviar DAMDFE para SEFAZ (futuro)

Usa Selenium para interagir com o Outlook no navegador.
"""

import time
import logging
from typing import Optional, List

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from config import config

logger = logging.getLogger(__name__)


class OutlookWeb:
    """Automacao do Outlook Web (Office 365)."""

    def __init__(self, driver: webdriver.Chrome = None):
        """
        Pode usar um driver existente ou criar um novo.
        """
        self._driver_proprio = driver is None
        self.driver = driver
        self.wait = None
        self._logado = False

    def iniciar(self, headless: bool = False):
        """Inicia navegador para Outlook."""
        if self.driver is None:
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("--headless")
            options.add_argument("--start-maximized")
            # Manter sessao/perfil para evitar login repetido
            options.add_argument("--user-data-dir=outlook_profile")
            self.driver = webdriver.Chrome(options=options)

        self.wait = WebDriverWait(self.driver, config.timeout_longo)
        logger.info("Outlook: Navegador pronto")

    def abrir_outlook(self):
        """
        Abre o Outlook Web.
        NOTA: O usuario pode precisar fazer login manual na primeira vez.
        O perfil do Chrome mantera a sessao para proximas execucoes.
        """
        self.driver.get(config.url_outlook)
        time.sleep(5)

        # Verifica se ja esta logado
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//div[contains(@role,'main')] | "
                    "//div[contains(@class,'mail')] | "
                    "//button[contains(@aria-label,'Nova')]"
                ))
            )
            self._logado = True
            logger.info("Outlook: Sessao ativa detectada")
        except TimeoutException:
            logger.warning(
                "Outlook: Nao detectou sessao ativa. "
                "O usuario pode precisar fazer login manual."
            )
            self._logado = False

    def buscar_email_sefaz(self, assunto_busca: str = "SEFAZ") -> bool:
        """
        Busca emails da SEFAZ na caixa de entrada.

        Args:
            assunto_busca: Termo para buscar nos emails

        Returns:
            True se encontrou emails, False caso contrario
        """
        try:
            # Campo de busca do Outlook
            campo_busca = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[contains(@aria-label,'Pesquis') or "
                    "contains(@placeholder,'Pesquis') or "
                    "contains(@aria-label,'Search') or "
                    "contains(@placeholder,'Search')]"
                ))
            )
            campo_busca.click()
            campo_busca.clear()
            campo_busca.send_keys(assunto_busca)
            campo_busca.send_keys(Keys.ENTER)

            time.sleep(4)

            # Verifica se encontrou resultados
            resultados = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'listMessage') or contains(@role,'option')]"
            )
            encontrou = len(resultados) > 0
            logger.info(f"Outlook: Busca '{assunto_busca}' - {len(resultados)} resultados")
            return encontrou

        except TimeoutException:
            logger.warning("Outlook: Campo de busca nao encontrado")
            return False

    def verificar_resposta_manifesto(self, numero_manifesto: str) -> Optional[str]:
        """
        Verifica se a SEFAZ respondeu ao email de um manifesto especifico.

        Args:
            numero_manifesto: Numero do MDF-e para buscar

        Returns:
            Texto do email de resposta, ou None se nao encontrou
        """
        try:
            # Busca pelo numero do manifesto
            busca_ok = self.buscar_email_sefaz(f"MDF {numero_manifesto}")
            if not busca_ok:
                busca_ok = self.buscar_email_sefaz(numero_manifesto)

            if not busca_ok:
                logger.info(f"Outlook: Nenhuma resposta para manifesto {numero_manifesto}")
                return None

            # Clica no primeiro resultado
            primeiro_email = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "(//div[contains(@class,'listMessage') or "
                    "contains(@role,'option')])[1]"
                ))
            )
            primeiro_email.click()
            time.sleep(3)

            # Extrai corpo do email
            corpo = self.driver.find_element(
                By.XPATH,
                "//div[contains(@class,'readingPane') or "
                "contains(@role,'document') or "
                "contains(@class,'ItemBody')]"
            )
            texto_email = corpo.text
            logger.info(f"Outlook: Resposta encontrada para manifesto {numero_manifesto}")
            return texto_email

        except TimeoutException:
            logger.warning(f"Outlook: Timeout ao verificar manifesto {numero_manifesto}")
            return None
        except Exception as e:
            logger.error(f"Outlook: Erro ao verificar manifesto: {e}")
            return None

    def listar_emails_nao_lidos_sefaz(self) -> List[dict]:
        """
        Lista emails nao lidos da SEFAZ.

        Returns:
            Lista de dicts com 'assunto', 'data', 'remetente'
        """
        emails = []
        try:
            self.buscar_email_sefaz("SEFAZ is:unread")
            time.sleep(3)

            itens = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'listMessage')]"
            )

            for item in itens[:20]:  # Limita a 20
                try:
                    assunto_elem = item.find_element(
                        By.XPATH, ".//span[contains(@class,'subject')]"
                    )
                    emails.append({
                        "assunto": assunto_elem.text,
                        "elemento": item,
                    })
                except Exception:
                    continue

            logger.info(f"Outlook: {len(emails)} emails nao lidos da SEFAZ")

        except Exception as e:
            logger.error(f"Outlook: Erro ao listar emails: {e}")

        return emails

    def fechar(self):
        """Fecha o navegador se for proprio."""
        if self._driver_proprio and self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            logger.info("Outlook: Navegador fechado")
