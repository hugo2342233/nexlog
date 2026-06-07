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
        Baseado no script original (Liberar_retencao.py):
        1. Ajusta data inicial para dia 01
        2. Seleciona status "Retida" no select2
        3. Clica Pesquisar
        """
        try:
            # Ajusta data inicial — usa o campo StartDate
            # O script original apenas digita "01" (dia 1 do mes)
            campo_data_ini = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[@id='StartDate']"
                    " | //input[contains(@id,'StartDate') or contains(@id,'startDate') "
                    "or contains(@name,'StartDate')]"
                    " | //label[contains(.,'Data inicial')]//following::input[1]"
                ))
            )
            campo_data_ini.click()
            campo_data_ini.send_keys(Keys.CONTROL, "a")
            campo_data_ini.send_keys("01")
            campo_data_ini.send_keys(Keys.ENTER)
            time.sleep(1)

            # Seleciona status "Retida" no Select2 (exatamente como script original)
            try:
                # Abre o Select2
                select2_container = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//span[@id='select2-Status-container']"
                        " | //span[contains(@id,'select2') and contains(@id,'Status')]"
                    ))
                )
                select2_container.click()
                time.sleep(1)

                # Seleciona opcao "Retida"
                opcao_retida = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//li[contains(@class,'select2-results__option') "
                        "and normalize-space()='Retida']"
                    ))
                )
                opcao_retida.click()
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Nao conseguiu selecionar status 'Retida': {e}")

            # Clica Pesquisar (botao com id searchButton ou texto)
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[@id='searchButton']"
                    " | //button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(5)

            logger.info(f"Filtros configurados: data 01 + status Retida")

        except Exception as e:
            logger.error(f"Erro ao configurar filtros de retencao: {e}")
            raise

    def selecionar_awbs_para_liberacao(self, awbs: Set[str]) -> Dict[str, str]:
        """
        Para cada AWB na lista, pesquisa no campo de filtro da DataTable
        e seleciona usando o menu "Tudo" (exatamente como script original).

        Baseado no script original (Liberar_retencao.py):
        - Campo: //*[@id='RetentionList_filter']/label/input
        - Menu: //div[contains(@class,'divDataTableSelection')]
        - Opcao: //span[contains(@class,'DataTableSelectionAll')]

        Args:
            awbs: Set de AWBs para tentar liberar

        Returns:
            Dict com resultado
        """
        resultado = {
            "selecionados": [],
            "ja_liberados": [],
            "erros": [],
        }

        # XPaths exatos do script original
        xpath_input = (
            "//*[@id='RetentionList_filter']/label/input"
            " | //*[@id='RetentionList_filter']//input"
            " | //div[contains(@id,'_filter')]//input"
            " | //input[contains(@type,'search')]"
        )
        xpath_menu = "//div[contains(@class,'divDataTableSelection')]"
        xpath_opcao_tudo = "//span[contains(@class,'DataTableSelectionAll')]"

        for awb in awbs:
            try:
                # 1. Espera o campo de filtro
                campo = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_input))
                )

                # 2. Limpa e digita o AWB
                campo.click()
                campo.send_keys(Keys.CONTROL, "a")
                campo.send_keys(Keys.BACKSPACE)
                campo.send_keys(awb)

                logger.debug(f"AWB digitado: {awb}")
                time.sleep(2)  # Tempo para a tabela filtrar

                # 3. Verifica se a tabela tem resultados
                # Se filtrou e nao tem linhas, AWB ja foi liberado
                try:
                    linhas = self.driver.find_elements(By.XPATH,
                        "//table[@id='RetentionList']//tbody//tr"
                        " | //table//tbody//tr"
                    )
                    # Verifica se tem a mensagem "Nenhum registro"
                    tem_resultado = False
                    for linha in linhas:
                        texto = linha.text.strip()
                        if texto and "nenhum" not in texto.lower() and "empty" not in texto.lower():
                            tem_resultado = True
                            break

                    if not tem_resultado:
                        resultado["ja_liberados"].append(awb)
                        logger.info(f"AWB {awb}: nao encontrado (ja liberado/retirado)")
                        continue
                except Exception:
                    pass

                # 4. Clica no menu de selecao (exatamente como script original)
                menu = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_menu))
                )
                menu.click()

                # 5. Clica em "Tudo" (seleciona todas as linhas filtradas)
                opcao = self.wait.until(
                    EC.visibility_of_element_located((By.XPATH, xpath_opcao_tudo))
                )
                opcao.click()

                logger.info(f"AWB {awb}: selecionado (menu 'Tudo' clicado)")
                resultado["selecionados"].append(awb)

                # Pausa entre AWBs
                time.sleep(2)

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
