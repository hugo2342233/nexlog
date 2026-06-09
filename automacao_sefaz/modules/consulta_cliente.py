"""
Modulo de consulta para atendimento ao cliente (WhatsApp/Telegram).
Centraliza todas as buscas necessarias para responder perguntas de clientes.

Cenarios:
1. "Chegou em MCZ?" -> Busca rapida no Nexlog pelo AWB
2. "Esta liberado?" -> Verifica na tela de Retencoes (status)
3. "Retido sem termo" -> Resposta: "Aguardando analise fiscal"
4. "Retido com termo" -> Consulta termo na SEFAZ, baixa arquivos
5. "Entrega: quando sai?" -> Verifica se AWB esta na relacao de entrega (email)
6. "Entrega com termo" -> Mesmo tratamento do item 4
"""

import time
import re
import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)


# ========= MODELOS DE RESPOSTA =========

class StatusAWB(Enum):
    CHEGOU_LIBERADO = "chegou_liberado"
    CHEGOU_RETIDO_SEM_TERMO = "chegou_retido_sem_termo"
    CHEGOU_RETIDO_COM_TERMO = "chegou_retido_com_termo"
    NAO_CHEGOU = "nao_chegou"
    EM_ROTA_ENTREGA = "em_rota_entrega"
    ENTREGA_RETIDA = "entrega_retida"
    NAO_ENCONTRADO = "nao_encontrado"


@dataclass
class TermoInfo:
    """Informacoes de um termo de apreensao."""
    numero: str = ""
    situacao: str = ""
    arquivo_path: str = ""  # Caminho do PDF do termo baixado


@dataclass
class ResultadoConsulta:
    """Resultado completo de uma consulta de AWB para o cliente."""
    awb: str = ""
    status: StatusAWB = StatusAWB.NAO_ENCONTRADO
    tipo_entrega: str = ""  # "RETIRA" ou "ENTREGA/DOMICILIO"
    data_chegada: str = ""
    voo: str = ""
    termos: List[TermoInfo] = field(default_factory=list)
    em_rota: bool = False
    observacao: str = ""

    def resposta_cliente(self) -> str:
        """Gera texto pronto para enviar ao cliente no WhatsApp."""
        linhas = []

        if self.status == StatusAWB.NAO_CHEGOU:
            linhas.append(f"AWB {self.awb}: Ainda nao chegou em MCZ.")
            if self.observacao:
                linhas.append(self.observacao)
            return "\n".join(linhas)

        if self.status == StatusAWB.NAO_ENCONTRADO:
            linhas.append(f"AWB {self.awb}: Nao encontrado no sistema.")
            return "\n".join(linhas)

        # Chegou
        linhas.append(f"AWB {self.awb}")
        if self.voo:
            linhas.append(f"Voo: {self.voo}")
        if self.data_chegada:
            linhas.append(f"Chegada: {self.data_chegada}")
        linhas.append(f"Tipo: {self.tipo_entrega}")
        linhas.append("")

        if self.status == StatusAWB.CHEGOU_LIBERADO:
            if self.tipo_entrega.upper() in ("RETIRA", "TECA"):
                linhas.append("Status: LIBERADO - Disponivel para retirada.")
            else:
                linhas.append("Status: LIBERADO")

        elif self.status == StatusAWB.CHEGOU_RETIDO_SEM_TERMO:
            linhas.append("Status: RETIDO - Aguardando analise fiscal (SEFAZ).")
            linhas.append("Ainda nao foi gerado termo de apreensao.")

        elif self.status == StatusAWB.CHEGOU_RETIDO_COM_TERMO:
            linhas.append("Status: RETIDO com Termo de Apreensao.")
            for t in self.termos:
                linha_termo = f"  - TA {t.numero}"
                if t.situacao:
                    linha_termo += f" ({t.situacao})"
                linhas.append(linha_termo)

        elif self.status == StatusAWB.EM_ROTA_ENTREGA:
            linhas.append("Status: SAIU PARA ENTREGA.")
            linhas.append("AWB consta na relacao de entrega de hoje.")

        elif self.status == StatusAWB.ENTREGA_RETIDA:
            linhas.append("Status: ENTREGA RETIDA - Aguardando liberacao fiscal.")
            if self.termos:
                linhas.append("Termo(s) de apreensao:")
                for t in self.termos:
                    linhas.append(f"  - TA {t.numero}")

        if self.observacao:
            linhas.append("")
            linhas.append(self.observacao)

        return "\n".join(linhas)


# ========= CLASSE PRINCIPAL =========

class ConsultaCliente:
    """
    Orquestra consultas no Nexlog/SEFAZ/Outlook para atendimento ao cliente.
    
    Usa o NexlogBrowser ja aberto (mesmo navegador do processamento).
    Se nao houver navegador aberto, abre um novo em headless.
    """

    def __init__(self, browser=None):
        """
        Args:
            browser: instancia de NexlogBrowser ja aberta e logada.
                     Se None, vai precisar criar na hora da consulta.
        """
        self.browser = browser
        self.driver = browser.driver if browser else None
        self.wait = browser.wait if browser else None

    def consultar_awb(self, awb: str) -> ResultadoConsulta:
        """
        Consulta completa de um AWB.
        
        Fluxo:
        1. Busca rapida no Nexlog (rastreio) -> verifica se chegou
        2. Verifica tela de Retencoes -> status retido/liberado
        3. Se retido -> consulta SEFAZ para ver se tem termo
        4. Se entrega -> verifica email com lista de entregas
        
        Returns:
            ResultadoConsulta com tudo preenchido
        """
        resultado = ResultadoConsulta(awb=awb)

        if not self.driver:
            resultado.observacao = "Navegador nao disponivel."
            return resultado

        # Etapa 1: Busca rapida no Nexlog (rastreio do AWB)
        info_rastreio = self._buscar_rastreio(awb)

        if not info_rastreio.get("encontrado"):
            resultado.status = StatusAWB.NAO_ENCONTRADO
            return resultado

        resultado.voo = info_rastreio.get("voo", "")
        resultado.data_chegada = info_rastreio.get("data_chegada", "")
        resultado.tipo_entrega = info_rastreio.get("tipo_entrega", "")

        # Se nao chegou em MCZ ainda
        if not info_rastreio.get("chegou_mcz"):
            resultado.status = StatusAWB.NAO_CHEGOU
            resultado.observacao = info_rastreio.get("ultimo_status", "")
            return resultado

        # Etapa 2: Verifica status na tela de Retencoes
        status_retencao = self._verificar_retencao(awb)

        if status_retencao == "liberada":
            resultado.status = StatusAWB.CHEGOU_LIBERADO
        elif status_retencao == "nao_encontrada":
            # Nao aparece em retencoes = ja foi liberado/retirado
            resultado.status = StatusAWB.CHEGOU_LIBERADO
            resultado.observacao = "Ja liberado/retirado anteriormente."
        elif status_retencao == "retida":
            # Retida — precisa ver se tem termo ou nao
            termos = self._consultar_termos_awb(awb)
            if termos:
                resultado.status = StatusAWB.CHEGOU_RETIDO_COM_TERMO
                resultado.termos = termos
            else:
                resultado.status = StatusAWB.CHEGOU_RETIDO_SEM_TERMO

        # Etapa 3: Se tipo ENTREGA, verifica se saiu para rota
        if resultado.tipo_entrega.upper() in ("ENTREGA", "DOMICILIO", "ENTREGA DOMICILIO"):
            if status_retencao == "retida":
                resultado.status = StatusAWB.ENTREGA_RETIDA
            else:
                em_rota = self._verificar_rota_entrega(awb)
                if em_rota:
                    resultado.status = StatusAWB.EM_ROTA_ENTREGA
                    resultado.em_rota = True

        return resultado

    # ========= BUSCA RASTREIO (ETAPA 1) =========

    def _buscar_rastreio(self, awb: str) -> dict:
        """
        Busca rapida do AWB no Nexlog.
        Usa a busca rapida (campo quickSearch) para abrir o rastreio.
        Extrai: se chegou, voo, data, tipo entrega, ultimo status.
        """
        info = {"encontrado": False, "chegou_mcz": False}

        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()
            time.sleep(1)

            # Busca rapida pelo AWB
            self.browser.busca_rapida(awb)
            time.sleep(3)

            # Verifica se abriu a tela de rastreio
            try:
                modal = self.wait.until(
                    EC.presence_of_element_located((By.XPATH,
                        "//div[contains(@class,'modal') and contains(@class,'show')]"
                        " | //div[contains(@class,'modal')]//table"
                        " | //*[contains(.,'Rastreio') or contains(.,'rastreio')]"
                    ))
                )
            except TimeoutException:
                return info

            info["encontrado"] = True

            # Extrai informacoes do modal de rastreio
            try:
                texto_modal = modal.text if modal else ""

                # Tipo entrega (RETIRA / ENTREGA DOMICILIO)
                if "RETIRA" in texto_modal.upper():
                    info["tipo_entrega"] = "RETIRA"
                elif "DOMIC" in texto_modal.upper() or "ENTREGA" in texto_modal.upper():
                    info["tipo_entrega"] = "ENTREGA"

                # Verifica se chegou em MCZ (tem evento de chegada)
                if "MCZ" in texto_modal.upper() and ("CHEGAD" in texto_modal.upper()
                        or "RECEBID" in texto_modal.upper()
                        or "DESEMBARCAD" in texto_modal.upper()):
                    info["chegou_mcz"] = True

                # Extrai voo
                match_voo = re.search(r'G3\s*\d+', texto_modal)
                if match_voo:
                    info["voo"] = match_voo.group(0)

                # Extrai data de chegada
                datas = re.findall(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto_modal)
                if datas:
                    info["data_chegada"] = datas[-1]  # Ultima data (mais recente)

                # Ultimo status visivel
                linhas = texto_modal.split("\n")
                for linha in reversed(linhas):
                    if linha.strip() and len(linha.strip()) > 5:
                        info["ultimo_status"] = linha.strip()[:100]
                        break

            except Exception:
                pass

            # Fecha modal
            self.browser._fechar_modais()
            time.sleep(1)

        except Exception as e:
            logger.debug(f"Consulta rastreio erro: {e}")
            self.browser._fechar_modais()

        return info

    # ========= VERIFICAR RETENCAO (ETAPA 2) =========

    def _verificar_retencao(self, awb: str) -> str:
        """
        Verifica o status do AWB na tela de Retencoes.
        
        Returns:
            "liberada", "retida", "parcial", ou "nao_encontrada"
        """
        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()

            # Navega para Retencoes
            self.browser.navegar_vendas_retencao_lista()
            time.sleep(3)

            # Usa o campo de filtro da tabela para buscar o AWB
            xpath_input = (
                "//*[@id='RetentionList_filter']/label/input"
                " | //*[@id='RetentionList_filter']//input"
                " | //div[contains(@id,'_filter')]//input"
                " | //input[contains(@type,'search')]"
            )

            try:
                campo = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, xpath_input))
                )
            except TimeoutException:
                # Pode precisar clicar pesquisar primeiro
                try:
                    btn = self.driver.find_element(By.XPATH,
                        "//*[@id='searchButton'] | //button[contains(.,'Pesquisar')]"
                    )
                    btn.click()
                    time.sleep(5)
                    campo = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, xpath_input))
                    )
                except Exception:
                    return "nao_encontrada"

            # Digita o AWB
            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.BACKSPACE)
            campo.send_keys(awb)
            time.sleep(2)

            # Verifica linhas da tabela
            linhas = self.driver.find_elements(By.XPATH,
                "//table[@id='RetentionList']//tbody//tr"
                " | //table//tbody//tr"
            )

            for linha in linhas:
                texto = linha.text.strip().upper()
                if not texto or "NENHUM" in texto or "EMPTY" in texto:
                    return "nao_encontrada"

                if "LIBERADA" in texto:
                    return "liberada"
                elif "PARCIAL" in texto:
                    return "parcial"
                elif "RETIDA" in texto:
                    return "retida"

            return "nao_encontrada"

        except Exception as e:
            logger.debug(f"Consulta retencao erro: {e}")
            return "nao_encontrada"

    # ========= CONSULTAR TERMOS (ETAPA 3) =========

    def _consultar_termos_awb(self, awb: str) -> List[TermoInfo]:
        """
        Verifica se o AWB tem termos de apreensao na SEFAZ.
        
        Estrategia:
        1. Busca o CTe do AWB (Conhecimento > Por referencia)
        2. Usa o CTe para verificar se ha termo associado
        
        Para simplificar a consulta rapida do cliente, verifica
        nos comentarios do AWB se tem "RETIDO PELA SEFAZ TA".
        Se tiver, extrai o numero do termo direto do comentario.
        """
        termos = []

        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()
            time.sleep(1)

            # Busca rapida do AWB para ver comentarios
            self.browser.busca_rapida(awb)
            time.sleep(3)

            # Tenta ler comentarios do rastreio
            try:
                texto_modal = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]"
                ).text
            except Exception:
                texto_modal = ""

            # Procura por "RETIDO PELA SEFAZ TA" nos comentarios
            matches = re.findall(r'TA\s*(\d+)', texto_modal, re.IGNORECASE)
            for num_ta in matches:
                termos.append(TermoInfo(numero=num_ta))

            self.browser._fechar_modais()
            time.sleep(1)

        except Exception as e:
            logger.debug(f"Consulta termos erro: {e}")
            self.browser._fechar_modais()

        return termos

    # ========= VERIFICAR ROTA DE ENTREGA (ETAPA 4) =========

    def _verificar_rota_entrega(self, awb: str) -> bool:
        """
        Verifica se o AWB esta na relacao de entrega de hoje.
        
        Estrategia: Busca no Outlook por emails com "relacao de entrega"
        ou "lista de entrega" de hoje, e verifica se o AWB consta.
        
        Returns:
            True se o AWB foi encontrado na lista de entregas de hoje
        """
        try:
            from modules.outlook import OutlookWeb
            outlook = OutlookWeb(self.driver)

            outlook.abrir_outlook()
            time.sleep(2)

            # Busca por relacao de entrega + AWB
            try:
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
                time.sleep(0.3)

                # Busca pelo AWB em emails de hoje
                campo_busca.send_keys(awb)
                campo_busca.send_keys(Keys.ENTER)
                time.sleep(5)

                # Verifica se encontrou email com "relacao" ou "entrega" ou "rota"
                try:
                    resultado = self.driver.find_element(By.XPATH,
                        "//*[contains(.,'rela') and contains(.,'entrega')]"
                        " | //*[contains(.,'lista') and contains(.,'entrega')]"
                        " | //*[contains(.,'rota') and contains(.,'entrega')]"
                        " | //*[contains(.,'saiu para')]"
                    )
                    if resultado.is_displayed():
                        outlook.voltar_para_nexlog()
                        return True
                except Exception:
                    pass

            except Exception:
                pass

            outlook.voltar_para_nexlog()
            return False

        except Exception as e:
            logger.debug(f"Verificar rota entrega erro: {e}")
            try:
                # Volta para aba principal
                abas = self.driver.window_handles
                if abas:
                    self.driver.switch_to.window(abas[0])
            except Exception:
                pass
            return False

    # ========= CONSULTA VIA SEFAZ (DOWNLOAD TERMO) =========

    def consultar_termo_sefaz(self, numero_termo: str) -> Optional[str]:
        """
        Consulta um termo de apreensao especifico no site da SEFAZ.
        Tenta baixar o PDF/arquivo do termo.
        
        Fluxo no site SEFAZ:
        1. Consultar TADe (Termo de Apreensao e Deposito)
        2. Preenche numero do termo
        3. Pesquisa
        4. Clica em "Visualizar" ou "Imprimir"
        5. Baixa o PDF
        
        Returns:
            Caminho do arquivo PDF baixado, ou None se falhar
        """
        try:
            from modules.sefaz import SefazConsulta
            from config import config

            sefaz = SefazConsulta(self.driver)
            sefaz.abrir_sefaz()

            if not sefaz.logado:
                sefaz.login()

            # Navega para "Consultar TADe"
            time.sleep(2)
            try:
                botao_tade = self.driver.find_element(By.XPATH,
                    "//a[contains(.,'Consultar TADe') or contains(.,'TADe')]"
                    " | //*[contains(text(),'TADe')]"
                )
                if botao_tade.is_displayed():
                    botao_tade.click()
                    time.sleep(3)
            except Exception:
                # Tenta URL direta
                self.driver.get("https://transportadoras.sefaz.al.gov.br/#/consultar-tade")
                time.sleep(3)

            # Preenche numero do termo
            campo = None
            try:
                campo = self.driver.find_element(By.XPATH,
                    "//input[contains(@placeholder,'ermo') or "
                    "contains(@placeholder,'TADe') or "
                    "contains(@placeholder,'mero')]"
                    " | //input[@type='text']"
                )
            except Exception:
                pass

            if not campo:
                sefaz.voltar_para_nexlog()
                return None

            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.BACKSPACE)
            campo.send_keys(numero_termo)
            time.sleep(1)

            # Pesquisa
            try:
                btn = self.driver.find_element(By.XPATH,
                    "//button[contains(.,'Pesquisar') or contains(.,'Consultar')]"
                    " | //button[@type='submit']"
                )
                btn.click()
                time.sleep(5)
            except Exception:
                campo.send_keys(Keys.ENTER)
                time.sleep(5)

            # Tenta baixar/imprimir o termo
            try:
                btn_imprimir = self.driver.find_element(By.XPATH,
                    "//button[contains(.,'Imprimir') or contains(.,'Visualizar') "
                    "or contains(.,'Download') or contains(.,'Baixar')]"
                    " | //a[contains(.,'Imprimir') or contains(.,'Visualizar')]"
                )
                btn_imprimir.click()
                time.sleep(5)

                # Aguarda download
                caminho = self.browser.aguardar_download(timeout=15)
                sefaz.voltar_para_nexlog()
                return caminho if caminho else None

            except Exception:
                pass

            sefaz.voltar_para_nexlog()
            return None

        except Exception as e:
            logger.debug(f"Consulta termo SEFAZ erro: {e}")
            try:
                abas = self.driver.window_handles
                if abas:
                    self.driver.switch_to.window(abas[0])
            except Exception:
                pass
            return None
