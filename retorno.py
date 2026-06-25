"""
Parser do arquivo retorno CNAB 400 Bradesco (Layout 400P, Abril/2022).

Posições confirmadas contra o manual oficial (MPO Layout 400P) e validadas
empiricamente em dois arquivos retorno reais (um rejeitado, um pago),
incluindo o dígito verificador do Nosso Número batendo com o cálculo
módulo 11 base 7 (1-indexed per spec):
  038-062  Número controle do participante (seu_numero)
  071-081  Nosso número (11 dígitos)            — igual posição da remessa
  082      DV nosso número                      — igual posição da remessa
  109-110  Código de ocorrência                 — igual posição da remessa
  111-116  Data de ocorrência no banco (DDMMAA)
  117-126  Número do documento (seu_numero, repetido)
  147-152  Data vencimento do título (DDMMAA)
  153-165  Valor do título (centavos)
  166-168  Banco cobrador
  169-173  Agência cobradora
  254-266  Valor pago (centavos)
  296-301  Data de crédito (DDMMAA)
  319-328  Motivo do código de ocorrência (10 chars, até 5 motivos de 2)
"""

from datetime import date

# Tabela oficial de ocorrências (manual MPO Layout 400P, pág. 36-37)
OCORRENCIAS: dict[str, str] = {
    "02": "Entrada confirmada",
    "03": "Entrada rejeitada",
    "06": "Liquidação normal",
    "07": "Confirmação exclusão cadastro pagador débito",
    "08": "Rejeição pedido exclusão cadastro pagador débito",
    "09": "Baixado automaticamente via arquivo",
    "10": "Baixado pelo banco",
    "11": "Em ser - título pendente",
    "12": "Abatimento concedido",
    "13": "Abatimento cancelado",
    "14": "Vencimento alterado",
    "15": "Liquidação em cartório",
    "16": "Título pago em cheque - vinculado",
    "17": "Liquidação após baixa ou título não registrado",
    "18": "Acerto de depositária",
    "19": "Confirmação recebimento instrução de protesto",
    "20": "Confirmação recebimento instrução sustação de protesto",
    "21": "Acerto do controle do participante",
    "22": "Título com pagamento cancelado",
    "23": "Entrada do título em cartório",
    "24": "Entrada rejeitada por CEP irregular",
    "25": "Confirmação recebimento instrução de protesto falimentar",
    "27": "Baixa rejeitada",
    "28": "Débito de tarifas/custas",
    "29": "Ocorrências do pagador",
    "30": "Alteração de outros dados rejeitada",
    "31": "Confirmado inclusão cadastro pagador",
    "32": "Instrução rejeitada",
    "33": "Confirmação pedido alteração outros dados",
    "34": "Retirado de cartório e manutenção em carteira",
    "35": "Cancelamento do agendamento do débito automático",
    "37": "Rejeitado inclusão cadastro pagador",
    "38": "Confirmado alteração pagador",
    "39": "Rejeitado alteração cadastro pagador",
    "40": "Estorno de pagamento",
    "55": "Sustado judicial",
    "66": "Título baixado por pagamento via Pix",
    "68": "Acerto dos dados do rateio de crédito",
    "69": "Cancelamento de rateio",
    "73": "Confirmação recebimento pedido de negativação",
    "74": "Confirmação pedido exclusão de negativação",
}

# Motivos mais comuns para Entrada Rejeitada / CEP irregular (manual pág. 39-40)
MOTIVOS: dict[str, str] = {
    "00": "Ocorrência aceita",
    "07": "Agência/Conta/Dígito inválido",
    "08": "Nosso número inválido",
    "09": "Nosso número duplicado",
    "10": "Carteira inválida",
    "16": "Data de vencimento inválida",
    "18": "Vencimento fora do prazo de operação",
    "20": "Valor do título inválido",
    "21": "Espécie do título inválida",
    "24": "Data de emissão inválida",
    "45": "Nome do pagador não informado",
    "46": "Tipo/número de inscrição do pagador inválidos",
    "47": "Endereço do pagador não informado",
    "48": "CEP inválido",
    "49": "CEP sem praça de cobrança",
    "50": "CEP irregular - banco correspondente",
    "59": "Valor/percentual da multa inválido",
    "63": "Entrada para título já cadastrado",
    "86": "Seu número inválido",
}

_PAGO      = {"06", "15", "17", "66"}
_BAIXADO   = {"09", "10", "24"}
_REJEITADO = {"03"}


def _data(s: str) -> date | None:
    s = s.strip()
    if not s or s == "000000":
        return None
    try:
        return date(2000 + int(s[4:6]), int(s[2:4]), int(s[0:2]))
    except Exception:
        return None


def _centavos(s: str) -> float:
    try:
        return int(s) / 100
    except Exception:
        return 0.0


def _descricao_motivos(s: str) -> str:
    """Decodifica até 5 motivos de 2 dígitos (posições 319-328) em texto legível."""
    codigos = [s[i:i + 2] for i in range(0, len(s), 2)]
    descritos = [MOTIVOS.get(c, c) for c in codigos if c.strip("0").strip()]
    return "; ".join(descritos)


def processar_retorno(conteudo: bytes) -> list[dict]:
    """
    Lê o arquivo retorno e retorna lista de registros de transação.

    Cada dict contém:
      seu_numero, nosso_numero, ocorrencia, descricao, motivo,
      data_vencimento, valor_titulo, valor_pago, data_credito, status_novo (ou None).

    status_novo:
      "Pago"      → ocorrência 06/15/17/66 (liquidado)
      "Cancelado" → ocorrência 03 (rejeitado) ou 09/10/24 (baixado)
      None        → ocorrência 02 (confirmação) e demais (sem alteração de status)
    """
    try:
        texto = conteudo.decode("ascii", errors="replace")
    except Exception:
        texto = conteudo.decode("latin-1", errors="replace")

    linhas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    registros: list[dict] = []

    for linha in linhas:
        if len(linha) < 400 or linha[0] != "1":
            continue

        ocorrencia = linha[108:110]
        descricao  = OCORRENCIAS.get(ocorrencia, f"Ocorrência {ocorrencia}")

        if ocorrencia in _PAGO:
            status_novo = "Pago"
        elif ocorrencia in _BAIXADO or ocorrencia in _REJEITADO:
            status_novo = "Cancelado"
        else:
            status_novo = None

        registros.append({
            "seu_numero":      linha[37:62].strip(),
            "nosso_numero":    linha[70:81].strip(),
            "ocorrencia":      ocorrencia,
            "descricao":       descricao,
            "motivo":          _descricao_motivos(linha[318:328]),
            "data_vencimento": _data(linha[146:152]),
            "valor_titulo":    _centavos(linha[152:165]),
            "valor_pago":      _centavos(linha[253:266]),
            "data_credito":    _data(linha[295:301]),
            "status_novo":     status_novo,
        })

    return registros
