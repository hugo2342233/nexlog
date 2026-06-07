"""
Modulo para operacoes com CTe no Nexlog:
- Buscar CTe na aba 'Por referencia' para encontrar o AWB
- Adicionar comentario critico no AWB via busca rapida
"""

import time
import logging
import re
from typing import Optional, List, Dict

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from models.termo import TermoApreensao, ConsultaMDFe
from modules.browser import NexlogBrowser

logger = logging.getLogger(__name__)


class NexlogCTeOperacoes:
    """Operacoes com CTe no Nexlog."""

    def __init__(self, browser: NexlogBrowser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def buscar_awb_do_cte(self, numero_cte: str) -> str:
        """
        Busca o AWB associado a um CTe.
        Fluxo: Vendas > Conhecimento > Lista > Aba 'Por referencia'
               > Campo 'Numero integracao' > Pesquisar > Le 'N. documento'

        IMPORTANTE: SEMPRE renavega para a pagina antes de buscar.
        Apos pesquisar, os campos de filtro ficam ocultos e o botao
        de filtro nao esta sendo encontrado pelos seletores.
        A forma mais segura e simplesmente recarregar a pagina.

        Returns:
            Numero do AWB (127...) ou "" se nao encontrar
        """
        try:
            # SEMPRE renavega para Conhecimento/Lista (reseta a pagina)
            # Isso garante que os campos de filtro estejam visiveis
            self.browser.navegar_vendas_conhecimento_lista()
            time.sleep(3)

            # Clica na aba "Por referencia"
            aba_referencia = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Por refer') or contains(.,'por refer')]"
                    " | //a[text()='Por referência']"
                ))
            )
            aba_referencia.click()
            time.sleep(2)

            # Preenche campo "Numero integracao"
            campo_integracao = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[contains(@id,'Integration') or contains(@name,'Integration') "
                    "or contains(@id,'integration')]"
                    " | //label[contains(.,'integra')]//following::input[1]"
                    " | //input[contains(@placeholder,'integra')]"
                ))
            )
            campo_integracao.click()
            campo_integracao.send_keys(Keys.CONTROL, "a")
            campo_integracao.send_keys(Keys.BACKSPACE)
            campo_integracao.send_keys(numero_cte)
            time.sleep(0.5)

            # Clica pesquisar
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Pesquisar')]"
                ))
            )
            botao_pesquisar.click()
            time.sleep(4)

            # Le o AWB da coluna "N. documento" na tabela de resultados
            awb = self._ler_awb_resultado()

            if awb:
                logger.info(f"CTe {numero_cte} -> AWB {awb}")
            else:
                logger.warning(f"CTe {numero_cte}: AWB nao encontrado")

            return awb

        except TimeoutException as e:
            logger.error(f"Timeout ao buscar AWB do CTe {numero_cte}: {e}")
            return ""
        except Exception as e:
            logger.error(f"Erro ao buscar AWB do CTe {numero_cte}: {e}")
            return ""

    def _garantir_campo_integracao_visivel(self):
        """
        Garante que o campo 'Numero integracao' esta visivel.
        Se nao estiver, clica no botao de filtro para mostrar.
        
        No Nexlog (Conhecimento/Lista), apos pesquisar os campos somem.
        O botao de filtro e um icone de funil na barra superior,
        proximo ao badge "Filtro aplicado".
        """
        campo_visivel = False
        try:
            campo_teste = self.driver.find_element(By.XPATH,
                "//input[contains(@id,'Integration') or contains(@name,'Integration') "
                "or contains(@id,'integration')]"
                " | //label[contains(.,'integra')]//following::input[1]"
                " | //input[contains(@placeholder,'integra')]"
            )
            campo_visivel = campo_teste.is_displayed()
        except Exception:
            campo_visivel = False

        if not campo_visivel:
            logger.debug("Campo integracao NAO visivel - tentando abrir filtro...")
            
            # Estrategias para encontrar o botao de filtro no Nexlog
            seletores_filtro = [
                # 1. Icone fa-filter — elemento pai clicavel
                "//*[contains(@class,'fa-filter')]/ancestor::button",
                "//*[contains(@class,'fa-filter')]/ancestor::a",
                "//*[contains(@class,'fa-filter')]/..",
                "//*[contains(@class,'fa-filter')]",
                # 2. Glyphicon filter
                "//*[contains(@class,'glyphicon-filter')]/ancestor::button",
                "//*[contains(@class,'glyphicon-filter')]/..",
                "//*[contains(@class,'glyphicon-filter')]",
                # 3. Proximo ao badge "Filtro aplicado"
                "//*[contains(text(),'Filtro aplicado')]/ancestor::div[1]//button",
                "//*[contains(text(),'Filtro aplicado')]/preceding-sibling::*[self::button or self::a]",
                "//*[contains(text(),'Filtro aplicado')]/following-sibling::*[self::button or self::a]",
                "//*[contains(text(),'Filtro aplicado')]/..",
                # 4. Barra de ferramentas / panel heading
                "//div[contains(@class,'panel-heading') or contains(@class,'card-header') "
                "or contains(@class,'toolbar')]//button",
                "//div[contains(@class,'panel-heading') or contains(@class,'card-header') "
                "or contains(@class,'toolbar')]//a[contains(@class,'btn')]",
                # 5. Botao/link com classe filter
                "//button[contains(@class,'filter')]",
                "//a[contains(@class,'filter')]",
                # 6. Botao com title filtro
                "//button[contains(@title,'iltro') or contains(@title,'ilter')]",
                "//a[contains(@title,'iltro') or contains(@title,'ilter')]",
                # 7. data-toggle collapse com filter
                "//*[@data-toggle='collapse'][contains(@href,'ilter') "
                "or contains(@data-target,'ilter')]",
                # 8. aria-label
                "//*[contains(@aria-label,'iltro') or contains(@aria-label,'ilter')]",
                # 9. Icone SVG ou span com icone
                "//span[contains(@class,'icon') and contains(@class,'filter')]/..",
                # 10. Qualquer i (icone) que parece filtro
                "//i[contains(@class,'filter') or contains(@class,'funnel')]/..",
            ]
            
            for xpath in seletores_filtro:
                try:
                    elementos = self.driver.find_elements(By.XPATH, xpath)
                    for elem in elementos:
                        if elem.is_displayed():
                            try:
                                elem.click()
                            except Exception:
                                self.driver.execute_script("arguments[0].click();", elem)
                            time.sleep(1.5)
                            
                            # Verifica se o campo apareceu
                            try:
                                campo_teste2 = self.driver.find_element(By.XPATH,
                                    "//input[contains(@id,'Integration') or "
                                    "contains(@name,'Integration') or "
                                    "contains(@id,'integration')]"
                                    " | //label[contains(.,'integra')]//following::input[1]"
                                    " | //input[contains(@placeholder,'integra')]"
                                )
                                if campo_teste2.is_displayed():
                                    logger.debug(f"Filtro aberto via: {xpath[:50]}")
                                    return
                                else:
                                    # Nao apareceu — clica de novo pra reverter
                                    try:
                                        elem.click()
                                        time.sleep(0.5)
                                    except Exception:
                                        pass
                            except Exception:
                                # Campo nao no DOM — reverter
                                try:
                                    elem.click()
                                    time.sleep(0.5)
                                except Exception:
                                    pass
                            break
                except Exception:
                    continue
            
            # FALLBACK: Se nada funcionou, renavega para a pagina
            # Isso reseta a view e mostra os campos novamente
            logger.warning("NAO conseguiu abrir o filtro! Renavegando para Conhecimento/Lista...")
            try:
                self.browser.navegar_vendas_conhecimento_lista()
                time.sleep(3)
            except Exception:
                pass

    def _ler_awb_resultado(self) -> str:
        """Le o AWB (N. documento) da primeira linha da tabela de resultados."""
        try:
            # Verifica se tem resultados
            try:
                self.driver.find_element(By.XPATH,
                    "//*[contains(.,'Nenhum registro')]"
                )
                return ""
            except Exception:
                pass

            # Busca na tabela de resultados a coluna N. documento
            # O AWB comeca com 127
            celulas = self.driver.find_elements(By.XPATH,
                "//table//tbody//tr[1]//td"
            )
            for celula in celulas:
                texto = celula.text.strip()
                if texto.startswith("127") and len(texto) >= 10 and texto.isdigit():
                    return texto

            # Alternativa: busca por regex na pagina
            page_text = self.driver.find_element(
                By.XPATH, "//table//tbody"
            ).text
            match = re.search(r'\b(127\d{7,})\b', page_text)
            if match:
                return match.group(1)

            return ""

        except Exception:
            return ""

    def adicionar_comentario_critico(self, awb: str, texto_comentario: str) -> bool:
        """
        Adiciona um comentario critico em um AWB no Nexlog.
        Fluxo:
        1. Cola AWB no campo pesquisa rapida > clica no alvo
        2. Abre tela de Rastreio
        3. Clica "Adicionar comentarios"
        4. Digita texto no campo Comentario
        5. Marca checkbox "Critico?"
        6. Clica botao "Adicionar"
        7. Fecha modal Comentarios
        8. Fecha modal Rastreio

        Returns:
            True se adicionou com sucesso
        """
        if not awb:
            logger.warning("AWB vazio - nao pode adicionar comentario")
            return False

        try:
            # 1. Busca rapida pelo AWB
            self.browser.busca_rapida(awb)
            time.sleep(3)

            # 2. Clica em "Adicionar comentarios" (link no canto superior direito)
            link_comentario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//a[contains(.,'Adicionar coment')]"
                    " | //*[contains(.,'Adicionar coment') and (self::a or self::button)]"
                ))
            )
            link_comentario.click()
            time.sleep(3)

            # 3. Localiza o campo de texto "Comentario"
            campo_comentario = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//textarea"
                    " | //div[contains(@class,'modal')]//textarea"
                ))
            )
            campo_comentario.clear()
            campo_comentario.send_keys(texto_comentario)
            time.sleep(0.5)

            # 4. Marca checkbox "Critico?"
            checkbox_critico = self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//input[@type='checkbox'][following-sibling::*[contains(.,'tico')] "
                    "or ancestor::label[contains(.,'tico')]]"
                    " | //label[contains(.,'tico')]//input[@type='checkbox']"
                    " | //input[@type='checkbox'][contains(@id,'ritico') "
                    "or contains(@id,'ritical') or contains(@name,'ritico')]"
                ))
            )
            if not checkbox_critico.is_selected():
                # Tenta clicar diretamente ou via JavaScript
                try:
                    checkbox_critico.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", checkbox_critico)
            time.sleep(0.5)

            # 5. Clica botao "Adicionar" (azul com +)
            botao_adicionar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Adicionar')]"
                    " | //button[contains(@class,'btn-primary') and contains(@class,'add')]"
                ))
            )
            botao_adicionar.click()
            time.sleep(2)

            # 6. Fecha modal Comentarios (botao "Fechar")
            try:
                botao_fechar_comentario = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH,
                        "(//button[contains(.,'Fechar')])[last()]"
                    ))
                )
                botao_fechar_comentario.click()
                time.sleep(1)
            except Exception:
                pass

            # 7. Fecha modal Rastreio
            try:
                # Tenta fechar com X ou botao Fechar
                botao_fechar_rastreio = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//button[contains(@class,'close')]"
                    " | //div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
                )
                botao_fechar_rastreio.click()
                time.sleep(1)
            except Exception:
                self.browser._fechar_modais()

            logger.info(f"Comentario adicionado: AWB {awb} -> '{texto_comentario}'")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout ao adicionar comentario no AWB {awb}: {e}")
            self.browser._fechar_modais()
            return False
        except Exception as e:
            logger.error(f"Erro ao adicionar comentario no AWB {awb}: {e}")
            self.browser._fechar_modais()
            return False

    def processar_termos_voo(self, consulta: ConsultaMDFe) -> Dict[str, str]:
        """
        Processa todos os termos de um voo:
        - Para cada CTe com termo, busca o AWB
        - Adiciona comentario critico no AWB
        - Agrupa termos do mesmo CTe em um unico comentario

        Returns:
            Dict mapeando CTe -> AWB (para uso posterior na liberacao)
        """
        mapa_cte_awb = {}
        ctes_processados = set()

        # Agrupa termos por CTe (pode ter multiplos termos pro mesmo CTe)
        ctes_unicos = list(set(consulta.ctes_retidos))

        # Navega para a pagina de lista de conhecimentos
        self.browser.navegar_vendas_conhecimento_lista()
        time.sleep(2)

        for cte in ctes_unicos:
            if cte in ctes_processados:
                continue

            logger.info(f"Processando CTe {cte}...")

            # 1. Busca AWB do CTe
            awb = self.buscar_awb_do_cte(cte)
            if not awb:
                logger.warning(f"CTe {cte}: AWB nao encontrado - pulando")
                continue

            mapa_cte_awb[cte] = awb

            # 2. Gera comentario (com todos os termos deste CTe)
            comentario = consulta.comentario_para_cte(cte)

            # 3. Adiciona comentario critico no AWB
            sucesso = self.adicionar_comentario_critico(awb, comentario)

            if sucesso:
                logger.info(f"  OK: AWB {awb} <- '{comentario}'")
            else:
                logger.error(f"  FALHA: AWB {awb} <- '{comentario}'")

            ctes_processados.add(cte)
            time.sleep(1)

        return mapa_cte_awb
