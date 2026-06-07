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
        """Faz login no portal de transportadoras da SEFAZ-AL."""
        usuario = usuario or config.sefaz.usuario
        senha = senha or config.sefaz.senha

        if not usuario or not senha:
            raise ValueError("Credenciais da SEFAZ nao configuradas.")

        self.driver.get(config.url_sefaz)
        time.sleep(5)  # SPA precisa de mais tempo pra carregar

        try:
            # O site mostra "Realize o login para ver os servicos disponiveis"
            # O login fica no menu "Conta" no canto superior direito
            # Tenta clicar no menu/link "Conta" ou "Login"
            try:
                menu_conta = self.wait.until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//a[contains(text(),'Conta')] | "
                        "//button[contains(text(),'Conta')] | "
                        "//a[contains(text(),'Login')] | "
                        "//a[contains(text(),'login')] | "
                        "//li[contains(.,'Conta')]//a | "
                        "//*[contains(@class,'dropdown')]//a[contains(.,'Conta')]"
                    ))
                )
                menu_conta.click()
                time.sleep(2)
            except TimeoutException:
                logger.info("SEFAZ: Menu 'Conta' nao encontrado, tentando login direto")

            # Tenta clicar em "Entrar" ou "Login" no submenu
            try:
                opcao_login = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//a[contains(text(),'Entrar')] | "
                        "//a[contains(text(),'Login')] | "
                        "//button[contains(text(),'Entrar')] | "
                        "//a[contains(@href,'login')] | "
                        "//a[contains(@href,'Login')]"
                    ))
                )
                opcao_login.click()
                time.sleep(3)
            except TimeoutException:
                logger.info("SEFAZ: Opcao 'Entrar' nao encontrada, pode ja estar na tela de login")

            # Agora busca os campos de login
            # Tenta multiplas estrategias de seletores
            campo_usuario = self._encontrar_campo_usuario()
            if campo_usuario is None:
                raise RuntimeError("Nao encontrou campo de usuario/login")

            campo_usuario.clear()
            campo_usuario.send_keys(usuario)

            campo_senha = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//input[@type='password']"
                ))
            )
            campo_senha.clear()
            campo_senha.send_keys(senha)

            # Botao de login/entrar
            botao = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[@type='submit'] | "
                    "//button[contains(text(),'Entrar')] | "
                    "//button[contains(text(),'Login')] | "
                    "//button[contains(text(),'Acessar')] | "
                    "//input[@type='submit'] | "
                    "//button[contains(@class,'btn-primary')] | "
                    "//button[contains(@class,'btn') and contains(@class,'login')]"
                ))
            )
            botao.click()

            time.sleep(5)
            self._logado = True
            logger.info("SEFAZ: Login realizado com sucesso")

        except TimeoutException:
            logger.error("SEFAZ: Nao encontrou campos de login")
            raise RuntimeError("Falha no login da SEFAZ - campos nao encontrados")

    def _encontrar_campo_usuario(self):
        """
        Tenta encontrar o campo de usuario com diversas estrategias.
        Sites SPA podem ter IDs dinamicos, entao tenta varias abordagens.
        """
        seletores = [
            # Por tipo e placeholder
            "//input[contains(@placeholder,'usu') or contains(@placeholder,'Usu')]",
            "//input[contains(@placeholder,'login') or contains(@placeholder,'Login')]",
            "//input[contains(@placeholder,'CPF') or contains(@placeholder,'cpf')]",
            "//input[contains(@placeholder,'CNPJ') or contains(@placeholder,'cnpj')]",
            "//input[contains(@placeholder,'E-mail') or contains(@placeholder,'email')]",
            # Por id/name
            "//input[@id='username' or @id='login' or @id='user' or @id='email']",
            "//input[@name='username' or @name='login' or @name='user' or @name='email']",
            # Por tipo (text que NAO e senha e NAO e busca)
            "//input[@type='text' and not(contains(@placeholder,'Pesquis')) "
            "and not(contains(@placeholder,'Busca'))]",
            "//input[@type='email']",
            # Por label
            "//label[contains(text(),'usu') or contains(text(),'Usu') or "
            "contains(text(),'Login')]//following::input[1]",
            # Primeiro input visivel em formulario
            "//form//input[@type='text' or @type='email'][1]",
        ]

        for xpath in seletores:
            try:
                campo = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                if campo.is_displayed():
                    logger.info(f"SEFAZ: Campo usuario encontrado com: {xpath[:50]}")
                    return campo
            except TimeoutException:
                continue

        return None

    def navegar_consulta_analise_mdfe(self):
        """Navega ate a pagina de Consulta Analise MDF-e."""
        try:
            # Clica no menu/botao de Consulta Analise MDF-e
            botao_consulta = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//a[contains(text(),'Consulta')] | "
                    "//button[contains(text(),'Consulta')] | "
                    "//span[contains(text(),'Consulta')]"
                ))
            )
            botao_consulta.click()
            time.sleep(2)

            # Se houver submenu "Analise MDFe"
            try:
                opcao_mdfe = self.wait.until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//*[contains(text(),'An') and contains(text(),'lise MDF')] | "
                        "//*[contains(text(),'Análise MDF')]"
                    ))
                )
                opcao_mdfe.click()
                time.sleep(2)
            except TimeoutException:
                # Talvez ja esteja na pagina certa
                pass

            logger.info("SEFAZ: Na pagina de Consulta Analise MDF-e")

        except TimeoutException:
            logger.warning("SEFAZ: Tentando acesso direto a consulta")

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
