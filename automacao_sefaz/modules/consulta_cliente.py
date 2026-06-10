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
    # Dados do TADe (preenchido automaticamente quando retido com termo)
    tade_situacao: str = ""         # Ex: "Pendente (Enviar e-mail)"
    tade_valor: str = ""            # Ex: "R$ 48,28"
    tade_data: str = ""             # Ex: "09/06/2026"
    tade_ta_path: str = ""          # Caminho do PDF do Termo
    tade_dar_path: str = ""         # Caminho do PDF do DAR

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
            if self.tade_situacao:
                linhas.append(f"  Situacao: {self.tade_situacao}")
            if self.tade_valor:
                linhas.append(f"  Valor DAR: {self.tade_valor}")
            if self.tade_ta_path or self.tade_dar_path:
                linhas.append("  (PDFs do Termo e DAR baixados)")

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

            # Fecha modal do rastreio
            self._fechar_modal_rastreio()

            # Se retido com termo, consulta TADe automaticamente na SEFAZ
            if resultado.status == StatusAWB.RETIDO_COM_TERMO and resultado.termos:
                self._consultar_tade_automatico(resultado)

        except Exception as e:
            resultado.status = StatusAWB.NAO_ENCONTRADO
            resultado.observacao = str(e)[:80]
            try:
                self.browser._fechar_modais()
            except Exception:
                pass

        return resultado

    def _aguardar_modal_rastreio(self, awb: str) -> bool:
        """Aguarda o modal de rastreio abrir (id=modalContainer, display:block)."""
        try:
            # O modal do Nexlog tem id="modalContainer" e fica display:block quando aberto
            self.wait.until(
                EC.visibility_of_element_located((By.XPATH,
                    "//div[@id='modalContainer'][contains(@style,'display: block')]"
                    " | //div[@id='modalContainer']//h4[contains(.,'Rastreio')]"
                    " | //div[@id='modalContainer']//h4[contains(@class,'modal-title')]"
                ))
            )
            time.sleep(3)
            return True
        except TimeoutException:
            # Fallback: qualquer modal com display:block que contenha o AWB
            try:
                modais = self.driver.find_elements(By.XPATH,
                    "//div[contains(@class,'modal')][contains(@style,'display: block')]"
                )
                for modal in modais:
                    if modal.is_displayed():
                        texto = modal.text
                        if awb in texto or "Rastreio" in texto:
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
                    "//div[@id='modalContainer']"
                    " | //div[contains(@class,'modal')][contains(@style,'display: block')]"
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
        """
        Extrai a ultima movimentacao da tabela de rastreio.
        
        Estrutura das colunas (observada no Nexlog):
        Col 0: + (expandir)
        Col 1: Etapa (numero)
        Col 2: Sigla (DOC, MAN, DEP, CIE, FDC, LOC, CRC - sigla da ACAO, NAO aeroporto)
        Col 3: Acoes (texto da etapa: Embarque, Desembarque, etc.)
        Col 4: Id. da operacao
        Col 5: Local (aeroporto REAL: VCP, GRU, MCZ)
        Col 6: Destino (aeroporto REAL: MCZ, GRU)
        Col 7: Data da acao
        Col 8: Observacoes
        Col 9: Responsavel
        Col 10: Referencia
        Col 11: Acoes (icones)
        
        IMPORTANTE: A coluna 2 (Sigla) contem siglas de ACAO (DOC, MAN, DEP, CRC)
        que tambem sao 3 letras maiusculas mas NAO sao aeroportos!
        Por isso usamos os INDICES das colunas (pelo header) para pegar Local/Destino.
        
        Logica:
        - Descobre indice de Local/Destino pelo header da tabela
        - Pega apenas dessas colunas (ignora coluna Sigla)
        - Ultima movimentacao = ultima linha onde Local != Destino
        """
        try:
            linhas = self.driver.find_elements(By.XPATH,
                "//div[@id='modalContainer']//table//tbody//tr"
                " | //div[contains(@class,'modal')][contains(@style,'display: block')]//table//tbody//tr"
            )

            if not linhas:
                return

            # Descobre os indices das colunas pelo header da tabela
            idx_local = -1
            idx_destino = -1
            idx_acoes = -1
            idx_data = -1

            try:
                headers = self.driver.find_elements(By.XPATH,
                    "//div[@id='modalContainer']//table//thead//th"
                    " | //div[contains(@class,'modal')][contains(@style,'display: block')]//table//thead//th"
                )
                for i, th in enumerate(headers):
                    texto_th = th.text.strip().lower()
                    if texto_th == "local":
                        idx_local = i
                    elif texto_th == "destino":
                        idx_destino = i
                    elif "ações" in texto_th or "acoes" in texto_th or "ação" in texto_th:
                        # Coluna "Ações" de texto (nome da etapa), nao a de icones
                        if idx_acoes == -1:
                            idx_acoes = i
                    elif "data" in texto_th:
                        idx_data = i
            except Exception:
                pass

            # Fallback se nao encontrou pelo header
            if idx_local == -1:
                idx_local = 5
            if idx_destino == -1:
                idx_destino = 6
            if idx_acoes == -1:
                idx_acoes = 3
            if idx_data == -1:
                idx_data = 7

            # Coleta dados de TODAS as linhas
            ultima_movimentacao_real = None  # Local != Destino (ex: GRU > MCZ)
            ultima_linha_qualquer = None

            for linha in linhas:
                try:
                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if len(colunas) <= max(idx_local, idx_destino):
                        continue

                    # Extrai Local e Destino pelas posicoes CORRETAS da tabela
                    local = colunas[idx_local].text.strip() if idx_local < len(colunas) else ""
                    destino = colunas[idx_destino].text.strip() if idx_destino < len(colunas) else ""
                    data_linha = colunas[idx_data].text.strip() if idx_data < len(colunas) else ""
                    etapa_linha = colunas[idx_acoes].text.strip() if idx_acoes < len(colunas) else ""

                    # Valida que sao siglas de aeroporto (3 letras maiusculas exatas)
                    local_valido = bool(re.match(r'^[A-Z]{3}$', local))
                    destino_valido = bool(re.match(r'^[A-Z]{3}$', destino))

                    if not local_valido and not destino_valido:
                        continue

                    dados_linha = {
                        "local": local if local_valido else "",
                        "destino": destino if destino_valido else "",
                        "data": data_linha,
                        "etapa": etapa_linha,
                    }

                    ultima_linha_qualquer = dados_linha

                    # Movimentacao real = Local diferente de Destino (ambos validos)
                    if local_valido and destino_valido and local != destino:
                        ultima_movimentacao_real = dados_linha

                except Exception:
                    continue

            # Usa a ultima movimentacao real (Local != Destino)
            dados = ultima_movimentacao_real or ultima_linha_qualquer

            if dados:
                resultado.ultimo_local = dados["local"]
                resultado.ultimo_destino = dados["destino"]
                resultado.data_ultima_etapa = dados["data"]
                if dados["etapa"]:
                    resultado.ultima_etapa = dados["etapa"]

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
                    "//div[@id='modalContainer']"
                    " | //div[contains(@class,'modal')][contains(@style,'display: block')]"
                )
                texto_modal = modal.text
            except Exception:
                return

            # Verifica se ja tem mencion a termo no texto visivel
            # Formatos possiveis:
            #   "RETIDO PELA SEFAZ TA 2410845"
            #   "TA 7828273"
            #   "RETIDO TA 8388"
            #   "ta 2410845, TA 2410848"
            termos = re.findall(r'(?:TA|ta)\s*(\d{4,})', texto_modal)
            if termos:
                resultado.tem_comentario_retido = True
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
                    termos = re.findall(r'(?:TA|ta)\s*(\d{4,})', texto_comentarios)
                    resultado.termos = list(set(termos))
                else:
                    # Tenta formato mais simples: so "TA" + numeros
                    termos = re.findall(r'(?:TA|ta)\s*(\d{4,})', texto_comentarios)
                    if termos:
                        resultado.tem_comentario_retido = True
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

    def _consultar_tade_automatico(self, resultado: ResultadoConsulta):
        """
        Quando o AWB esta retido com termo, consulta o TADe na SEFAZ
        automaticamente e baixa os PDFs do Termo e DAR.
        Usa o primeiro termo encontrado nos comentarios.
        """
        try:
            numero_termo = resultado.termos[0]  # Pega o primeiro termo

            tade = ConsultaTADe(self.browser)
            dados_tade = tade.extrair_termo_e_dar(numero_termo)

            # Preenche os dados no resultado
            resultado.tade_situacao = dados_tade.get("situacao", "")
            resultado.tade_valor = dados_tade.get("valor", "")
            resultado.tade_data = dados_tade.get("data", "")
            resultado.tade_ta_path = dados_tade.get("ta_path", "")
            resultado.tade_dar_path = dados_tade.get("dar_path", "")

            # Volta para aba Nexlog
            self.browser.voltar_aba_principal()

        except Exception as e:
            logger.debug(f"Consulta TADe automatica erro: {e}")
            try:
                self.browser.voltar_aba_principal()
            except Exception:
                pass

    def _fechar_modal_rastreio(self):
        """Fecha o modal de rastreio (clica no X ou Fechar)."""
        try:
            # Tenta o X do modal (modalContainer)
            btn = self.driver.find_element(By.XPATH,
                "//div[@id='modalContainer']//button[contains(@class,'close')]"
                " | //div[@id='modalContainer']//button[@aria-label='Close']"
                " | //div[contains(@class,'modal')][contains(@style,'display: block')]"
                "//button[contains(@class,'close')]"
            )
            btn.click()
            time.sleep(1)
        except Exception:
            self.browser._fechar_modais()



class ConsultaTADe:
    """
    Consulta Termo de Apreensao (TADe) no site da SEFAZ-AL.
    Extrai PDF do Termo (TA) e do DAR para enviar ao cliente.
    
    Fluxo:
    1. Acessa SEFAZ > Consultar TADe
    2. Digita numero do termo no campo "No do TADe"
    3. Clica "Consultar"
    4. Na tabela de resultado:
       - Coluna TA: clica icone impressora -> abre PDF em nova aba
       - Coluna DAR: clica icone lupa -> abre modal "Itens Infracao TA"
         -> clica "Imprimir DAR" -> abre PDF em nova aba
    5. Captura as novas abas e salva os PDFs
    """

    def __init__(self, browser):
        from modules.sefaz import SefazConsulta
        self.browser = browser
        self.driver = browser.driver
        self.wait = browser.wait
        self.sefaz = SefazConsulta(self.driver)

    def extrair_termo_e_dar(self, numero_termo: str) -> dict:
        """
        Consulta um TADe na SEFAZ e extrai os PDFs do Termo e do DAR.
        
        Args:
            numero_termo: Numero do TADe (ex: "2426405")
            
        Returns:
            dict com:
                "ta_path": caminho do PDF do Termo (ou "")
                "dar_path": caminho do PDF do DAR (ou "")
                "situacao": situacao do termo (ex: "Pendente (Enviar e-mail)")
                "valor": valor do DAR (ex: "R$ 48,28")
                "data": data do termo
                "erro": mensagem de erro se falhou
        """
        resultado = {
            "ta_path": "",
            "dar_path": "",
            "situacao": "",
            "valor": "",
            "data": "",
            "erro": "",
        }

        try:
            # Abre SEFAZ e faz login se necessario
            self.sefaz.abrir_sefaz()
            if not self.sefaz.logado:
                self.sefaz.login()

            # Navega para Consultar TADe
            self._navegar_consultar_tade()
            time.sleep(3)

            # Preenche numero do termo
            self._preencher_numero_termo(numero_termo)
            time.sleep(1)

            # Clica Consultar
            self._clicar_consultar()
            time.sleep(5)

            # Extrai dados da tabela de resultado
            self._extrair_dados_tabela(resultado)

            # Baixa PDF do Termo (TA)
            resultado["ta_path"] = self._baixar_ta()

            # Baixa PDF do DAR
            resultado["dar_path"] = self._baixar_dar()

        except Exception as e:
            resultado["erro"] = str(e)[:100]

        # Volta para o Nexlog
        try:
            self.sefaz.voltar_para_nexlog()
        except Exception:
            pass

        return resultado

    def _navegar_consultar_tade(self):
        """Navega para a pagina Consultar TADe."""
        # Se ja esta na aba SEFAZ, tenta encontrar o botao/link
        try:
            # Tenta clicar no botao "Consultar TADe" na pagina inicial
            botao = self.driver.find_element(By.XPATH,
                "//a[contains(.,'Consultar TADe')]"
                " | //span[contains(.,'Consultar TADe')]/ancestor::a"
                " | //*[contains(text(),'Consultar TADe')]"
            )
            if botao.is_displayed():
                botao.click()
                time.sleep(3)
                return
        except Exception:
            pass

        # Fallback: URL direta
        self.driver.get("https://transportadoras.sefaz.al.gov.br/#/consultar-tade")
        time.sleep(3)

    def _preencher_numero_termo(self, numero: str):
        """Preenche o campo 'No do TADe'."""
        # Busca campo de input (label "No do TADe" -> input proximo)
        campo = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//input[contains(@placeholder,'TADe') or contains(@placeholder,'tade') "
                "or contains(@placeholder,'mero')]"
                " | //label[contains(.,'TADe')]/following::input[1]"
                " | //label[contains(.,'TADe')]/..//input"
                " | //input[@type='text' or @type='number']"
            ))
        )
        campo.click()
        campo.send_keys(Keys.CONTROL, "a")
        campo.send_keys(Keys.BACKSPACE)
        campo.send_keys(numero)
        time.sleep(0.5)

    def _clicar_consultar(self):
        """Clica no botao 'Consultar' (verde)."""
        botao = self.wait.until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[contains(.,'Consultar')]"
                " | //button[contains(@class,'btn-primary') or contains(@class,'btn-success')]"
            ))
        )
        botao.click()

    def _extrair_dados_tabela(self, resultado: dict):
        """Extrai dados da linha da tabela de resultado (situacao, valor, data)."""
        try:
            # Aguarda tabela aparecer
            self.wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//table//tbody//tr//td"
                ))
            )
            time.sleep(2)

            # Le primeira linha da tabela
            linha = self.driver.find_element(By.XPATH, "//table//tbody//tr")
            colunas = linha.find_elements(By.TAG_NAME, "td")

            if len(colunas) >= 4:
                # Colunas: No TADe | Data | Situacao | Valor | TA | DAR | Email | Liberar
                resultado["data"] = colunas[1].text.strip()
                resultado["situacao"] = colunas[2].text.strip()
                resultado["valor"] = colunas[3].text.strip()

        except Exception:
            pass

    def _baixar_ta(self) -> str:
        """
        Clica no icone de impressora na coluna TA.
        O PDF abre em nova aba. Salva via Chrome print-to-pdf.
        
        Returns:
            Caminho do PDF salvo, ou "" se falhou
        """
        import os
        from config import PASTA_DOWNLOADS

        try:
            abas_antes = set(self.driver.window_handles)

            # Clica no icone da coluna TA (impressora - primeiro icone da linha)
            # A coluna TA vem apos Valor, entao e o 5o td (indice 4)
            botao_ta = self.driver.find_element(By.XPATH,
                "//table//tbody//tr//td[5]//button"
                " | //table//tbody//tr//td[5]//a"
                " | //table//tbody//tr//td[5]//i/ancestor::button"
                " | //table//tbody//tr//td[5]//i/ancestor::a"
                " | (//table//tbody//tr//button[contains(@class,'btn')])[1]"
            )
            botao_ta.click()
            time.sleep(5)

            # Detecta nova aba
            caminho = self._salvar_pdf_nova_aba(abas_antes, "termo")
            return caminho

        except Exception as e:
            logger.debug(f"Erro ao baixar TA: {e}")
            return ""

    def _baixar_dar(self) -> str:
        """
        Clica no icone de lupa na coluna DAR.
        Abre modal "Itens Infracao TA" com checkbox + botao "Imprimir DAR".
        Clica "Imprimir DAR" -> abre PDF em nova aba.
        
        Returns:
            Caminho do PDF salvo, ou "" se falhou
        """
        try:
            abas_antes = set(self.driver.window_handles)

            # Clica no icone DAR (lupa - segundo icone/botao da linha)
            # Coluna DAR e a 6a (indice 5)
            botao_dar = self.driver.find_element(By.XPATH,
                "//table//tbody//tr//td[6]//button"
                " | //table//tbody//tr//td[6]//a"
                " | //table//tbody//tr//td[6]//i/ancestor::button"
                " | //table//tbody//tr//td[6]//i/ancestor::a"
                " | (//table//tbody//tr//button[contains(@class,'btn')])[2]"
            )
            botao_dar.click()
            time.sleep(4)

            # Modal "Itens Infracao TA" abre com checkboxes e botao "Imprimir DAR"
            # Aguarda o botao "Imprimir DAR" aparecer
            btn_imprimir_dar = self.wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//button[contains(.,'Imprimir DAR')]"
                    " | //a[contains(.,'Imprimir DAR')]"
                ))
            )
            time.sleep(1)

            # Clica "Imprimir DAR"
            abas_antes_dar = set(self.driver.window_handles)
            btn_imprimir_dar.click()
            time.sleep(5)

            # Detecta nova aba com o PDF do DAR
            caminho = self._salvar_pdf_nova_aba(abas_antes_dar, "dar")

            # Fecha modal (botao "Fechar")
            try:
                btn_fechar = self.driver.find_element(By.XPATH,
                    "//button[contains(.,'Fechar')]"
                )
                btn_fechar.click()
                time.sleep(1)
            except Exception:
                pass

            return caminho

        except Exception as e:
            logger.debug(f"Erro ao baixar DAR: {e}")
            # Tenta fechar modal se abriu
            try:
                btn_fechar = self.driver.find_element(By.XPATH,
                    "//button[contains(.,'Fechar')]"
                )
                btn_fechar.click()
            except Exception:
                pass
            return ""

    def _salvar_pdf_nova_aba(self, abas_antes: set, prefixo: str) -> str:
        """
        Detecta nova aba aberta (PDF), salva o conteudo como PDF
        usando Chrome DevTools Protocol (print to PDF).
        
        Args:
            abas_antes: set de window handles antes de clicar
            prefixo: "termo" ou "dar" (para nome do arquivo)
            
        Returns:
            Caminho do arquivo salvo, ou ""
        """
        import os
        import base64
        from config import PASTA_DOWNLOADS

        try:
            # Espera nova aba aparecer (max 10s)
            tempo_inicio = time.time()
            nova_aba = None

            while time.time() - tempo_inicio < 10:
                abas_atuais = set(self.driver.window_handles)
                novas = abas_atuais - abas_antes
                if novas:
                    nova_aba = list(novas)[0]
                    break
                time.sleep(0.5)

            if not nova_aba:
                return ""

            # Troca para a nova aba
            aba_anterior = self.driver.current_window_handle
            self.driver.switch_to.window(nova_aba)
            time.sleep(3)

            # Salva como PDF usando Chrome DevTools Protocol
            caminho_pdf = ""
            try:
                # Usa Page.printToPDF do Chrome DevTools
                result = self.driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "preferCSSPageSize": True,
                })
                pdf_data = base64.b64decode(result["data"])

                # Salva arquivo
                nome_arquivo = f"sefaz_{prefixo}_{int(time.time())}.pdf"
                caminho_pdf = os.path.join(str(PASTA_DOWNLOADS), nome_arquivo)
                os.makedirs(str(PASTA_DOWNLOADS), exist_ok=True)

                with open(caminho_pdf, "wb") as f:
                    f.write(pdf_data)

            except Exception:
                # Fallback: tenta pegar URL do PDF e baixar via requests
                try:
                    url_pdf = self.driver.current_url
                    if url_pdf and "pdf" in url_pdf.lower():
                        import requests
                        resp = requests.get(url_pdf, timeout=15)
                        if resp.status_code == 200:
                            nome_arquivo = f"sefaz_{prefixo}_{int(time.time())}.pdf"
                            caminho_pdf = os.path.join(str(PASTA_DOWNLOADS), nome_arquivo)
                            with open(caminho_pdf, "wb") as f:
                                f.write(resp.content)
                except Exception:
                    pass

            # Fecha a aba do PDF e volta
            try:
                self.driver.close()
            except Exception:
                pass

            self.driver.switch_to.window(aba_anterior)
            return caminho_pdf

        except Exception as e:
            logger.debug(f"Salvar PDF nova aba erro: {e}")
            # Garante que volta para aba correta
            try:
                abas = self.driver.window_handles
                if abas:
                    self.driver.switch_to.window(abas[-1])
            except Exception:
                pass
            return ""
