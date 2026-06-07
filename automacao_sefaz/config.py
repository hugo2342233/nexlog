"""
Configuracao centralizada do projeto.
Credenciais sao carregadas de variavel de ambiente ou arquivo local criptografado.
NUNCA commitar credenciais no repositorio.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Pasta de dados do usuario
APPDATA = os.getenv("APPDATA", os.path.expanduser("~"))
PASTA_CONFIG = Path(APPDATA) / "automacao_sefaz"
PASTA_CONFIG.mkdir(parents=True, exist_ok=True)

ARQUIVO_CREDENCIAIS = PASTA_CONFIG / "credenciais.json"
ARQUIVO_LOG = PASTA_CONFIG / "execucao.log"


@dataclass
class CredenciaisNexlog:
    usuario: str = ""
    senha: str = ""
    base: str = "MCZ"


@dataclass
class CredenciaisSefaz:
    usuario: str = ""
    senha: str = ""


@dataclass
class CredenciaisOutlook:
    email: str = ""
    # Outlook web usa autenticacao do navegador (sessao)


@dataclass
class Config:
    nexlog: CredenciaisNexlog = field(default_factory=CredenciaisNexlog)
    sefaz: CredenciaisSefaz = field(default_factory=CredenciaisSefaz)
    outlook: CredenciaisOutlook = field(default_factory=CredenciaisOutlook)

    # URLs
    url_nexlog: str = "https://golcargo.gollog.com.br/account/#/login"
    url_sefaz: str = "https://transportadoras.sefaz.al.gov.br/#/"
    url_outlook: str = "https://outlook.office.com/mail/"

    # Timeouts (segundos)
    timeout_padrao: int = 20
    timeout_curto: int = 5
    timeout_longo: int = 60

    def salvar(self):
        """Salva credenciais em arquivo local (nao vai pro git)."""
        dados = {
            "nexlog": {
                "usuario": self.nexlog.usuario,
                "senha": self.nexlog.senha,
                "base": self.nexlog.base,
            },
            "sefaz": {
                "usuario": self.sefaz.usuario,
                "senha": self.sefaz.senha,
            },
            "outlook": {
                "email": self.outlook.email,
            },
        }
        with open(ARQUIVO_CREDENCIAIS, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)

    def carregar(self) -> bool:
        """Carrega credenciais do arquivo local. Retorna True se encontrou."""
        if not ARQUIVO_CREDENCIAIS.exists():
            return False
        try:
            with open(ARQUIVO_CREDENCIAIS, "r", encoding="utf-8") as f:
                dados = json.load(f)
            self.nexlog = CredenciaisNexlog(**dados.get("nexlog", {}))
            self.sefaz = CredenciaisSefaz(**dados.get("sefaz", {}))
            self.outlook = CredenciaisOutlook(**dados.get("outlook", {}))
            return True
        except (json.JSONDecodeError, TypeError):
            return False


# Instancia global
config = Config()
config.carregar()
