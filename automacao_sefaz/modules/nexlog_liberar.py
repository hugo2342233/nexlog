"""
Modulo para liberar CTes no Nexlog.
Apenas libera CTes que:
- NAO possuem termo de apreensao
- Sao do tipo RETIRA (Teca / Aeroporto)
- NAO sao entrega domicilio
"""

import time
import logging
from typing import List

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException

from models.termo import CTeProcesado, TipoEntrega
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogLiberar:
    """Libera CTes no Nexlog (mesmo fluxo do Liberar_retencao.py existente)."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def navegar_lista_retencao(self):
        """
        Navega ate a tela de Lista de Retencao no Nexlog.
        Menu: Comercial > Lista (Retencao)
        """
        try:
            # Hover no menu (similar ao codigo existente)
            botao_menu = self.wait.until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//*[@id='mainMenu']/div[1]/div/ul/li[3]/div/div/div[1]/div"
                ))
            )
            ActionChains(self.driver).move_to_element(botao_menu).perform()
            time.sleep(1)

            # Clica em "Lista" de retencao
            opcao_lista = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//a[@href='/Sales/Retention/' and .//span[normalize-space()='Lista']]"
                ))
            )
            opcao_lista.click()
            time.sleep(3)

            logger.info("Nexlog: Na tela de Lista de Retencao")

        except TimeoutException:
            logger.error("Nexlog: Nao conseguiu navegar para Lista de Retencao")
            raise

    def configurar_filtros(self):
        """Configura filtros da lista (data e status 'Retida')."""
        try:
            # Data inicio = dia 01 do mes
            campo_data = self.wait.until(
                EC.element_to_be_clickable((By.ID, "StartDate"))
            )
            campo_data.click()
            campo_data.send_keys("01")
            campo_data.send_keys(Keys.ENTER)
            time.sleep(1)

            # Status = Retida
            self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH, "//span[@id='select2-Status-container']"
                ))
            ).click()

            self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//li[contains(@class,'select2-results__option') "
                    "and normalize-space()='Retida']"
                ))
            ).click()

            # Pesquisar
            self.wait.until(
                EC.element_to_be_clickable((By.ID, "searchButton"))
            ).click()
            time.sleep(5)

            logger.info("Nexlog: Filtros configurados (Retida, dia 01)")

        except TimeoutException:
            logger.error("Nexlog: Erro ao configurar filtros")
            raise

    def liberar_ctes(self, ctes_para_liberar: List[str]) -> dict:
        """
        Libera uma lista de CTes na tela de retencao.
        Usa o mesmo fluxo do Liberar_retencao.py (busca + seleciona tudo).

        Args:
            ctes_para_liberar: Lista de numeros de CTe para liberar

        Returns:
            Dict com resultado: {'liberados': [...], 'erros': [...]}
        """
        resultado = {"liberados": [], "erros": []}

        xpath_input = "//*[@id='RetentionList_filter']/label/input"
        xpath_menu = "//div[contains(@class,'divDataTableSelection')]"
        xpath_opcao_tudo = "//span[contains(@class,'DataTableSelectionAll')]"

        for cte in ctes_para_liberar:
            try:
                # Busca o CTe no filtro
                campo = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_input))
                )
                campo.click()
                campo.send_keys(Keys.CONTROL, "a")
                campo.send_keys(Keys.BACKSPACE)
                campo.send_keys(cte)

                time.sleep(2)  # Aguarda tabela filtrar

                # Seleciona tudo
                menu = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_menu))
                )
                menu.click()

                opcao = self.wait.until(
                    EC.visibility_of_element_located((By.XPATH, xpath_opcao_tudo))
                )
                opcao.click()

                resultado["liberados"].append(cte)
                logger.info(f"CTe {cte} selecionado para liberacao")

                time.sleep(1.5)

            except TimeoutException:
                resultado["erros"].append(cte)
                logger.warning(f"CTe {cte}: nao encontrado na lista de retencao")
            except Exception as e:
                resultado["erros"].append(cte)
                logger.error(f"CTe {cte}: erro ao liberar - {e}")

        return resultado

    def liberar_apenas_retira(self, ctes_processados: List[CTeProcesado]) -> dict:
        """
        Filtra e libera apenas os CTes que atendem TODAS as condicoes:
        1. NAO possuem termo de apreensao
        2. Sao tipo RETIRA (Teca / Aeroporto)

        Args:
            ctes_processados: Lista de CTeProcesado ja consultados

        Returns:
            Dict com resultado detalhado
        """
        # Filtra os que podem ser liberados
        para_liberar = []
        nao_liberar = []

        for cte in ctes_processados:
            if cte.pode_liberar:
                para_liberar.append(cte.numero_cte)
            else:
                nao_liberar.append({
                    "cte": cte.numero_cte,
                    "motivo": cte.motivo_nao_liberar
                })

        logger.info(
            f"Liberacao: {len(para_liberar)} para liberar | "
            f"{len(nao_liberar)} retidos/domicilio"
        )

        # Log dos que NAO serao liberados
        for item in nao_liberar:
            logger.info(f"  NAO liberar CTe {item['cte']}: {item['motivo']}")

        # Navega e libera
        if para_liberar:
            self.navegar_lista_retencao()
            self.configurar_filtros()
            resultado_liberacao = self.liberar_ctes(para_liberar)
        else:
            resultado_liberacao = {"liberados": [], "erros": []}

        return {
            "liberados": resultado_liberacao["liberados"],
            "erros_liberacao": resultado_liberacao["erros"],
            "retidos": nao_liberar,
            "total_processados": len(ctes_processados),
        }
