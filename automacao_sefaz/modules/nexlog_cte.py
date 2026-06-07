"""
Modulo para operacoes de CTe no Nexlog:
- Buscar CTe e extrair AWB
- Verificar tipo de entrega (RETIRA vs DOMICILIO)
- Adicionar comentario critico
"""

import time
import logging
from typing import Optional, List

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from models.termo import (
    TermoApreensao,
    CTeProcesado,
    TipoEntrega,
)
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogCTeOperacoes:
    """Operacoes com CTe no Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def consultar_cte(self, numero_cte: str) -> CTeProcesado:
        """
        Consulta um CTe no Nexlog e extrai informacoes.
        Retorna um CTeProcesado com AWB e tipo de entrega.
        """
        cte_proc = CTeProcesado(numero_cte=numero_cte)

        try:
            # Busca o CTe
            self.browser.busca_rapida(numero_cte)
            time.sleep(3)

            # Extrai AWB
            awb = self._extrair_awb()
            cte_proc.awb = awb

            # Verifica tipo de entrega
            tipo = self._verificar_tipo_entrega()
            cte_proc.tipo_entrega = tipo

            logger.info(
                f"CTe {numero_cte}: AWB={awb or 'N/A'} | "
                f"Entrega={tipo.value}"
            )

        except Exception as e:
            logger.error(f"Erro ao consultar CTe {numero_cte}: {e}")

        return cte_proc

    def _extrair_awb(self) -> str:
        """Extrai o numero do AWB da tela de rastreio do CTe."""
        try:
            # Tenta diversas abordagens para encontrar o AWB
            import re

            # Abordagem 1: Buscar em tabelas/campos da tela
            elementos_awb = self.driver.find_elements(
                By.XPATH,
                "//*[contains(text(),'AWB') or contains(text(),'Conhecimento')]"
                "/ancestor::tr//td[2] | "
                "//*[contains(text(),'AWB')]/following-sibling::*"
            )
            for elem in elementos_awb:
                texto = elem.text.strip()
                if texto and texto.isdigit() and len(texto) >= 8:
                    return texto

            # Abordagem 2: Regex no conteudo da pagina
            # AWBs da Gollog comecam com 127
            page_source = self.driver.page_source
            matches = re.findall(r'\b(127\d{7,})\b', page_source)
            if matches:
                # Retorna o primeiro que nao e o proprio CTe
                for m in matches:
                    return m

            # Abordagem 3: Buscar campo especifico
            try:
                campo_awb = self.driver.find_element(
                    By.XPATH,
                    "//label[contains(text(),'AWB') or contains(text(),'Conhecimento')]"
                    "/following::span[1] | "
                    "//label[contains(text(),'AWB')]/following::input[1]"
                )
                valor = campo_awb.text.strip() or campo_awb.get_attribute("value") or ""
                if valor:
                    return valor
            except Exception:
                pass

            return ""

        except Exception as e:
            logger.warning(f"Nao conseguiu extrair AWB: {e}")
            return ""

    def _verificar_tipo_entrega(self) -> TipoEntrega:
        """
        Verifica o tipo de entrega do CTe atual.
        'Teca / Aeroporto' = RETIRA
        'Entrega Domicilio' = DOMICILIO
        """
        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text.upper()

            # Busca especifica pelo campo "Local de entrega"
            if "TECA" in page_text or "AEROPORTO" in page_text:
                return TipoEntrega.RETIRA
            elif "ENTREGA DOMIC" in page_text or "DOMICILIO" in page_text or "DOMICÍLIO" in page_text:
                return TipoEntrega.DOMICILIO

            # Tenta buscar mais especificamente
            try:
                elem_entrega = self.driver.find_element(
                    By.XPATH,
                    "//*[contains(text(),'Local de entrega')]/following::*[1]"
                )
                texto_entrega = elem_entrega.text.upper()
                if "TECA" in texto_entrega or "AEROPORTO" in texto_entrega:
                    return TipoEntrega.RETIRA
                elif "DOMIC" in texto_entrega:
                    return TipoEntrega.DOMICILIO
            except Exception:
                pass

            return TipoEntrega.DESCONHECIDO

        except Exception:
            return TipoEntrega.DESCONHECIDO

    def adicionar_comentario_critico(self, awb: str, texto_comentario: str) -> bool:
        """
        Adiciona um comentario critico em um AWB no Nexlog.

        Fluxo:
        1. Busca o AWB no campo de busca rapida
        2. Clica no botao "Adicionar comentario"
        3. Marca checkbox "Critico"
        4. Digita o texto do comentario
        5. Clica em "Adicionar comentario" (confirmar)

        Returns:
            True se adicionou com sucesso, False caso contrario
        """
        if not awb:
            logger.warning("AWB vazio - nao pode adicionar comentario")
            return False

        try:
            # 1. Busca o AWB
            self.browser.busca_rapida(awb)
            time.sleep(3)

            # 2. Clica no botao "Adicionar comentario"
            botao_comentario = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(text(),'Adicionar coment') or "
                    "contains(text(),'adicionar coment') or "
                    "contains(@title,'coment') or "
                    "contains(@class,'comment')]"
                    " | //a[contains(text(),'Adicionar coment')]"
                ))
            )
            botao_comentario.click()
            time.sleep(2)

            # 3. Marca checkbox "Critico"
            checkbox_critico = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[@type='checkbox'][contains(following-sibling::*,'r') "
                    "or contains(@id,'critic') or contains(@id,'Critic') "
                    "or contains(@name,'critic') or contains(@name,'Critic')]"
                    " | //label[contains(text(),'r')]//input[@type='checkbox']"
                    " | //input[contains(@id,'rit') or contains(@id,'ritico')]"
                ))
            )
            if not checkbox_critico.is_selected():
                checkbox_critico.click()
            time.sleep(0.5)

            # 4. Digita o texto do comentario
            campo_texto = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//textarea | //input[@type='text'][contains(@placeholder,'oment')]"
                ))
            )
            campo_texto.clear()
            campo_texto.send_keys(texto_comentario)
            time.sleep(0.5)

            # 5. Clica em confirmar/adicionar
            botao_confirmar = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(text(),'Adicionar') and contains(@class,'btn')]"
                    " | //button[contains(text(),'Salvar')]"
                    " | //button[contains(text(),'Confirmar')]"
                    " | //button[@type='submit']"
                ))
            )
            botao_confirmar.click()
            time.sleep(2)

            logger.info(f"Comentario adicionado: AWB {awb} -> '{texto_comentario}'")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout ao adicionar comentario no AWB {awb}: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro ao adicionar comentario no AWB {awb}: {e}")
            return False

    def processar_termos(self, termos: List[TermoApreensao]) -> List[CTeProcesado]:
        """
        Processa uma lista de termos:
        - Consulta cada CTe no Nexlog
        - Extrai AWB e tipo de entrega
        - Adiciona comentario critico nos retidos
        - Retorna lista de CTes processados

        Returns:
            Lista de CTeProcesado com resultados
        """
        processados = []

        for termo in termos:
            if not termo.cte:
                logger.warning(f"Termo {termo.numero} sem CTe - pulando")
                continue

            logger.info(f"Processando CTe {termo.cte} (Termo {termo.numero})")

            # Consulta CTe para obter AWB e tipo entrega
            cte_proc = self.consultar_cte(termo.cte)
            cte_proc.termo = termo

            # Se encontrou AWB, adiciona comentario critico
            if cte_proc.awb:
                termo.awb = cte_proc.awb
                sucesso = self.adicionar_comentario_critico(
                    awb=cte_proc.awb,
                    texto_comentario=termo.comentario_nexlog
                )
                cte_proc.comentario_adicionado = sucesso
            else:
                logger.warning(
                    f"CTe {termo.cte}: AWB nao encontrado - "
                    "comentario nao adicionado"
                )

            processados.append(cte_proc)
            time.sleep(1)  # Pausa entre operacoes

        return processados
