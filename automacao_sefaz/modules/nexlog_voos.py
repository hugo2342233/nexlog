"""
Modulo para operacoes com voos no Nexlog:
- Buscar voos por data (Gerenciar rotas)
- Extrair chave do MDF-e (Visualizar integracao MDFe)
- Baixar manifesto do voo (PDF)
"""

import time
import logging
import re
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from models.termo import Voo
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogVoos:
    """Operacoes com voos no Nexlog (tela Gerenciar rotas/recebimento)."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def pesquisar_voos(self, data_inicial: str, data_final: str) -> List[Voo]:
        """
        Pesquisa voos na tela de Gerenciar rotas (Recebimento).

        Args:
            data_inicial: formato DD/MM/YYYY
            data_final: formato DD/MM/YYYY

        Returns:
            Lista de Voo ordenada por hora de chegada
        """
        # Navega para a pagina
        self.browser.navegar_operacoes_gerenciar_rotas()
        time.sleep(2)

        # Preenche data inicial
        campo_data_ini = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//input[contains(@id,'StartDate') or contains(@id,'startDate') "
                "or contains(@name,'StartDate')]"
                " | //input[contains(@placeholder,'Data inicial')]"
            ))
        )
        campo_data_ini.click()
        campo_data_ini.send_keys(Keys.CONTROL, "a")
        campo_data_ini.send_keys(data_inicial)
        campo_data_ini.send_keys(Keys.TAB)
        time.sleep(0.5)

        # Preenche data final
        campo_data_fim = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//input[contains(@id,'EndDate') or contains(@id,'endDate') "
                "or contains(@name,'EndDate')]"
                " | //input[contains(@placeholder,'Data final')]"
            ))
        )
        campo_data_fim.click()
        campo_data_fim.send_keys(Keys.CONTROL, "a")
        campo_data_fim.send_keys(data_final)
        campo_data_fim.send_keys(Keys.TAB)
        time.sleep(0.5)

        # Clica pesquisar
        botao_pesquisar = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[contains(.,'Pesquisar')] | //button[contains(@id,'search')]"
            ))
        )
        botao_pesquisar.click()
        time.sleep(5)

        # Extrai voos da tabela
        voos = self._extrair_voos_tabela()

        # Ordena por hora de chegada
        voos.sort(key=lambda v: v.data_chegada)

        logger.info(f"Encontrados {len(voos)} voos entre {data_inicial} e {data_final}")
        return voos

    def _extrair_voos_tabela(self) -> List[Voo]:
        """Extrai dados dos voos da tabela de resultados."""
        voos = []
        try:
            # Aguarda tabela carregar
            self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//table//tbody//tr"))
            )

            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")

            for i, linha in enumerate(linhas):
                try:
                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if len(colunas) < 6:
                        continue

                    # Mapeia colunas baseado na estrutura observada nos prints
                    # Colunas: checkbox, icones, Numero controle, Etapas, Data chegada, SLA, Assinado, Status, Acoes
                    numero_controle = ""
                    etapas = ""
                    data_chegada = ""
                    assinado = ""
                    status_recebimento = ""

                    for col in colunas:
                        texto = col.text.strip()
                        # Identifica coluna por conteudo
                        if re.match(r'G3\s*\d+', texto):
                            numero_controle = texto
                        elif re.match(r'[A-Z]{3}/[A-Z]{3}', texto):
                            etapas = texto
                        elif re.match(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto):
                            data_chegada = texto
                        elif "vol(s)" in texto.lower() or "kg" in texto.lower():
                            assinado = texto
                        elif texto in ["Fechado", "Aberto", "Em andamento"]:
                            status_recebimento = texto

                    if numero_controle:
                        voo = Voo(
                            numero_controle=numero_controle,
                            etapas=etapas,
                            data_chegada=data_chegada,
                            assinado=assinado,
                            status_recebimento=status_recebimento,
                            indice_tabela=i,
                        )
                        voos.append(voo)

                except Exception as e:
                    logger.debug(f"Erro ao extrair linha {i}: {e}")
                    continue

        except TimeoutException:
            logger.warning("Nenhum voo encontrado na tabela")

        return voos

    def extrair_chave_mdfe(self, voo: Voo) -> str:
        """
        Extrai a chave do MDF-e de um voo.
        Fluxo: Clica em Acoes > Visualizar integracao MDFe > Le a coluna 'Chave'
        """
        try:
            # Clica no botao de acoes (setinha) do voo
            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")
            if voo.indice_tabela >= len(linhas):
                logger.error(f"Indice do voo {voo.numero_controle} fora do range")
                return ""

            linha = linhas[voo.indice_tabela]

            # Procura o botao de acoes na ultima coluna
            botao_acoes = linha.find_element(By.XPATH,
                ".//td[last()]//button | .//td[last()]//a | "
                ".//td[last()]//*[contains(@class,'dropdown') or contains(@class,'action')]"
            )
            botao_acoes.click()
            time.sleep(1)

            # Clica em "Visualizar integracao MDFe"
            opcao_mdfe = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(.,'Visualizar integra') and contains(.,'MDFe')]"
                    " | //a[contains(.,'integra') and contains(.,'MDFe')]"
                ))
            )
            opcao_mdfe.click()
            time.sleep(4)

            # Le a chave da tabela no modal
            chave = self._ler_chave_modal()

            # Fecha o modal
            self._fechar_modal_integracao()

            return chave

        except Exception as e:
            logger.error(f"Erro ao extrair chave MDF-e do voo {voo.numero_controle}: {e}")
            return ""

    def _ler_chave_modal(self) -> str:
        """Le a chave do MDF-e no modal de Integracao MDFe."""
        try:
            # Aguarda modal abrir e tabela carregar
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//div[contains(@class,'modal')]//table//tbody//tr"
                ))
            )
            time.sleep(2)

            # A chave esta na coluna "Chave" da tabela
            # Busca por celula que contenha 44 digitos
            celulas = self.driver.find_elements(By.XPATH,
                "//div[contains(@class,'modal')]//table//tbody//tr//td"
            )
            for celula in celulas:
                texto = celula.text.strip()
                if len(texto) == 44 and texto.isdigit():
                    logger.info(f"Chave MDF-e encontrada: {texto[:20]}...")
                    return texto

            # Alternativa: busca na pagina toda por 44 digitos
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            match = re.search(r'\b(\d{44})\b', page_text)
            if match:
                return match.group(1)

            logger.warning("Chave MDF-e nao encontrada no modal")
            return ""

        except Exception as e:
            logger.error(f"Erro ao ler chave do modal: {e}")
            return ""

    def _fechar_modal_integracao(self):
        """Fecha o modal de Integracao MDFe."""
        try:
            botao_fechar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
                    " | //div[contains(@class,'modal')]//button[contains(@class,'close')]"
                    " | //div[contains(@class,'modal')]//button[@aria-label='Close']"
                ))
            )
            botao_fechar.click()
            time.sleep(1)
        except Exception:
            # Tenta fechar com X
            try:
                self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(@class,'close')]"
                ).click()
            except Exception:
                pass

    def baixar_manifesto(self, voo: Voo) -> str:
        """
        Baixa o PDF do manifesto de despacho do voo.
        Fluxo: Clica nos volumes > Aba Manifestos > Acoes > Imprimir

        Returns:
            Caminho do arquivo PDF baixado, ou "" se falhar
        """
        try:
            # Clica no link de volumes/peso (coluna "Assinado")
            linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")
            if voo.indice_tabela >= len(linhas):
                return ""

            linha = linhas[voo.indice_tabela]

            # Procura link de volumes (texto tipo "58 vol(s), 289,023 kg")
            link_volumes = linha.find_element(By.XPATH,
                ".//a[contains(.,'vol(s)')] | .//td[contains(.,'vol(s)')]//a"
            )
            link_volumes.click()
            time.sleep(3)

            # Clica na aba "Manifestos"
            aba_manifestos = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Manifestos')] | //li//a[text()='Manifestos']"
                ))
            )
            aba_manifestos.click()
            time.sleep(2)

            # Clica no botao de acoes do manifesto
            botao_acoes_manifesto = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//div[contains(@class,'modal')]//table//tbody//tr//td[last()]"
                    "//*[contains(@class,'dropdown') or contains(@class,'action') or self::button]"
                ))
            )
            botao_acoes_manifesto.click()
            time.sleep(1)

            # Clica em "Imprimir"
            opcao_imprimir = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Imprimir')] | //button[contains(.,'Imprimir')]"
                ))
            )
            opcao_imprimir.click()

            # Aguarda download
            caminho = self.browser.aguardar_download(timeout=30)

            # Fecha o modal
            try:
                botao_fechar = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//button[contains(.,'Fechar')]"
                    ))
                )
                botao_fechar.click()
                time.sleep(1)
            except Exception:
                pass

            logger.info(f"Manifesto baixado: {caminho}")
            return caminho

        except Exception as e:
            logger.error(f"Erro ao baixar manifesto do voo {voo.numero_controle}: {e}")
            # Tenta fechar qualquer modal aberto
            self.browser._fechar_modais()
            return ""
