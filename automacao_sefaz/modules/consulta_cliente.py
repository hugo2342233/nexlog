"""
Modulo de consulta para atendimento ao cliente (WhatsApp/Telegram).
Centraliza a busca de status de AWB usando a pesquisa rapida do Nexlog.

Tudo que precisa esta na tela de rastreio (quickSearch + quickTracking):
- Ultima movimentacao (ex: GRU > MCZ)
- Status (Retido, Entregue, Aguardando entrega, Liberado, etc.)
- Comentarios (se tem "RETIDO PELA SEFAZ TA" = tem termo)

Cenarios de resposta ao cliente:
1. Nao encontrado -> "AWB nao encontrado no sistema"
2. Nao chegou em MCZ -> "Ainda nao chegou, ultima movimentacao em X"
3. Chegou, liberado -> "Disponivel para retirada"
4. Retido sem termo -> "Aguardando analise fiscal"
5. Retido com termo -> "Retido com TA XXXXX"
6. Entrega aguardando -> "Aguardando entrega / Em rota"
7. Entregue -> "Ja foi entregue"
"""

import time
import re
import logging
from dataclasses import dataclass, field
from typing import List
from enum import Enum

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)


# ========= MODELOS =========

class StatusAWB(Enum):
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
    """Resultado da consulta de um AWB."""
    awb: str = ""
    status: StatusAWB = StatusAWB.NAO_ENCONTRADO
    status_texto: str = ""          # Texto exato do status no Nexlog
    ultima_movimentacao: str = ""   # Ex: "GRU > MCZ"
    data_movimentacao: str = ""     # Ex: "09/06/2026 10:10"
    tipo_entrega: str = ""          # "RETIRA" ou "ENTREGA"
    termos: List[str] = field(default_factory=list)  # ["2410845", "2410848"]
    comentarios: str = ""           # Texto completo dos comentarios
    observacao: str = ""

    def resposta_cliente(self) -> str:
        """Gera texto pronto para copiar e colar no WhatsApp."""
        linhas = []
        linhas.append(f"AWB {self.awb}")

        if self.status == StatusAWB.NAO_ENCONTRADO:
            linhas.append("Nao encontrado no sistema.")
            return "\n".join(linhas)

        if self.ultima_movimentacao:
            linhas.append(f"Movimentacao: {self.ultima_movimentacao}")
        if self.data_movimentacao:
            linhas.append(f"Data: {self.data_movimentacao}")
        if self.tipo_entrega:
            linhas.append(f"Tipo: {self.tipo_entrega}")

        linhas.append("")

        if self.status == StatusAWB.NAO_CHEGOU:
            linhas.append("Status: AINDA NAO CHEGOU em MCZ.")
            if self.observacao:
                linhas.append(f"Ultima info: {self.observacao}")

        elif self.status == StatusAWB.LIBERADO:
            if self.tipo_entrega.upper() == "RETIRA":
                linhas.append("Status: LIBERADO - Disponivel para retirada.")
            else:
                linhas.append("Status: LIBERADO.")

        elif self.status == StatusAWB.RETIDO_SEM_TERMO:
            linhas.append("Status: RETIDO - Aguardando analise fiscal (SEFAZ).")

        elif self.status == StatusAWB.RETIDO_COM_TERMO:
            linhas.append("Status: RETIDO - Termo de Apreensao.")
            for ta in self.termos:
                linhas.append(f"  TA {ta}")

        elif self.status == StatusAWB.AGUARDANDO_ENTREGA:
            linhas.append("Status: AGUARDANDO ENTREGA.")
            linhas.append("A carga esta em MCZ, aguardando inclusao na rota.")

        elif self.status == StatusAWB.EM_ROTA_ENTREGA:
            linhas.append("Status: SAIU PARA ENTREGA.")

        elif self.status == StatusAWB.ENTREGUE:
            linhas.append("Status: ENTREGUE.")

        if self.observacao and self.status not in (StatusAWB.NAO_CHEGOU,):
            linhas.append(f"\nObs: {self.observacao}")

        return "\n".join(linhas)


# ========= CLASSE PRINCIPAL =========

class ConsultaCliente:
    """
    Consulta AWB usando a busca rapida do Nexlog (quickSearch).
    Tudo que precisa esta na tela de rastreio que abre ao buscar o AWB.
    """

    def __init__(self, browser):
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait

    def consultar_awb(self, awb: str) -> ResultadoConsulta:
        """
        Consulta completa de um AWB usando a busca rapida do Nexlog.

        Fluxo:
        1. Digita AWB no quickSearch e clica no alvo (quickTracking)
        2. Le o conteudo da tela de rastreio que abre:
           - Ultima movimentacao / rota
           - Status atual (Retido, Liberado, Entregue, etc.)
           - Comentarios (verifica se tem "RETIDO PELA SEFAZ TA")
        3. Classifica e gera resposta pro cliente

        Returns:
            ResultadoConsulta com texto pronto pra WhatsApp
        """
        resultado = ResultadoConsulta(awb=awb)

        try:
            self.browser.voltar_aba_principal()
            self.browser._fechar_modais()
            time.sleep(1)

            # Busca rapida pelo AWB (abre tela de rastreio)
            self.browser.busca_rapida(awb)
            time.sleep(4)

            # Le todo o conteudo visivel na tela/modal de rastreio
            texto_tela = self._extrair_texto_rastreio()

            if not texto_tela:
                resultado.status = StatusAWB.NAO_ENCONTRADO
                self.browser._fechar_modais()
                return resultado

            # Extrai informacoes do texto
            self._classificar_status(resultado, texto_tela)

            # Fecha a tela de rastreio
            self.browser._fechar_modais()
            time.sleep(1)

        except Exception as e:
            resultado.observacao = f"Erro: {str(e)[:80]}"
            self.browser._fechar_modais()

        return resultado

    def _extrair_texto_rastreio(self) -> str:
        """
        Extrai o texto completo da tela de rastreio aberta.
        Tenta pegar do modal ou da pagina inteira.
        """
        texto = ""

        try:
            # Tenta ler do modal aberto
            modais = self.driver.find_elements(By.XPATH,
                "//div[contains(@class,'modal') and contains(@class,'show')]"
                " | //div[contains(@class,'modal')][contains(@style,'display: block')]"
                " | //div[contains(@class,'modal')]//div[contains(@class,'modal-body')]"
            )

            for modal in modais:
                try:
                    if modal.is_displayed():
                        t = modal.text.strip()
                        if len(t) > len(texto):
                            texto = t
                except Exception:
                    continue

            # Se nao encontrou modal, pega o body inteiro (pagina de rastreio inline)
            if not texto or len(texto) < 20:
                body = self.driver.find_element(By.TAG_NAME, "body")
                texto = body.text

        except Exception:
            pass

        return texto

    def _classificar_status(self, resultado: ResultadoConsulta, texto: str):
        """
        Analisa o texto da tela de rastreio e classifica o status do AWB.
        """
        texto_upper = texto.upper()

        # --- Tipo de entrega ---
        if "RETIRA" in texto_upper:
            resultado.tipo_entrega = "RETIRA"
        elif "DOMIC" in texto_upper or "ENTREGA" in texto_upper:
            resultado.tipo_entrega = "ENTREGA"

        # --- Ultima movimentacao (rota tipo GRU > MCZ ou CGH/MCZ) ---
        match_rota = re.search(
            r'([A-Z]{3})\s*[>/\-]\s*([A-Z]{3})', texto)
        if match_rota:
            resultado.ultima_movimentacao = f"{match_rota.group(1)} > {match_rota.group(2)}"

        # --- Data mais recente ---
        datas = re.findall(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}', texto)
        if datas:
            resultado.data_movimentacao = datas[-1]

        # --- Comentarios com termos ---
        termos_encontrados = re.findall(r'TA\s*(\d{5,})', texto, re.IGNORECASE)
        if termos_encontrados:
            resultado.termos = list(set(termos_encontrados))

        # Texto de comentarios "RETIDO PELA SEFAZ"
        if "RETIDO PELA SEFAZ" in texto_upper:
            resultado.comentarios = "RETIDO PELA SEFAZ"

        # --- Classificar status principal ---

        # Verificar se chegou em MCZ
        chegou = ("MCZ" in texto_upper and (
            "RECEBID" in texto_upper or
            "CHEGAD" in texto_upper or
            "DESEMBARCAD" in texto_upper or
            "RETID" in texto_upper or
            "LIBERAD" in texto_upper or
            "ENTREG" in texto_upper
        ))

        if not chegou and "MCZ" not in texto_upper:
            resultado.status = StatusAWB.NAO_CHEGOU
            # Pega ultima linha significativa como observacao
            linhas = [l.strip() for l in texto.split("\n") if l.strip() and len(l.strip()) > 3]
            if linhas:
                resultado.observacao = linhas[-1][:100]
            return

        # Verificar status especificos (ordem importa)
        if "ENTREG" in texto_upper and "AGUARDANDO" not in texto_upper:
            resultado.status = StatusAWB.ENTREGUE

        elif "SAIU PARA" in texto_upper or "EM ROTA" in texto_upper:
            resultado.status = StatusAWB.EM_ROTA_ENTREGA

        elif "AGUARDANDO ENTREGA" in texto_upper:
            resultado.status = StatusAWB.AGUARDANDO_ENTREGA

        elif "RETID" in texto_upper:
            # Retido — verificar se tem termo
            if resultado.termos or "RETIDO PELA SEFAZ" in texto_upper:
                resultado.status = StatusAWB.RETIDO_COM_TERMO
            else:
                resultado.status = StatusAWB.RETIDO_SEM_TERMO

        elif "LIBERAD" in texto_upper:
            resultado.status = StatusAWB.LIBERADO

        else:
            # Chegou mas status nao identificado claramente
            # Se nao ta retido e nao ta entregue, considera liberado
            resultado.status = StatusAWB.LIBERADO
            resultado.observacao = "Status exato nao identificado na tela."

        # Guarda o texto do status encontrado para referencia
        for palavra in ["RETIDO", "LIBERADO", "ENTREGUE", "AGUARDANDO",
                        "SAIU PARA ENTREGA", "EM ROTA"]:
            if palavra in texto_upper:
                resultado.status_texto = palavra.capitalize()
                break
