"""
Parser do arquivo retorno CNAB 400 Bradesco (Layout 400P).

Posições relevantes no registro tipo 1 (1-indexed per spec):
  038-062  Número controle participante (seu_numero)
  063-073  Nosso número (11 dígitos)
  074      DV nosso número
  109-110  Código de ocorrência
  111-120  Número do documento
  121-126  Data vencimento (DDMMAA)
  127-139  Valor do título (centavos)
  296-301  Data de crédito (DDMMAA)
"""

from datetime import date

OCORRENCIAS: dict[str, str] = {
    "02": "Entrada confirmada",
    "03": "Entrada rejeitada",
    "06": "Liquidação normal",
    "09": "Baixa efetuada",
    "10": "Baixa solicitada",
    "12": "Abatimento concedido",
    "13": "Abatimento cancelado",
    "14": "Vencimento alterado",
    "15": "Liquidação em cartório",
    "17": "Liquidação após baixa",
    "19": "Confirmação de instrução de protesto",
    "23": "Encaminhado a cartório",
    "24": "Retirado de cartório",
    "27": "Confirmação de alteração de dados",
    "28": "Débito de tarifas/custas",
}

_PAGO      = {"06", "15", "17"}
_BAIXADO   = {"09", "10"}
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


def processar_retorno(conteudo: bytes) -> list[dict]:
    """
    Lê o arquivo retorno e retorna lista de registros de transação.

    Cada dict contém:
      seu_numero, nosso_numero, ocorrencia, descricao,
      data_vencimento, valor_titulo, data_credito, status_novo (ou None).

    status_novo:
      "Pago"      → ocorrência 06/15/17 (liquidado)
      "Cancelado" → ocorrência 03 (rejeitado) ou 09/10 (baixado)
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
            "nosso_numero":    linha[62:73].strip(),
            "ocorrencia":      ocorrencia,
            "descricao":       descricao,
            "data_vencimento": _data(linha[120:126]),
            "valor_titulo":    _centavos(linha[126:139]),
            "data_credito":    _data(linha[295:301]),
            "status_novo":     status_novo,
        })

    return registros
