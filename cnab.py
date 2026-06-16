"""
Gerador de arquivo CNAB 400 — Cobrança Bradesco
Para: BSB PARK ADMINISTRADORA EIRELI ME
Convênio: 7867330 | Agência 0606 | Conta 165641-4 | Carteira 09
Layout: MPO Arquivos Layout 400P (Bradesco, Abril/2022)
"""

import unicodedata
from datetime import datetime, date
import pandas as pd


# ============================================================================
# CONFIGURAÇÕES FIXAS DA EMPRESA
# ============================================================================
EMPRESA_NOME = "BSB PARK ADMINISTRADORA EIRELI ME"
EMPRESA_CNPJ = "08505544000117"  # só dígitos
AGENCIA = "0606"
CONTA = "0165641"        # 7 dígitos
CONTA_DV = "4"
CARTEIRA = "09"
CONVENIO = "7867330"     # Código da Empresa no Bradesco (20 pos no header)

# Regras de cobrança (padrão para todos os boletos)
MULTA_PERCENTUAL = 2.00           # 2% após o vencimento
JUROS_MES_PERCENTUAL = 1.00       # 1% ao mês → /30 = juros/dia em R$
DESCONTO_VALOR_FIXO = 50.00       # R$ 50 até o vencimento
ESPECIE_TITULO = "01"             # DM - Duplicata Mercantil

# Campos obrigatórios em cada item de entrada de gerar_cnab()
CAMPOS_OBRIGATORIOS = [
    "num_documento", "valor", "data_vencimento", "data_emissao",
    "nome_pagador", "inscricao_pagador", "endereco", "cep",
]


# ============================================================================
# UTILITÁRIOS DE FORMATAÇÃO DE CAMPOS CNAB
# ============================================================================
def remover_acentos(texto: str) -> str:
    """Remove acentos e caracteres especiais (banco não aceita)."""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def alfa(valor, tamanho: int) -> str:
    """Campo alfanumérico: maiúsculas, sem acento, alinhado à esquerda, completa com espaços."""
    s = remover_acentos(str(valor or "")).upper()
    # remove caracteres não-ASCII restantes
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in s)
    return s[:tamanho].ljust(tamanho)


def num(valor, tamanho: int) -> str:
    """Campo numérico: alinhado à direita, completa com zeros à esquerda."""
    s = "".join(c for c in str(valor) if c.isdigit())
    return s[-tamanho:].zfill(tamanho)


def valor_centavos(valor_reais: float, tamanho: int = 13) -> str:
    """Converte valor em reais para string de centavos, completando com zeros à esquerda."""
    centavos = int(round(float(valor_reais) * 100))
    return str(centavos).zfill(tamanho)


def data_ddmmaa(dt) -> str:
    """Converte data para DDMMAA."""
    if isinstance(dt, str):
        dt = pd.to_datetime(dt, dayfirst=True).date()
    elif isinstance(dt, datetime):
        dt = dt.date()
    return dt.strftime("%d%m%y")


def mes_extenso(dt) -> str:
    """Retorna nome do mês em maiúsculas (JANEIRO, FEVEREIRO, MARCO, ...)."""
    meses = ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
             "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]
    if isinstance(dt, str):
        dt = pd.to_datetime(dt, dayfirst=True).date()
    return meses[dt.month - 1]


# ============================================================================
# CÁLCULO DO DV DO NOSSO NÚMERO (Módulo 11, base 7)
# Conforme manual Bradesco: carteira + nosso_numero (11 dígitos)
# ============================================================================
def dv_nosso_numero(carteira: str, nosso_numero: str) -> str:
    """
    Calcula o dígito verificador do Nosso Número (Módulo 11 base 7).
    Concatena CARTEIRA (2 dígitos) + NOSSO_NUMERO (11 dígitos) = 13 dígitos
    Multiplica da direita para a esquerda pelos pesos 2,3,4,5,6,7 ciclicamente.
    Soma, divide por 11. DV = 11 - resto.
    Casos especiais: resto=0 → DV='0' | DV=10 → DV='P'
    Validado contra boleto real (carteira 09, NN 60510000052, DV=2).
    """
    base = carteira.zfill(2) + nosso_numero.zfill(11)  # 13 dígitos
    pesos = [2, 3, 4, 5, 6, 7]
    soma = sum(int(d) * pesos[i % 6] for i, d in enumerate(reversed(base)))
    resto = soma % 11
    dv = 11 - resto
    if dv == 11:
        return "0"
    if dv == 10:
        return "P"
    return str(dv)


# ============================================================================
# REGISTROS DO ARQUIVO
# ============================================================================
def registro_header(num_remessa: int, data_geracao: date) -> str:
    """
    HEADER LABEL (Registro tipo 0) — 400 bytes
    """
    r = ""
    r += "0"                                          # 001: identificação registro
    r += "1"                                          # 002: identificação remessa
    r += alfa("REMESSA", 7)                           # 003-009
    r += "01"                                         # 010-011: código serviço
    r += alfa("COBRANCA", 15)                         # 012-026: literal serviço
    r += num(CONVENIO, 20)                            # 027-046: código da empresa
    r += alfa(EMPRESA_NOME, 30)                       # 047-076: nome empresa
    r += "237"                                        # 077-079: código Bradesco
    r += alfa("BRADESCO", 15)                         # 080-094: nome do banco
    r += data_geracao.strftime("%d%m%y")              # 095-100: data gravação
    r += " " * 8                                      # 101-108: branco
    r += "MX"                                         # 109-110: identificação sistema
    r += num(num_remessa, 7)                          # 111-117: nº sequencial remessa
    r += " " * 277                                    # 118-394: branco
    r += "000001"                                     # 395-400: nº sequencial registro
    assert len(r) == 400, f"Header tem {len(r)} bytes, esperado 400"
    return r


def registro_transacao(seq: int, dados: dict, config: dict | None = None) -> str:
    """
    REGISTRO DE TRANSAÇÃO TIPO 1 — 400 bytes
    Um boleto = um registro.

    `tipo_inscricao` é derivado automaticamente a partir do tamanho de
    `dados["inscricao_pagador"]` (somente dígitos): 11 dígitos -> CPF (cod "01"),
    14 dígitos -> CNPJ (cod "02").
    """
    cfg = config or {}
    multa_pct      = float(cfg.get("multa_percentual",     MULTA_PERCENTUAL))
    juros_mes_pct  = float(cfg.get("juros_mes_percentual", JUROS_MES_PERCENTUAL))
    desconto_val   = float(cfg.get("desconto_valor",        DESCONTO_VALOR_FIXO))
    abatimento_pct = float(cfg.get("abatimento_percentual", 0.0))
    mensagem_usr   = str(cfg.get("mensagem", "")).strip()

    nn = dados["nosso_numero"]                        # 11 dígitos
    dv_nn = dv_nosso_numero(CARTEIRA, nn)

    # Identificação da empresa beneficiária no banco (17 posições)
    # Conforme manual MPO Layout 400P:
    # pos 21    = "0" (zero)
    # pos 22-24 = código da carteira (3 dígitos)
    # pos 25-29 = código da agência sem dígito (5 dígitos)
    # pos 30-36 = conta-corrente (7 dígitos)
    # pos 37    = dígito da conta (1 dígito)
    ident_empresa = "0" + CARTEIRA.zfill(3) + AGENCIA.zfill(5) + CONTA.zfill(7) + CONTA_DV

    r = ""
    r += "1"                                          # 001: identificação registro
    r += " " * 19                                     # 002-020: débito automático (vazio)
    r += ident_empresa                                # 021-037: identif. empresa no banco (17 dígitos)
    r += alfa(dados["num_documento"], 25)             # 038-062: nº controle participante
    r += "000"                                        # 063-065: banco débito (zeros - sem deb. auto)
    r += "2" if multa_pct > 0 else "0"               # 066: tem multa?
    r += num(int(round(multa_pct * 100)), 4)          # 067-070: percentual multa (200 = 2,00%)
    r += num(nn, 11)                                  # 071-081: nosso número
    r += dv_nn                                        # 082: DV nosso número
    r += "0" * 10                                     # 083-092: desconto bonificação/dia
    r += "2"                                          # 093: emissão pelo cliente
    r += "N"                                          # 094: não débito automático
    r += " " * 10                                     # 095-104: identif. operação banco
    r += " "                                          # 105: rateio de crédito (não)
    r += " "                                          # 106: endereçamento aviso
    r += "  "                                         # 107-108: pagamento parcial
    r += "01"                                         # 109-110: ocorrência (01=remessa)
    r += alfa(dados["num_documento"], 10)             # 111-120: nº documento
    r += data_ddmmaa(dados["data_vencimento"])        # 121-126: data vencimento
    r += valor_centavos(dados["valor"], 13)           # 127-139: valor do título
    r += "000"                                        # 140-142: banco encarregado (zeros)
    r += "00000"                                      # 143-147: agência depositária (zeros)
    r += ESPECIE_TITULO                               # 148-149: espécie título
    r += "N"                                          # 150: identificação (sempre N)
    r += data_ddmmaa(dados["data_emissao"])           # 151-156: data emissão
    r += "00"                                         # 157-158: 1ª instrução (sem protesto)
    r += "00"                                         # 159-160: 2ª instrução

    # Juros/mora por dia
    juros_dia = round(float(dados["valor"]) * juros_mes_pct / 100 / 30, 2)
    r += valor_centavos(juros_dia, 13)                # 161-173: valor mora/dia

    # Desconto até o vencimento
    if desconto_val > 0:
        r += data_ddmmaa(dados["data_vencimento"])    # 174-179: data limite desconto
        r += valor_centavos(desconto_val, 13)         # 180-192: valor desconto
    else:
        r += "000000"                                 # 174-179: sem data
        r += "0" * 13                                 # 180-192: sem desconto

    r += "0" * 13                                     # 193-205: valor IOF

    # Abatimento (% sobre o valor do boleto)
    abatimento_val = round(float(dados["valor"]) * abatimento_pct / 100, 2)
    r += valor_centavos(abatimento_val, 13)           # 206-218: valor abatimento

    # Inscrição do pagador - tipo derivado pelo tamanho (11=CPF, 14=CNPJ)
    inscricao = "".join(c for c in str(dados["inscricao_pagador"]) if c.isdigit())
    cod_insc = "02" if len(inscricao) == 14 else "01"
    r += cod_insc                                     # 219-220: tipo inscrição
    r += num(inscricao, 14)                           # 221-234: nº inscrição
    r += alfa(dados["nome_pagador"], 40)              # 235-274: nome pagador
    r += alfa(dados["endereco"], 40)                  # 275-314: endereço

    # 1ª mensagem (12 chars) - usado para "MENSALIDADE..."
    mensagem_1 = f"MENS.EST.{mes_extenso(dados['data_vencimento'])[:3]}"
    r += alfa(mensagem_1, 12)                         # 315-326: 1ª mensagem

    r += num(dados["cep"], 5)                         # 327-331: CEP
    r += num(dados.get("cep_sufixo", "000"), 3)       # 332-334: sufixo CEP

    # 2ª mensagem (60 pos): mensagem do usuário ou "MENSALIDADE DE ESTACIONAMENTO..."
    if mensagem_usr:
        msg_2 = mensagem_usr
    else:
        msg_2 = f"MENSALIDADE DE ESTACIONAMENTO - {mes_extenso(dados['data_vencimento'])}/{pd.to_datetime(dados['data_vencimento'], dayfirst=True).year}"
    r += alfa(msg_2, 60)                              # 335-394: 2ª mensagem

    r += num(seq, 6)                                  # 395-400: nº sequencial
    assert len(r) == 400, f"Transação tem {len(r)} bytes, esperado 400"
    return r


def registro_trailer(seq: int) -> str:
    """TRAILER (Registro tipo 9) — 400 bytes"""
    r = ""
    r += "9"                                          # 001: identificação registro
    r += " " * 393                                    # 002-394: branco
    r += num(seq, 6)                                  # 395-400: nº sequencial
    assert len(r) == 400
    return r


# ============================================================================
# GERAÇÃO DO ARQUIVO
# ============================================================================
def gerar_cnab(
    boletos: list[dict],
    nosso_numero_inicial: int,
    num_remessa: int,
    data_geracao: date | None = None,
    config: dict | None = None,
) -> tuple[bytes, list[dict]]:
    """
    Gera o conteúdo de um arquivo CNAB 400 a partir de uma lista de boletos em memória.

    Cada item de `boletos` deve conter:
        num_documento, valor, data_vencimento, data_emissao,
        nome_pagador, inscricao_pagador (CPF/CNPJ só dígitos), endereco, cep
        (cep_sufixo é opcional, default "000")

    Retorna (conteudo_cnab_bytes, boletos_gerados), onde `boletos_gerados` ecoa cada
    boleto na mesma ordem, acrescido de nosso_numero, dv_nosso_numero e
    nosso_numero_completo ("NNNNNNNNNNN-D").
    """
    if not boletos:
        raise ValueError("Lista de boletos está vazia")

    for idx, item in enumerate(boletos):
        faltando = [c for c in CAMPOS_OBRIGATORIOS if c not in item or item[c] in (None, "")]
        if faltando:
            ident = item.get("num_documento", f"índice {idx}")
            raise ValueError(f"Boleto '{ident}': campos obrigatórios faltando: {faltando}")

    data_geracao = data_geracao or date.today()

    linhas = []
    boletos_gerados = []

    # Header (seq=1 fixo, vai no campo 395-400 do header)
    linhas.append(registro_header(num_remessa, data_geracao))

    nn_atual = nosso_numero_inicial
    for idx, item in enumerate(boletos):
        seq = idx + 2  # +2 porque header é o 1
        dados = dict(item)
        dados["nosso_numero"] = str(nn_atual)
        linhas.append(registro_transacao(seq, dados, config))

        dv = dv_nosso_numero(CARTEIRA, str(nn_atual))
        boletos_gerados.append({
            **item,
            "nosso_numero": nn_atual,
            "dv_nosso_numero": dv,
            "nosso_numero_completo": f"{nn_atual}-{dv}",
        })
        nn_atual += 1

    # Trailer
    seq_trailer = len(boletos) + 2
    linhas.append(registro_trailer(seq_trailer))

    # Cada registro + CRLF (delimitador conforme manual).
    # Nota: o manual menciona finalizador 1A (EOF), mas o NetEmpresa web rejeita
    # com "tamanho do registro inválido" — então gravamos apenas os registros + CRLF.
    conteudo = "\r\n".join(linhas) + "\r\n"
    return conteudo.encode("ascii", errors="replace"), boletos_gerados
