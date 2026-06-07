"""
Parser do relatorio da SEFAZ-AL.
Extrai termos de apreensao, CTes, e informacoes estruturadas
do texto gerado pela consulta de Analise MDF-e.
"""

import re
import logging
from typing import List, Optional

from models.termo import (
    TermoApreensao,
    ConsultaMDFe,
    TipoFielDepositario,
    SituacaoTermo,
    StatusMDFe,
)

logger = logging.getLogger(__name__)


def parsear_situacao(texto: str) -> SituacaoTermo:
    """Converte texto de situacao para enum."""
    texto = texto.strip().upper()
    if "PENDENTE" in texto:
        return SituacaoTermo.PENDENTE
    elif "REGULARIZ" in texto:
        return SituacaoTermo.REGULARIZADO
    return SituacaoTermo.DESCONHECIDO


def parsear_status_mdfe(texto: str) -> StatusMDFe:
    """Converte texto de status do MDF-e para enum."""
    texto = texto.strip().upper()
    if "COM PEND" in texto:
        return StatusMDFe.ANALISADO_COM_PENDENCIAS
    elif "SEM PEND" in texto:
        return StatusMDFe.ANALISADO_SEM_PENDENCIAS
    elif "EM AN" in texto:
        return StatusMDFe.EM_ANALISE
    return StatusMDFe.DESCONHECIDO


def extrair_chave_mdfe(texto: str) -> str:
    """Extrai chave do MDF-e (44 digitos) do texto."""
    match = re.search(r'\b(\d{44})\b', texto)
    return match.group(1) if match else ""


def extrair_numero_mdfe(texto: str) -> str:
    """Extrai numero do MDF-e do texto."""
    # Padrao: chave de 44 digitos seguida do numero do MDF-e
    match = re.search(r'\d{44}\s+(\d{4,7})', texto)
    if match:
        return match.group(1)
    # Tenta padrao com label
    match = re.search(r'N[º°o]\s*MDF-?E\s*(\d+)', texto, re.IGNORECASE)
    if match:
        return match.group(1)
    # Tenta padrao alternativo
    match = re.search(r'MDF-?[eE]\s*(\d{5,7})', texto)
    return match.group(1) if match else ""


def extrair_total_termos(texto: str) -> int:
    """Extrai o numero total de termos."""
    match = re.search(r'TOTAL DE TERMOS\s*(\d+)', texto, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def extrair_termos(texto: str) -> List[TermoApreensao]:
    """
    Extrai todos os termos de apreensao do texto do relatorio.
    Funciona para o formato padrao da SEFAZ-AL.
    """
    termos = []

    # Detectar secoes de tipo de fiel depositario
    # Dividir o texto em blocos por tipo
    secoes = re.split(
        r'(Transportadora Fiel Deposit[aá]rio|Destinat[aá]rio Fiel Deposit[aá]rio)',
        texto,
        flags=re.IGNORECASE
    )

    tipo_atual = TipoFielDepositario.TRANSPORTADORA

    for i, secao in enumerate(secoes):
        secao_upper = secao.strip().upper()

        # Identifica tipo da secao
        if "TRANSPORTADORA FIEL" in secao_upper:
            tipo_atual = TipoFielDepositario.TRANSPORTADORA
            continue
        elif "DESTINAT" in secao_upper and "FIEL" in secao_upper:
            tipo_atual = TipoFielDepositario.DESTINATARIO
            continue

        # Busca termos no bloco atual
        # Padrao: numero do termo (6-7 digitos), seguido de situacao, data, etc.
        padrao_termo = re.compile(
            r'(\d{6,8})\s+'          # Numero do termo
            r'(Pendente|Regularizado|Liberado)\s+'  # Situacao
            r'(\d{2}/\d{2}/\d{4})\s+'  # Data emissao
            r'(.+?)\s+'              # Fiel depositario nome
            r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\s*'  # CNPJ
            r'(?:NF-?e\s*(\d+))?\s*'  # NF-e (opcional)
            r'(?:CT-?e\s*(\d+))?',    # CT-e (opcional)
            re.IGNORECASE
        )

        for match in padrao_termo.finditer(secao):
            termo = TermoApreensao(
                numero=match.group(1),
                situacao=parsear_situacao(match.group(2)),
                data_emissao=match.group(3),
                fiel_depositario_nome=match.group(4).strip(),
                fiel_depositario_cnpj=match.group(5),
                tipo_fiel=tipo_atual,
                nfe=match.group(6) or "",
                cte=match.group(7) or "",
            )
            termos.append(termo)
            logger.debug(f"Termo encontrado: {termo.numero} - CTe: {termo.cte}")

    # Se o regex complexo nao pegou, tenta uma abordagem mais simples
    if not termos:
        termos = _extrair_termos_simples(texto)

    return termos


def _extrair_termos_simples(texto: str) -> List[TermoApreensao]:
    """
    Abordagem alternativa mais flexivel para extrair termos.
    Usa padroes separados para cada campo.
    """
    termos = []

    # Encontra todos os numeros de termo (6-8 digitos que aparecem no contexto correto)
    linhas = texto.split('\n')
    tipo_atual = TipoFielDepositario.TRANSPORTADORA

    for linha in linhas:
        linha_upper = linha.upper().strip()

        # Detecta mudanca de secao
        if "TRANSPORTADORA FIEL" in linha_upper:
            tipo_atual = TipoFielDepositario.TRANSPORTADORA
            continue
        elif "DESTINAT" in linha_upper and "FIEL" in linha_upper:
            tipo_atual = TipoFielDepositario.DESTINATARIO
            continue

        # Busca numero de termo + CTe na mesma linha ou proximas
        match_termo = re.search(r'\b(\d{7})\b', linha)
        match_cte = re.search(r'CT-?e\s*(\d+)', linha, re.IGNORECASE)
        match_nfe = re.search(r'NF-?e\s*(\d+)', linha, re.IGNORECASE)
        match_data = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
        match_situacao = re.search(r'(Pendente|Regularizado|Liberado)', linha, re.IGNORECASE)

        if match_termo and (match_cte or match_nfe):
            termo = TermoApreensao(
                numero=match_termo.group(1),
                situacao=parsear_situacao(match_situacao.group(1)) if match_situacao else SituacaoTermo.DESCONHECIDO,
                data_emissao=match_data.group(1) if match_data else "",
                tipo_fiel=tipo_atual,
                nfe=match_nfe.group(1) if match_nfe else "",
                cte=match_cte.group(1) if match_cte else "",
            )
            termos.append(termo)

    return termos


def parsear_relatorio_sefaz(texto: str) -> ConsultaMDFe:
    """
    Parseia o texto completo do relatorio da SEFAZ e retorna
    um objeto ConsultaMDFe com todos os termos extraidos.
    """
    chave = extrair_chave_mdfe(texto)
    numero_mdfe = extrair_numero_mdfe(texto)
    total_termos = extrair_total_termos(texto)
    status = parsear_status_mdfe(texto)
    termos = extrair_termos(texto)

    # Extrair data de emissao
    match_data = re.search(r'DATA DE EMISS[ÃA]O\s*(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    data_emissao = match_data.group(1) if match_data else ""

    # Extrair emitente
    match_emitente = re.search(r'EMITENTE\s+(.+)', texto)
    emitente = match_emitente.group(1).strip() if match_emitente else ""

    # Extrair CNPJ
    match_cnpj = re.search(r'CNPJ\s+([\d./-]+)', texto)
    cnpj = match_cnpj.group(1).strip() if match_cnpj else ""

    consulta = ConsultaMDFe(
        chave=chave,
        numero_mdfe=numero_mdfe,
        data_emissao=data_emissao,
        emitente=emitente,
        cnpj_emitente=cnpj,
        status=status,
        total_termos=total_termos,
        termos=termos,
    )

    logger.info(
        f"Relatorio parseado: MDF-e {numero_mdfe} | "
        f"{len(termos)} termos encontrados | "
        f"Status: {status.value}"
    )

    return consulta


def parsear_multiplos_relatorios(textos: List[str]) -> List[ConsultaMDFe]:
    """Parseia multiplos relatorios de uma vez."""
    return [parsear_relatorio_sefaz(texto) for texto in textos]
