"""
Modelos de dados para estruturar informacoes extraidas da SEFAZ.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class TipoFielDepositario(Enum):
    TRANSPORTADORA = "transportadora"
    DESTINATARIO = "destinatario"


class SituacaoTermo(Enum):
    PENDENTE = "pendente"
    REGULARIZADO = "regularizado"
    DESCONHECIDO = "desconhecido"


class StatusMDFe(Enum):
    ANALISADO_COM_PENDENCIAS = "analisado_com_pendencias"
    ANALISADO_SEM_PENDENCIAS = "analisado_sem_pendencias"
    EM_ANALISE = "em_analise"
    DESCONHECIDO = "desconhecido"


class TipoEntrega(Enum):
    RETIRA = "retira"           # Teca / Aeroporto
    DOMICILIO = "domicilio"     # Entrega Domicilio
    DESCONHECIDO = "desconhecido"


@dataclass
class TermoApreensao:
    """Representa um Termo de Apreensao/Averiguacao da SEFAZ."""
    numero: str
    situacao: SituacaoTermo = SituacaoTermo.DESCONHECIDO
    data_emissao: str = ""
    fiel_depositario_nome: str = ""
    fiel_depositario_cnpj: str = ""
    tipo_fiel: TipoFielDepositario = TipoFielDepositario.TRANSPORTADORA
    nfe: str = ""
    cte: str = ""
    awb: Optional[str] = None  # Preenchido apos consulta no Nexlog

    @property
    def deve_reter(self) -> bool:
        """Nenhum fiel depositario e liberado (regra de negocio)."""
        return True  # Todos com termo ficam retidos

    @property
    def comentario_nexlog(self) -> str:
        """Gera o texto do comentario para o Nexlog."""
        return f"RETIDO PELA SEFAZ TA {self.numero}"


@dataclass
class ConsultaMDFe:
    """Resultado de uma consulta de MDF-e no site da SEFAZ."""
    chave: str
    numero_mdfe: str = ""
    data_emissao: str = ""
    emitente: str = ""
    cnpj_emitente: str = ""
    status: StatusMDFe = StatusMDFe.DESCONHECIDO
    data_fim: str = ""
    posto: str = ""
    total_termos: int = 0
    termos: List[TermoApreensao] = field(default_factory=list)

    @property
    def ctes_retidos(self) -> List[str]:
        """Retorna lista de CTes que possuem termos (retidos)."""
        return [t.cte for t in self.termos if t.cte]

    @property
    def tem_pendencias(self) -> bool:
        return self.total_termos > 0


@dataclass
class CTeProcesado:
    """Representa um CTe ja processado no fluxo."""
    numero_cte: str
    awb: Optional[str] = None
    tipo_entrega: TipoEntrega = TipoEntrega.DESCONHECIDO
    termo: Optional[TermoApreensao] = None
    comentario_adicionado: bool = False
    liberado: bool = False

    @property
    def pode_liberar(self) -> bool:
        """So libera se NAO tem termo E e RETIRA."""
        return (
            self.termo is None
            and self.tipo_entrega == TipoEntrega.RETIRA
        )

    @property
    def motivo_nao_liberar(self) -> str:
        if self.termo is not None:
            return f"Retido - TA {self.termo.numero}"
        if self.tipo_entrega == TipoEntrega.DOMICILIO:
            return "Entrega domicilio - nao liberar"
        if self.tipo_entrega == TipoEntrega.DESCONHECIDO:
            return "Tipo de entrega nao identificado"
        return ""
