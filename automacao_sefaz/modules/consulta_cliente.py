"""
Modulo de consulta para atendimento ao cliente (WhatsApp/Telegram).
Usa a busca rapida do Nexlog (quickSearch + quickTracking).

A tela de rastreio que abre mostra:
- CT-e/AWB: numero
- Status operacional: Entregue / Retido / etc.
- Servico: GCE - E-GOLLOG / MELI / etc.
- Local de entrega: Entrega Domicilio / Retira Teca
- Tabela de etapas com Local e Destino (movimentacoes)
- Comentarios (via link "Adicionar comentarios")
"""

import time
import re
import logging
from dataclasses import dataclass, field
from typing import List

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)


class StatusAWB:
    LIBERADO = "liberado"
    RETIDO_SEM_TERMO = "retido_sem_termo"
    RETIDO_COM_TERMO = "retido_com_termo"
    AGUARDANDO_ENTREGA = "aguardando_entrega"
    EM_ROTA_ENTREGA = "em_rota"
    ENTREGUE = "entregue"
    NAO_CHEGOU = "nao_chegou"
    NAO_ENCONTRADO = "nao_encontrado"


@dataclass
class ResultadoConsulta:
    awb: str = ""
    status: str = StatusAWB.NAO_ENCONTRADO
    status_operacional: str = ""    # Texto exato: "Entregue", "Em trânsito", etc.
    servico: str = ""               # "GCE - E-GOLLOG", "MELI", etc.
    local_entrega: str = ""         # "Entrega Domicilio", "Retira Teca"
    ultima_etapa: str = ""          # Ex: "Desembarque" ou "Embarque"
    ultimo_local: str = ""          # Ex: "GRU"
    ultimo_destino: str = ""        # Ex: "MCZ"
    data_ultima_etapa: str = ""     # Ex: "06/06/2026 03:25:13"
    termos: List[str] = field(default_factory=list)
    tem_comentario_retido: bool = False
    observacao: str = ""

    def resposta_cliente(self) -> str:
        """Texto pronto para copiar no WhatsApp."""
        linhas = []
        linhas.append(f"AWB {self.awb}")

        if self.status == StatusAWB.NAO_ENCONTRADO:
            linhas.append("Nao encontrado no sistema.")
            return "\n".join(linhas)

        if self.status_operacional:
            linhas.append(f"Status: {self.status_operacional}")
        if self.local_entrega:
            linhas.append(f"Tipo: {self.local_entrega}")
        if self.ultimo_local and self.ultimo_destino:
            linhas.append(f"Movimentacao: {self.ultimo_local} > {self.ultimo_destino}")
        if self.ultima_etapa:
            linhas.append(f"Etapa: {self.ultima_etapa}")
        if self.data_ultima_etapa:
            linhas.append(f"Data: {self.data_ultima_etapa}")

        linhas.append("")

        # Mensagem baseada no status classificado
        if self.status == StatusAWB.ENTREGUE:
            linhas.append("Carga ja foi ENTREGUE.")

        elif self.status == StatusAWB.LIBERADO:
            if "RETIRA" in self.local_entrega.upper():
                linhas.append("LIBERADO - Disponivel para retirada.")
            else:
                linhas.append("LIBERADO.")

        elif self.status == StatusAWB.RETIDO_SEM_TERMO:
            linhas.append("RETIDO - Aguardando analise fiscal (SEFAZ).")
            linhas.append("Nenhum termo gerado ate o momento.")

        elif self.status == StatusAWB.RETIDO_COM_TERMO:
            linhas.append("RETIDO - Termo de Apreensao SEFAZ:")
            for ta in self.termos:
                linhas.append(f"  TA {ta}")

        elif self.status == StatusAWB.NAO_CHEGOU:
            linhas.append("Ainda NAO CHEGOU em MCZ.")
            if self.observacao:
                linhas.append(self.observacao)

        elif self.status == StatusAWB.AGUARDANDO_ENTREGA:
            linhas.append("AGUARDANDO ENTREGA - Carga em MCZ, aguardando rota.")

        elif self.status == StatusAWB.EM_ROTA_ENTREGA:
            linhas.append("SAIU PARA ENTREGA.")

        if self.observacao and self.status not in (StatusAWB.NAO_CHEGOU,):
            linhas.append(f"\n{self.observacao}")

        return "\n".join(linhas)


class ConsultaCliente:
    """Consulta AWB usando a busca rapida do Nexlog (quickSearch + quickTracking)."""

    def __init__(self, browser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def consultar_awb(self, awb: str) -> ResultadoConsulta:
        """
        Consulta um AWB pela busca rapida do Nexlog.
        
        Fluxo:
        1. Digita AWB no quickSearch, clica quickTracking (icone alvo)
        2. Abre modal "Rastreio" com status, etapas, etc.
        3. Le: status operacional, local entrega, etapas (tabela)
        4. Verifica comentarios para detectar termos SEFAZ
        5. Fecha modal
        """
        resultado = ResultadoConsulta(awb=awb)

        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()
            time.sleep(1)

            # Digita AWB no campo de pesquisa rapida
            campo = self.wait.until(
                EC.visibility_of_element_located((By.ID, "quickSearch"))
            )
            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)
            campo.send_keys(awb)

            # Clica no icone alvo (quickTracking)
            botao = self.wait.until(
                EC.element_to_be_clickable((By.ID, "quickTracking-icon"))
            )
            botao.click()
            time.sleep(5)

            # Aguarda modal de rastreio abrir
            # O modal tem titulo "Rastreio" e contem o AWB
            modal_abriu = self._aguardar_modal_rastreio(awb)

            if not modal_abriu:
                resultado.status = StatusAWB.NAO_ENCONTRADO
                self.browser._fechar_modais()
                return resultado

            # Extrai dados do modal
            self._extrair_dados_modal(resultado)

            # Verifica comentarios (termos SEFAZ)
            self._verificar_comentarios(resultado)

            # Classifica status final
            self._classificar_status(resultado)

            # Fecha modal
            self._fechar_modal_rastreio()

        except Exception as e:
            resultado.status = StatusAWB.NAO_ENCONTRADO
            resultado.observacao = str(e)[:80]
            try:
                self.browser._fechar_modais()
            except Exception:
                pass

        return resultado

    def _aguardar_modal_rastreio(self, awb: str) -> bool:
        """Aguarda o modal de rastreio abrir e verificar que contem o AWB."""
        try:
            # Espera ate 10s por algum elemento do modal de rastreio
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//div[contains(@class,'modal')]//h4[contains(.,'Rastreio')]"
                    " | //div[contains(@class,'modal')]//*[contains(.,'CT-e')]"
                    " | //div[contains(@class,'modal')]//*[contains(.,'Status operacional')]"
                ))
            )
            time.sleep(2)
            return True
        except TimeoutException:
            # Tenta verificar se tem algum modal aberto mesmo sem o titulo
            try:
                modais = self.driver.find_elements(By.XPATH,
                    "//div[contains(@class,'modal')][contains(@style,'display: block')]"
                    " | //div[contains(@class,'modal') and contains(@class,'show')]"
                    " | //div[contains(@class,'modal')]//div[contains(@class,'modal-content')]"
                )
                for modal in modais:
                    if modal.is_displayed() and awb in modal.text:
                        return True
            except Exception:
                pass
            return False

    def _extrair_dados_modal(self, resultado: ResultadoConsulta):
        """Extrai todas as informacoes visiveis do modal de rastreio."""
        try:
            # Pega o texto completo do modal
            texto_modal = ""
            try:
                modal = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//div[contains(@class,'modal-body')]"
                    " | //div[contains(@class,'modal') and contains(@class,'show')]"
                )
                texto_modal = modal.text
            except Exception:
                texto_modal = self.driver.find_element(By.TAG_NAME, "body").text

            texto_upper = texto_modal.upper()

            # --- Status operacional ---
            # Procura texto apos "Status operacional:" 
            match_status = re.search(
                r'STATUS\s*OPERACIONAL[:\s]*([^\n]+)', texto_modal, re.IGNORECASE)
            if match_status:
                resultado.status_operacional = match_status.group(1).strip()
            else:
                # Tenta pegar de um elemento especifico
                try:
                    elem_status = self.driver.find_element(By.XPATH,
                        "//div[contains(@class,'modal')]//*[contains(.,'Status operacional')]"
                        "/following-sibling::*[1]"
                        " | //div[contains(@class,'modal')]//span[contains(@class,'text-success') "
                        "or contains(@class,'text-danger') or contains(@class,'text-warning')]"
                    )
                    resultado.status_operacional = elem_status.text.strip()
                except Exception:
                    pass

            # --- Servico ---
            match_servico = re.search(
                r'SERVI[CÇ]O[:\s]*([^\n]+)', texto_modal, re.IGNORECASE)
            if match_servico:
                resultado.servico = match_servico.group(1).strip()

            # --- Local de entrega ---
            match_local = re.search(
                r'LOCAL\s*DE\s*ENTREGA[:\s]*([^\n]+)', texto_modal, re.IGNORECASE)
            if match_local:
                resultado.local_entrega = match_local.group(1).strip()

            # --- Etapas da tabela (ultima etapa, local, destino, data) ---
            self._extrair_ultima_etapa(resultado)

        except Exception as e:
            logger.debug(f"Extrair dados modal erro: {e}")

    def _extrair_ultima_etapa(self, resultado: ResultadoConsulta):
        """Extrai a ultima etapa da tabela de rastreio dentro do modal."""
        try:
            # Busca todas as linhas da tabela dentro do modal
            linhas = self.driver.find_elements(By.XPATH,
                "//div[contains(@class,'modal')]//table//tbody//tr"
            )

            if not linhas:
                return

            # Pega a ultima linha com dados
            for linha in reversed(linhas):
                try:
                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if len(colunas) < 5:
                        continue

                    texto_linha = linha.text.strip()
                    if not texto_linha:
                        continue

                    # Estrutura da tabela observada:
                    # Etapa | Sigla | Acoes | Id. operacao | Local | Destino | Data | Obs | Resp | Ref
                    # Indices podem variar, vamos buscar por conteudo

                    for i, col in enumerate(colunas):
                        texto_col = col.text.strip()

                        # Sigla de 3 letras = Local ou Destino
                        if re.match(r'^[A-Z]{3}$', texto_col):
                            if not resultado.ultimo_local:
                                resultado.ultimo_local = texto_col
                            else:
                                resultado.ultimo_destino = texto_col

                        # Data
                        if re.match(r'\d{2}/\d{2}/\d{4}', texto_col):
                            resultado.data_ultima_etapa = texto_col

                        # Nome da etapa (Reserva, Embarque, Desembarque, etc.)
                        if texto_col in ("Reserva", "Embarque", "Desembarque",
                                        "Pré-despacho - Em aberto", "Corte de Carga",
                                        "Emissão Conhecimento", "Consolidado",
                                        "Entrega", "Saiu para entrega"):
                            resultado.ultima_etapa = texto_col

                    if resultado.ultimo_local or resultado.ultima_etapa:
                        break  # Encontrou dados na ultima linha

                except Exception:
                    continue

        except Exception:
            pass

    def _verificar_comentarios(self, resultado: ResultadoConsulta):
        """
        Verifica nos comentarios do AWB se tem termo SEFAZ.
        
        Na tela de rastreio tem o link "Adicionar comentarios" no canto.
        Ao clicar, abre os comentarios existentes.
        Alternativa: le o texto visivel do modal que pode ja conter comentarios.
        """
        try:
            # Primeiro verifica se o texto do modal ja tem info de termo
            texto_modal = ""
            try:
                modal = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//div[contains(@class,'modal-body')]"
                    " | //div[contains(@class,'modal') and contains(@class,'show')]"
                )
                texto_modal = modal.text
            except Exception:
                return

            # Verifica se ja tem mencion a termo no texto visivel
            if "RETIDO PELA SEFAZ" in texto_modal.upper():
                resultado.tem_comentario_retido = True
                termos = re.findall(r'TA\s*(\d{5,})', texto_modal, re.IGNORECASE)
                resultado.termos = list(set(termos))
                return

            # Se nao encontrou no texto visivel, tenta abrir comentarios
            try:
                link_comentarios = self.driver.find_element(By.XPATH,
                    "//div[contains(@class,'modal')]//a[contains(.,'coment')]"
                    " | //a[contains(.,'Adicionar coment')]"
                )
                link_comentarios.click()
                time.sleep(3)

                # Le texto dos comentarios
                texto_comentarios = self.driver.find_element(By.TAG_NAME, "body").text

                if "RETIDO PELA SEFAZ" in texto_comentarios.upper():
                    resultado.tem_comentario_retido = True
                    termos = re.findall(r'TA\s*(\d{5,})', texto_comentarios, re.IGNORECASE)
                    resultado.termos = list(set(termos))

                # Fecha popup de comentarios
                try:
                    btn_fechar = self.driver.find_element(By.XPATH,
                        "(//button[contains(.,'Fechar')])[last()]"
                    )
                    btn_fechar.click()
                    time.sleep(1)
                except Exception:
                    pass

            except Exception:
                pass  # Link de comentarios nao encontrado — OK

        except Exception:
            pass

    def _classificar_status(self, resultado: ResultadoConsulta):
        """Classifica o status final baseado nos dados extraidos."""
        status_op = resultado.status_operacional.upper()
        local_entrega = resultado.local_entrega.upper()

        # Entregue
        if "ENTREG" in status_op and "AGUARDANDO" not in status_op:
            resultado.status = StatusAWB.ENTREGUE
            return

        # Verificar se chegou em MCZ
        chegou_mcz = (resultado.ultimo_destino == "MCZ" or
                      "MCZ" in resultado.ultimo_local or
                      "DESEMBARQ" in resultado.ultima_etapa.upper())

        if not chegou_mcz and resultado.ultimo_destino and resultado.ultimo_destino != "MCZ":
            resultado.status = StatusAWB.NAO_CHEGOU
            resultado.observacao = (
                f"Em transito: {resultado.ultimo_local} > {resultado.ultimo_destino}")
            return

        # Retido
        if "RETID" in status_op:
            if resultado.tem_comentario_retido and resultado.termos:
                resultado.status = StatusAWB.RETIDO_COM_TERMO
            else:
                resultado.status = StatusAWB.RETIDO_SEM_TERMO
            return

        # Liberado
        if "LIBERAD" in status_op:
            resultado.status = StatusAWB.LIBERADO
            return

        # Aguardando entrega
        if "AGUARDANDO" in status_op:
            resultado.status = StatusAWB.AGUARDANDO_ENTREGA
            return

        # Em rota
        if "ROTA" in status_op or "SAIU" in status_op:
            resultado.status = StatusAWB.EM_ROTA_ENTREGA
            return

        # Se nao identificou claramente mas chegou, usa o status_operacional como esta
        if status_op:
            resultado.status = StatusAWB.LIBERADO
            resultado.observacao = f"Status no sistema: {resultado.status_operacional}"
        else:
            resultado.status = StatusAWB.NAO_ENCONTRADO

    def _fechar_modal_rastreio(self):
        """Fecha o modal de rastreio (clica no X ou Fechar)."""
        try:
            # Tenta o X do modal
            btn = self.driver.find_element(By.XPATH,
                "//div[contains(@class,'modal')]//button[contains(@class,'close')]"
                " | //div[contains(@class,'modal')]//button[@aria-label='Close']"
                " | //div[contains(@class,'modal')]//button[contains(.,'Fechar')]"
            )
            btn.click()
            time.sleep(1)
        except Exception:
            self.browser._fechar_modais()
