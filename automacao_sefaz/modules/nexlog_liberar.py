"""
Modulo para liberar AWBs na tela de Retencao do Nexlog.
Regras:
- So libera AWBs da secao RETIRA do manifesto
- Nao libera AWBs que tem termos de apreensao
- Nao libera AWBs com status "Liberada" ou "Parcialmente liberada"
- Se o AWB nao aparece na busca = ja foi liberado/retirado -> pula
"""

import time
import logging
from typing import List, Set, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogLiberar:
    """Libera AWBs na tela de Retencao do Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def configurar_filtros(self, data_inicial: str, data_final: str):
        """
        Configura os filtros da tela de Retencoes.
        Os filtros ja vem com a base MCZ preenchida.
        Ajusta as datas e clica em Pesquisar.
        """
        try:
            # Ajusta data inicial (formato DD/MM/YYYY HH:MM)
            campo_data_ini = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@id,'StartDate') or contains(@id,'startDate') "
                    "or contains(@name,'StartDate')]"
                    " | //label[contains(.,'Data inicial')]//following::input[1]"
                ))
            )
            campo_data_ini.click()
            campo_data_ini.send_keys(Keys.CONTROL, "a")
            # Formato com hora: DD/MM/YYYY 00:00
            campo_data_ini.send_keys(f"{data_inicial} 00:00")
            campo_data_ini.send_keys(Keys.TAB)
            time.sleep(0.5)

            # Ajusta data final
            campo_data_fim = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@id,'EndDate') or contains(@id,'endDate') "
                    "or contains(@name,'EndDate')]"
                    " | //label[contains(.,'Data final')]//following::input[1]"
                ))
            )
            campo_data_fim.click()
            campo_data_fim.send_keys(Keys.CONTROL, "a")
            campo_data_fim.send_keys(f"{data_final} 23:59")
            campo_data_fim.send_keys(Keys.TAB)
            time.sleep(0.5)

            # Clica Pesquisar
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(5)

            logger.info(f"Filtros configurados: {data_inicial} ate {data_final}")

        except Exception as e:
            logger.error(f"Erro ao configurar filtros de retencao: {e}")
            raise

    def selecionar_awbs_para_liberacao(self, awbs: Set[str]) -> Dict[str, str]:
        """
        Para cada AWB na lista, pesquisa na tabela e marca o checkbox
        APENAS se o status for "Retida".

        Args:
            awbs: Set de AWBs para tentar liberar

        Returns:
            Dict com resultado: {'liberados': [...], 'ja_liberados': [...], 'erros': [...]}
        """
        resultado = {
            "selecionados": [],
            "ja_liberados": [],
            "erros": [],
        }

        # Campo de pesquisa da tabela
        xpath_pesquisar = (
            "//input[contains(@class,'search') or contains(@type,'search')]"
            " | //div[contains(@class,'filter')]//input"
            " | //label[contains(.,'Pesquisar')]//input"
            " | //input[contains(@placeholder,'Pesquisar') or contains(@aria-label,'Pesquisar')]"
        )

        for awb in awbs:
            try:
                # Pesquisa o AWB no campo de filtro da tabela
                campo = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_pesquisar))
                )
                campo.click()
                campo.send_keys(Keys.CONTROL, "a")
                campo.send_keys(Keys.BACKSPACE)
                campo.send_keys(awb)
                time.sleep(2)

                # Verifica se encontrou resultados
                try:
                    sem_registro = self.driver.find_element(By.XPATH,
                        "//*[contains(.,'Nenhum registro') or contains(.,'0 de 0')]"
                    )
                    if sem_registro.is_displayed():
                        # AWB nao encontrado = ja liberado/retirado
                        resultado["ja_liberados"].append(awb)
                        logger.info(f"AWB {awb}: nao encontrado (ja liberado/retirado)")
                        continue
                except NoSuchElementException:
                    pass

                # Encontrou resultados - verifica status e marca checkboxes
                linhas = self.driver.find_elements(By.XPATH, "//table//tbody//tr")

                for linha in linhas:
                    try:
                        texto_linha = linha.text.upper()

                        # Verifica o status
                        if "RETIDA" in texto_linha and "PARCIALMENTE" not in texto_linha:
                            # Status "Retida" - MARCA o checkbox
                            checkbox = linha.find_element(By.XPATH,
                                ".//input[@type='checkbox']"
                            )
                            if not checkbox.is_selected():
                                try:
                                    checkbox.click()
                                except Exception:
                                    self.driver.execute_script(
                                        "arguments[0].click();", checkbox
                                    )
                                time.sleep(0.5)

                        elif "LIBERADA" in texto_linha or "PARCIALMENTE" in texto_linha:
                            # Ja liberada ou parcialmente - NAO marca
                            logger.debug(f"AWB {awb}: linha com status liberada/parcial - pulando")

                    except Exception as e:
                        logger.debug(f"Erro ao processar linha para AWB {awb}: {e}")

                resultado["selecionados"].append(awb)
                logger.info(f"AWB {awb}: selecionado para liberacao")

            except TimeoutException:
                resultado["erros"].append(awb)
                logger.error(f"AWB {awb}: timeout na busca")
            except Exception as e:
                resultado["erros"].append(awb)
                logger.error(f"AWB {awb}: erro - {e}")

        return resultado

    def confirmar_liberacao(self) -> bool:
        """
        Apos selecionar todos os AWBs, clica em "Liberar documento"
        e confirma na tela de liberacao.
        Fluxo:
        1. Clica botao "Liberar documento"
        2. Abre modal "Liberar retencao" com lista dos selecionados
        3. Campo Protocolo = vazio
        4. Campo Observacao = vazio
        5. Clica botao "Liberado" (azul com checkmark)
        """
        try:
            # Verifica se tem alguma selecao
            texto_selecionados = ""
            try:
                elem_selecionados = self.driver.find_element(By.XPATH,
                    "//*[contains(.,'selecionado')]"
                )
                texto_selecionados = elem_selecionados.text
            except Exception:
                pass

            if "0 selecionado" in texto_selecionados:
                logger.warning("Nenhum AWB selecionado para liberacao")
                return False

            # 1. Clica "Liberar documento"
            botao_liberar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Liberar documento')]"
                    " | //a[contains(.,'Liberar documento')]"
                ))
            )
            botao_liberar.click()
            time.sleep(4)

            # 2. Aguarda modal "Liberar retencao" abrir
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(.,'Liberar reten') and "
                    "(self::h4 or self::h3 or self::h5 or self::div)]"
                ))
            )
            time.sleep(2)

            # 3. Protocolo e Observacao ficam vazios (nao preenche nada)

            # 4. Clica botao "Liberado" (azul)
            botao_liberado = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Liberado')]"
                    " | //button[contains(@class,'btn-primary') and contains(.,'Libera')]"
                    " | //button[contains(@class,'btn-info') and contains(.,'Libera')]"
                ))
            )
            botao_liberado.click()
            time.sleep(5)

            logger.info("Liberacao confirmada com sucesso!")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout ao confirmar liberacao: {e}")
            return False
        except Exception as e:
            logger.error(f"Erro ao confirmar liberacao: {e}")
            return False

    def liberar_awbs(self, awbs: Set[str], data_inicial: str, data_final: str) -> Dict:
        """
        Fluxo completo de liberacao:
        1. Navega para tela de Retencoes
        2. Configura filtros (datas)
        3. Para cada AWB: pesquisa, verifica status, marca checkbox
        4. Clica "Liberar documento"
        5. Confirma liberacao

        Returns:
            Dict com resultado detalhado
        """
        if not awbs:
            logger.info("Nenhum AWB para liberar")
            return {"selecionados": [], "ja_liberados": [], "erros": [], "liberacao_ok": False}

        # 1. Navega
        self.browser.navegar_vendas_retencao_lista()
        time.sleep(2)

        # 2. Configura filtros
        self.configurar_filtros(data_inicial, data_final)

        # 3. Seleciona AWBs
        resultado = self.selecionar_awbs_para_liberacao(awbs)

        # 4 e 5. Confirma liberacao (se tem algum selecionado)
        if resultado["selecionados"]:
            liberacao_ok = self.confirmar_liberacao()
            resultado["liberacao_ok"] = liberacao_ok
        else:
            resultado["liberacao_ok"] = False
            logger.info("Nenhum AWB novo para liberar (todos ja liberados ou com erro)")

        return resultado
