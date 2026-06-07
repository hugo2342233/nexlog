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

        Returns:
            Numero do AWB (127...) ou "" se nao encontrar
        """
        try:
            # SEMPRE renavega para Conhecimento/Lista (reseta a pagina)
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
            time.sleep(3)

            # Verifica se campo esta visivel
            if not self._campo_integracao_visivel():
                # Tenta abrir filtro via JavaScript (clica no pai do i.fa-filter)
                logger.debug("Campo nao visivel, tentando abrir filtro via JS...")
                self._tentar_abrir_filtro()
                time.sleep(2)

            if not self._campo_integracao_visivel():
                # FALLBACK: forca exibicao via JavaScript
                # Expande qualquer container colapsado que contenha o campo
                logger.warning("Filtro nao abriu - forcando exibicao via JS...")
                self.driver.execute_script("""
                    // Busca containers colapsados e expande os que tem 'integra'
                    var els = document.querySelectorAll('.collapse, [style*="display: none"], [style*="display:none"], .panel-collapse');
                    for (var i = 0; i < els.length; i++) {
                        var el = els[i];
                        var txt = (el.textContent || '').toLowerCase();
                        if (txt.indexOf('integra') !== -1 || txt.indexOf('pesquisar') !== -1) {
                            el.style.display = 'block';
                            el.style.height = 'auto';
                            el.style.overflow = 'visible';
                            el.classList.add('show');
                            el.classList.add('in');
                            el.classList.remove('collapsing');
                        }
                    }
                    // Busca inputs hidden com 'Integration' e mostra seus parents
                    var inputs = document.querySelectorAll('input[id*="Integration"], input[name*="Integration"]');
                    for (var j = 0; j < inputs.length; j++) {
                        var input = inputs[j];
                        var parent = input.parentElement;
                        while (parent && parent !== document.body) {
                            if (parent.style.display === 'none' || parent.classList.contains('collapse')) {
                                parent.style.display = 'block';
                                parent.style.height = 'auto';
                                parent.classList.add('show');
                                parent.classList.add('in');
                            }
                            parent = parent.parentElement;
                        }
                    }
                """)
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

    def _campo_integracao_visivel(self) -> bool:
        """Verifica se o campo Numero integracao esta visivel."""
        try:
            campo = self.driver.find_element(By.XPATH,
                "//input[contains(@id,'Integration') or contains(@name,'Integration') "
                "or contains(@id,'integration')]"
                " | //label[contains(.,'integra')]//following::input[1]"
                " | //input[contains(@placeholder,'integra')]"
            )
            return campo.is_displayed()
        except Exception:
            return False

    def _tentar_abrir_filtro(self) -> bool:
        """
        Clica no botao de filtro para mostrar os campos.
        O botao e um icone: <i class="fal fa-filter"></i>
        Usa JavaScript puro para encontrar e clicar.
        """
        try:
            # JavaScript: encontra i.fa-filter e clica no pai
            clicou = self.driver.execute_script("""
                // Busca por classe exata
                var icones = document.querySelectorAll('i.fa-filter, i[class*="fa-filter"]');
                if (icones.length > 0) {
                    var pai = icones[0].parentElement;
                    if (pai) { pai.click(); return 'pai'; }
                    icones[0].click(); return 'icone';
                }
                // Busca qualquer elemento com filter na classe
                var todos = document.querySelectorAll('[class*="filter"]');
                for (var i = 0; i < todos.length; i++) {
                    var el = todos[i];
                    var tag = el.tagName.toLowerCase();
                    if (tag === 'i' || tag === 'button' || tag === 'a' || tag === 'span') {
                        el.click(); return 'generico-' + tag;
                    }
                    if (tag === 'i') {
                        var p = el.parentElement;
                        if (p) { p.click(); return 'generico-pai'; }
                    }
                }
                return null;
            """)

            if clicou:
                logger.debug(f"Filtro clicado via JS: {clicou}")
                time.sleep(2)
                return self._campo_integracao_visivel()

        except Exception as e:
            logger.debug(f"Erro JS filtro: {e}")

        return False

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

        Returns:
            Dict mapeando CTe -> AWB (para uso posterior na liberacao)
        """
        mapa_cte_awb = {}
        ctes_processados = set()
        ctes_unicos = list(set(consulta.ctes_retidos))

        for cte in ctes_unicos:
            if cte in ctes_processados:
                continue

            logger.info(f"Processando CTe {cte}...")

            awb = self.buscar_awb_do_cte(cte)
            if not awb:
                logger.warning(f"CTe {cte}: AWB nao encontrado - pulando")
                continue

            mapa_cte_awb[cte] = awb
            comentario = consulta.comentario_para_cte(cte)
            sucesso = self.adicionar_comentario_critico(awb, comentario)

            if sucesso:
                logger.info(f"  OK: AWB {awb} <- '{comentario}'")
            else:
                logger.error(f"  FALHA: AWB {awb} <- '{comentario}'")

            ctes_processados.add(cte)
            time.sleep(1)

        return mapa_cte_awb
