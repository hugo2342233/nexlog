"""
Modulo de consulta no site da SEFAZ-AL (transportadoras.sefaz.al.gov.br).
Realiza login, consulta Analise MDF-e por chave, e extrai relatorio.
"""

import time
import logging
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from config import config
from models.termo import ConsultaMDFe
from modules.parser import parsear_relatorio_sefaz

logger = logging.getLogger(__name__)


class SefazConsulta:
    """Gerencia consultas no site da SEFAZ-AL."""

    def __init__(self, driver: webdriver.Chrome = None):
        """
        Pode receber um driver existente ou criar um novo.
        Se criar um novo, ele e independente do Nexlog.
        """
        self._driver_proprio = driver is None
        self.driver = driver
        self.wait = None
        self._logado = False

    def iniciar(self, headless: bool = False):
        """Inicia navegador proprio se nenhum foi fornecido."""
        if self.driver is None:
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("--headless")
            options.add_argument("--start-maximized")
            self.driver = webdriver.Chrome(options=options)

        self.wait = WebDriverWait(self.driver, config.timeout_padrao)
        logger.info("SEFAZ: Navegador pronto")

    def login(self, usuario: str = None, senha: str = None):
        """
        Faz login no portal de transportadoras da SEFAZ-AL.
        
        Fluxo real do site:
        1. Pagina carrega com header "Termo de Averiguacao - TADe"
        2. No canto superior direito tem menu dropdown "Conta"
        3. Clicar em "Conta" abre dropdown com opcao "Entrar"
        4. Clicar em "Entrar" abre MODAL de autenticacao com:
           - Titulo: "Autenticacao"  (com X para fechar)
           - Campo "Usuario" (placeholder "Seu usuario")
           - Campo "Senha" (placeholder "Sua senha")
           - Checkbox "Manter-me logado"
           - Botao azul "Entrar"
        5. Apos login: mostra 3 botoes (Consultar TADe, Consulta Analise MDF-e, etc)
        """
        usuario = usuario or config.sefaz.usuario
        senha = senha or config.sefaz.senha

        if not usuario or not senha:
            raise ValueError("Credenciais da SEFAZ nao configuradas.")

        self.driver.get(config.url_sefaz)
        time.sleep(5)  # SPA precisa de mais tempo pra carregar

        try:
            # PASSO 1: Clicar no menu "Conta" (dropdown no header)
            logger.info("SEFAZ: Clicando no menu 'Conta'...")
            menu_conta = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    # Menu "Conta" com icone de usuario no header
                    "//a[contains(.,'Conta')] | "
                    "//a[contains(@class,'dropdown') and contains(.,'Conta')] | "
                    "//*[@id='navbarSupportedContent']//a[contains(.,'Conta')] | "
                    "//nav//a[contains(.,'Conta')] | "
                    "//li[contains(@class,'dropdown')]//a[contains(.,'Conta')] | "
                    "//a[@data-toggle='dropdown' and contains(.,'Conta')]"
                ))
            )
            menu_conta.click()
            time.sleep(2)

            # PASSO 2: Clicar em "Entrar" no dropdown
            logger.info("SEFAZ: Clicando em 'Entrar'...")
            opcao_entrar = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    # Link/botao "Entrar" no dropdown de Conta
                    "//a[contains(.,'Entrar')] | "
                    "//a[contains(@data-target,'login') or contains(@data-target,'Login')] | "
                    "//a[contains(@data-target,'modal')] | "
                    "//li//a[contains(.,'Entrar')] | "
                    "//div[contains(@class,'dropdown-menu')]//a[contains(.,'Entrar')]"
                ))
            )
            opcao_entrar.click()
            time.sleep(3)

            # PASSO 3: Aguardar modal de "Autenticacao" abrir
            # O modal tem titulo "Autenticacao" e campos de login
            logger.info("SEFAZ: Aguardando modal de autenticacao...")

            # Espera o modal ficar visivel
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.visibility_of_element_located((
                        By.XPATH,
                        "//div[contains(@class,'modal') and contains(@style,'display: block')] | "
                        "//div[contains(@class,'modal-dialog')] | "
                        "//div[contains(@class,'modal-content')] | "
                        "//h4[contains(.,'Autentica')] | "
                        "//h5[contains(.,'Autentica')]"
                    ))
                )
            except TimeoutException:
                logger.info("SEFAZ: Modal pode ja estar visivel, continuando...")

            # PASSO 4: Preencher campo "Usuario" (placeholder "Seu usuario")
            campo_usuario = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    # Busca pelo placeholder exato que aparece no print
                    "//input[contains(@placeholder,'usu') or contains(@placeholder,'Usu')]"
                    " | //input[contains(@placeholder,'seu usu') or contains(@placeholder,'Seu usu')]"
                    " | //div[contains(@class,'modal')]//input[@type='text']"
                    " | //div[contains(@class,'modal')]//input[not(@type='password') "
                    "and not(@type='checkbox') and not(@type='hidden')]"
                ))
            )
            campo_usuario.clear()
            campo_usuario.send_keys(usuario)
            logger.info(f"SEFAZ: Usuario preenchido")

            # PASSO 5: Preencher campo "Senha" (placeholder "Sua senha")
            campo_senha = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[@type='password']"
                    " | //input[contains(@placeholder,'senha') or contains(@placeholder,'Senha')]"
                ))
            )
            campo_senha.clear()
            campo_senha.send_keys(senha)
            logger.info("SEFAZ: Senha preenchida")

            # PASSO 6: Clicar botao "Entrar" (azul, dentro do modal)
            botao_entrar = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    # Botao azul "Entrar" dentro do modal
                    "//div[contains(@class,'modal')]//button[contains(.,'Entrar')]"
                    " | //div[contains(@class,'modal')]//button[contains(@class,'btn-primary')]"
                    " | //div[contains(@class,'modal')]//button[@type='submit']"
                    " | //button[contains(@class,'btn-primary') and contains(.,'Entrar')]"
                    " | //button[contains(.,'Entrar') and not(contains(@class,'dropdown'))]"
                ))
            )
            botao_entrar.click()

            # PASSO 7: Aguardar login ser processado
            time.sleep(5)

            # Verifica se login foi bem sucedido
            # Apos login, aparece "Voce esta logado como [usuario]"
            # e os 3 botoes de consulta
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        "//*[contains(.,'logado como')] | "
                        "//*[contains(.,'Consultar TADe')] | "
                        "//*[contains(.,'Consulta An')]"
                    ))
                )
                self._logado = True
                logger.info("SEFAZ: Login realizado com sucesso!")
            except TimeoutException:
                # Pode ter dado erro de credenciais
                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                if "incorret" in page_text.lower() or "inv" in page_text.lower():
                    raise RuntimeError("SEFAZ: Credenciais incorretas")
                # Assume que logou mesmo sem confirmar
                self._logado = True
                logger.warning("SEFAZ: Login feito mas nao confirmou tela pos-login")

        except TimeoutException as e:
            logger.error(f"SEFAZ: Timeout no login - {e}")
            raise RuntimeError(
                "Falha no login da SEFAZ.\n"
                "Verifique se o site esta acessivel e as credenciais estao corretas."
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"SEFAZ: Erro inesperado no login - {e}")
            raise RuntimeError(f"Erro no login SEFAZ: {e}")

    def navegar_consulta_analise_mdfe(self):
        """
        Navega ate a pagina de Consulta Analise MDF-e.
        Apos login, a tela mostra 3 botoes grandes azuis:
        - "Consultar TADe"
        - "Consulta Analise MDF-e"
        - "Registro de Passagem"
        
        Clica no botao "Consulta Analise MDF-e".
        """
        try:
            logger.info("SEFAZ: Clicando em 'Consulta Analise MDF-e'...")
            
            # Botao grande azul "Consulta Analise MDF-e"
            botao_mdfe = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    # Tenta pelo texto do botao
                    "//*[contains(.,'lise MDF') and (self::a or self::button or self::div)]"
                    " | //a[contains(.,'lise MDF')]"
                    " | //button[contains(.,'lise MDF')]"
                    " | //*[contains(@class,'card') or contains(@class,'btn')]"
                    "[contains(.,'MDF')]"
                    " | //div[contains(.,'Consulta') and contains(.,'MDF')]"
                    "/ancestor-or-self::a"
                ))
            )
            botao_mdfe.click()
            time.sleep(3)

            logger.info("SEFAZ: Na pagina de Consulta Analise MDF-e")

        except TimeoutException:
            # Tenta via menu se os botoes nao funcionaram
            logger.warning("SEFAZ: Botao MDF-e nao encontrado, tentando via Menu...")
            try:
                menu = self.wait.until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//a[contains(.,'Menu')] | //button[contains(.,'Menu')]"
                    ))
                )
                menu.click()
                time.sleep(1)

                opcao = self.wait.until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//a[contains(.,'MDF')] | //a[contains(.,'Análise')]"
                    ))
                )
                opcao.click()
                time.sleep(3)
            except TimeoutException:
                raise RuntimeError("SEFAZ: Nao conseguiu navegar para Consulta Analise MDF-e")

    def consultar_chave_mdfe(self, chave: str) -> Optional[ConsultaMDFe]:
        """
        Consulta uma chave de MDF-e e extrai o relatorio.

        Args:
            chave: Chave do MDF-e (44 digitos)

        Returns:
            ConsultaMDFe com os dados extraidos, ou None se falhar
        """
        if len(chave.replace(" ", "")) != 44:
            logger.warning(f"Chave invalida (deve ter 44 digitos): {chave}")

        try:
            # Localiza campo de chave
            campo_chave = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[contains(@placeholder,'have') or contains(@placeholder,'MDF') "
                    "or @id='chave' or @name='chave' or @type='text']"
                ))
            )
            campo_chave.clear()
            campo_chave.send_keys(chave)

            # Botao pesquisar
            botao_pesquisar = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(text(),'Pesquisar')] | "
                    "//button[contains(text(),'Consultar')] | "
                    "//button[@type='submit']"
                ))
            )
            botao_pesquisar.click()

            time.sleep(5)
            logger.info(f"SEFAZ: Pesquisa realizada para chave {chave[:20]}...")

            # Aguarda resultado e clica na impressora para gerar relatorio
            texto_relatorio = self._clicar_impressora_e_extrair()

            if texto_relatorio:
                resultado = parsear_relatorio_sefaz(texto_relatorio)
                # Garante que a chave esta preenchida
                if not resultado.chave:
                    resultado.chave = chave
                return resultado
            else:
                logger.warning("SEFAZ: Nao conseguiu extrair relatorio")
                return None

        except TimeoutException:
            logger.error(f"SEFAZ: Timeout na consulta da chave {chave[:20]}...")
            return None
        except Exception as e:
            logger.error(f"SEFAZ: Erro na consulta: {e}")
            return None

    def _clicar_impressora_e_extrair(self) -> str:
        """
        Clica no icone de impressora para gerar o relatorio
        e extrai o texto resultante.
        """
        try:
            # Procura icone de impressora / botao de gerar relatorio
            # Pode ser um <i>, <span>, <button> ou <a> com icone de print
            icone_impressora = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//*[contains(@class,'print') or contains(@class,'impressora') "
                    "or contains(@title,'Imprimir') or contains(@title,'print') "
                    "or contains(@class,'fa-print') or contains(@class,'icon-print')]"
                    " | //button[contains(@onclick,'print') or contains(@onclick,'relatorio')]"
                ))
            )
            icone_impressora.click()
            time.sleep(4)

            # O relatorio pode abrir em nova aba ou na mesma pagina
            # Verifica se abriu nova aba
            abas = self.driver.window_handles
            if len(abas) > 1:
                # Muda para a nova aba
                self.driver.switch_to.window(abas[-1])
                time.sleep(2)

                # Extrai texto da pagina
                texto = self.driver.find_element(By.TAG_NAME, "body").text
                
                # Fecha a aba e volta
                self.driver.close()
                self.driver.switch_to.window(abas[0])
            else:
                # Relatorio na mesma pagina - busca o conteudo
                time.sleep(2)
                texto = self.driver.find_element(By.TAG_NAME, "body").text

            return texto

        except TimeoutException:
            # Tenta extrair diretamente da pagina atual (o resultado pode ja estar visivel)
            logger.warning("SEFAZ: Impressora nao encontrada, tentando extrair da pagina")
            try:
                texto = self.driver.find_element(By.TAG_NAME, "body").text
                return texto
            except Exception:
                return ""

    def consultar_tade(self, numero_termo: str) -> str:
        """
        Consulta um Termo de Apreensao (TADe) especifico.
        Retorna o texto do resultado.
        """
        try:
            # Navega para consulta TADe se necessario
            # (implementar navegacao especifica quando testar)
            campo = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[contains(@placeholder,'ermo') or contains(@placeholder,'TAD') "
                    "or @id='termo' or @name='termo']"
                ))
            )
            campo.clear()
            campo.send_keys(numero_termo)

            botao = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(text(),'Pesquisar')] | //button[@type='submit']"
                ))
            )
            botao.click()
            time.sleep(4)

            return self.driver.find_element(By.TAG_NAME, "body").text

        except Exception as e:
            logger.error(f"SEFAZ: Erro ao consultar TADe {numero_termo}: {e}")
            return ""

    def fechar(self):
        """Fecha o navegador se for proprio."""
        if self._driver_proprio and self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            logger.info("SEFAZ: Navegador fechado")

    @property
    def logado(self) -> bool:
        return self._logado
