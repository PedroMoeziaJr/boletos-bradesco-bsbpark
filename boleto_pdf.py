"""
Boleto Bradesco — BSB PARK
Layout duas seções: Recibo do Pagador (topo) + Ficha de Compensação (base)
Referência: Layout 400P Bradesco Abril/2022, carteira 09.
"""
from datetime import date
from io import BytesIO
import os

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

# ─── Constantes bancárias ─────────────────────────────────────────────────────
BANCO_COD    = "237"
AGENCIA      = "0606"
AGENCIA_DV   = "3"
CONTA        = "0165641"
CONTA_DV     = "4"
CARTEIRA     = "09"
MOEDA        = "9"

EMPRESA_NOME = "BSB PARK ADMINISTRADORA EIRELI ME"
EMPRESA_CNPJ = "08.505.544/0001-17"
EMPRESA_END1 = "Q SCS QUADRA, 2 BLOCO A, 1 SUB - ED. BRADESCO - ASA SUL"
EMPRESA_END2 = "70329-900 - BRASILIA - DF"
LOCAL_PAG    = "Pagavel Preferencialmente na rede Bradesco ou no Bradesco Expresso."

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_bsbpark.png")

_BASE_DATE    = date(1997, 10, 7)
_RESTART_DATE = date(2025, 2, 22)

W, H  = A4           # 595 × 842 pt
LM    = 12 * mm      # margem esquerda
RM    = W - 12 * mm  # borda direita
TW    = RM - LM      # largura útil  (≈186 mm)

MESES_PT = [
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
]


# ─── Cálculos bancários ───────────────────────────────────────────────────────
def _fator_vencimento(v: date) -> str:
    f = (v - _BASE_DATE).days
    if f > 9999:
        f = 1000 + (v - _RESTART_DATE).days
    return str(max(0, min(f, 9999))).zfill(4)


def _campo_livre(nosso_numero: int) -> str:
    nn = str(nosso_numero).zfill(11)
    return AGENCIA + CARTEIRA + nn + CONTA + "0"   # 4+2+11+7+1 = 25


def _dv_mod10(digits: str) -> str:
    total, peso = 0, 2
    for ch in reversed(digits):
        v = int(ch) * peso
        total += (v // 10) + (v % 10)
        peso = 1 if peso == 2 else 2
    return str((10 - total % 10) % 10)


def _dv_mod11_barcode(s: str) -> str:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    total = sum(int(ch) * pesos[i % 8] for i, ch in enumerate(reversed(s)))
    r = total % 11
    return "1" if r in (0, 1) else str(11 - r)


def calcular_barcode(nosso_numero: int, valor: float, vencimento: date) -> str:
    fator   = _fator_vencimento(vencimento)
    val_str = str(round(valor * 100)).zfill(10)
    cl      = _campo_livre(nosso_numero)
    sem_dv  = BANCO_COD + MOEDA + fator + val_str + cl
    dv      = _dv_mod11_barcode(sem_dv)
    return sem_dv[:4] + dv + sem_dv[4:]


def calcular_linha_digitavel(bc: str) -> str:
    cl   = bc[19:]
    dv_g = bc[4]
    fv   = bc[5:19]
    c1d  = bc[0:3] + bc[3] + cl[0:5]
    c1   = bc[0:3] + bc[3] + "." + cl[0:5] + _dv_mod10(c1d)
    c2d  = cl[5:15]
    c2   = c2d[:5] + "." + c2d[5:] + _dv_mod10(c2d)
    c3d  = cl[15:25]
    c3   = c3d[:5] + "." + c3d[5:] + _dv_mod10(c3d)
    return f"{c1}  {c2}  {c3}  {dv_g}  {fv}"


# ─── Formatadores ─────────────────────────────────────────────────────────────
def _fmt_valor(v: float) -> str:
    integer, decimal = f"{v:,.2f}".split(".")
    return integer.replace(",", ".") + "," + decimal


def _fmt_data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _fmt_nosso(n: int, dv: str) -> str:
    return f"{CARTEIRA}/{str(n).zfill(11)}-{dv}"


def _fmt_agencia_conta() -> str:
    return f"{AGENCIA}/{CONTA}-{CONTA_DV}"


def _fmt_doc(digits: str) -> str:
    d = "".join(ch for ch in str(digits) if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return digits


def _to_date(val) -> date:
    if isinstance(val, date):
        return val
    import pandas as pd
    return pd.Timestamp(val).date()


# ─── Primitivas gráficas ──────────────────────────────────────────────────────
def _hline(c, x1, y, x2, width=0.3):
    c.setStrokeColor(colors.black)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)


def _vline(c, x, y1, y2, width=0.3):
    c.setStrokeColor(colors.black)
    c.setLineWidth(width)
    c.line(x, y1, x, y2)


def _box_outline(c, x, y, w, h):
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.rect(x, y, w, h, stroke=1, fill=0)


def _label(c, x, y, text, size=6):
    c.setFont("Helvetica", size)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(x, y, text)
    c.setFillColor(colors.black)


def _value(c, x, y, text, size=8, bold=True):
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.setFillColor(colors.black)
    c.drawString(x, y, text)


def _value_right(c, x, y, text, size=8, bold=True):
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.setFillColor(colors.black)
    c.drawRightString(x, y, text)


def _field(c, x, y_bot, w, h, label, value, vsize=8, bold=True, right=False):
    """Campo retangular com rótulo no topo e valor no rodapé."""
    _box_outline(c, x, y_bot, w, h)
    _label(c, x + 1.5 * mm, y_bot + h - 3.8 * mm, label)
    if right:
        _value_right(c, x + w - 1.5 * mm, y_bot + 2 * mm, value, vsize, bold)
    else:
        _value(c, x + 1.5 * mm, y_bot + 2 * mm, value, vsize, bold)


# ─── Componentes de seção ─────────────────────────────────────────────────────
def _draw_header(c, y_top, linha_dig):
    """Cabeçalho: banco à esq. | linha digitável à dir."""
    h   = 12 * mm
    y   = y_top - h
    sep = LM + 44 * mm

    _box_outline(c, LM, y, TW, h)
    _vline(c, sep, y, y + h)

    c.setFillColor(colors.black)

    # Linha SUPERIOR: "bradesco" (esq.) + linha digitável (dir.) — mesma altura
    c.setFont("Helvetica-Bold", 12)
    c.drawString(LM + 2 * mm, y + 6.5 * mm, "bradesco")
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(RM - 2 * mm, y + 6.5 * mm, linha_dig)

    # Linha INFERIOR: "237-2" (esq. somente) — claramente separado
    c.setFont("Helvetica", 8)
    c.drawString(LM + 2 * mm, y + 2 * mm, "237-2")

    return y


def _draw_local_pag(c, y_top, vcto_str):
    h     = 11 * mm
    y     = y_top - h
    sep   = LM + TW * 0.74

    _field(c, LM, y, sep - LM, h, "Local de Pagamento", LOCAL_PAG, vsize=7, bold=False)
    _field(c, sep, y, RM - sep, h, "Vencimento", vcto_str, vsize=9, bold=True, right=True)
    return y


def _draw_beneficiario(c, y_top, agencia_conta):
    h        = 18 * mm
    y        = y_top - h
    sep_main = LM + TW * 0.74   # divisor box-esq / box-dir
    logo_w   = 30 * mm
    sep_logo = LM + logo_w + 3 * mm   # divisor logo / texto-empresa

    # Box esquerda (beneficiário)
    _box_outline(c, LM, y, sep_main - LM, h)
    _label(c, LM + 1.5 * mm, y + h - 3.5 * mm, "Beneficiario")

    # Logo BSB PARK no lado esquerdo da caixa
    logo_ok = False
    if os.path.exists(LOGO_PATH):
        try:
            logo_h = 10 * mm
            logo_y = y + (h - logo_h) / 2   # centraliza verticalmente
            c.drawImage(LOGO_PATH, LM + 1.5 * mm, logo_y,
                        width=logo_w, height=logo_h,
                        preserveAspectRatio=True, mask="auto")
            _vline(c, sep_logo, y, y + h)    # separador vertical
            logo_ok = True
        except Exception:
            pass

    # Texto da empresa à direita da logo
    tx  = sep_logo + 2 * mm if logo_ok else LM + 1.5 * mm
    mid = y + h / 2
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.black)
    c.drawString(tx, mid + 2.5 * mm, EMPRESA_NOME)
    c.setFont("Helvetica", 7.5)
    c.drawString(tx, mid - 2.5 * mm, f"CNPJ: {EMPRESA_CNPJ}")

    # Box direita (agência/conta)
    _field(c, sep_main, y, RM - sep_main, h,
           "Agencia/Codigo Beneficiario", agencia_conta,
           vsize=9, bold=True, right=True)
    return y


def _draw_row4(c, y_top, data_doc, num_doc, nosso_str):
    """Data doc. | Nº documento | Espécie | Aceite | Data Proces. | Nosso Número"""
    h  = 11 * mm
    y  = y_top - h
    ws = [28 * mm, 40 * mm, 20 * mm, 16 * mm, 22 * mm]
    ws.append(TW - sum(ws))

    labels = ["Data do Doc.", "No do documento",
              "Especie doc.", "Aceite", "Data Processamento", "Nosso numero"]
    values = [data_doc, num_doc, "DM", "N", data_doc, nosso_str]
    rights = [False, False, False, False, False, True]

    x = LM
    for w, lbl, val, rt in zip(ws, labels, values, rights):
        _field(c, x, y, w, h, lbl, val, right=rt)
        x += w
    return y


def _draw_row5(c, y_top, valor_str):
    """Uso do Banco | Carteira | Espécie | Quantidade | Valor/Percentual | (=) Valor"""
    h  = 11 * mm
    y  = y_top - h
    ws = [28 * mm, 40 * mm, 20 * mm, 16 * mm, 22 * mm]
    ws.append(TW - sum(ws))

    labels = ["Uso do Banco", "Carteira", "Especie",
              "Quantidade", "Valor/Percentual", "(=) Valor do documento"]
    values = ["", CARTEIRA, "R$", "", "", valor_str]
    rights = [False, False, False, False, False, True]

    x = LM
    for w, lbl, val, rt in zip(ws, labels, values, rights):
        _field(c, x, y, w, h, lbl, val, right=rt)
        x += w
    return y


def _draw_instrucoes(c, y_top, linhas):
    """Caixa de instruções (esq.) + campos financeiros (dir.)."""
    h      = 49 * mm
    y      = y_top - h
    inst_w = TW * 0.64
    side_w = TW - inst_w
    side_h = h / 5

    _box_outline(c, LM, y, inst_w, h)
    _label(c, LM + 1.5 * mm, y + h - 3.8 * mm,
           "Instrucoes (Texto de responsabilidade do beneficiario)")

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.black)
    for i, linha in enumerate(linhas):
        c.drawString(LM + 2 * mm, y + h - 10 * mm - i * 6.5 * mm, linha)

    side_labels = [
        "(-) Descontos/Abatimentos",
        "(-) Outras Deducoes",
        "(+) Mora/Multa",
        "(+) Outros Acrescimos",
        "(=) Valor Cobrado",
    ]
    sx = LM + inst_w
    for i, lbl in enumerate(side_labels):
        fy = y + h - (i + 1) * side_h
        _field(c, sx, fy, side_w, side_h, lbl, "")
    return y


def _draw_pagador(c, y_top, nome, insc, tipo_doc, end_linha1, end_linha2=""):
    h = 19 * mm
    y = y_top - h

    _box_outline(c, LM, y, TW, h)
    _label(c, LM + 1.5 * mm, y + h - 3.5 * mm, "Pagador")

    doc_fmt = _fmt_doc(insc)
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(colors.black)
    c.drawString(LM + 1.5 * mm, y + h - 8 * mm,
                 f"{nome}  |  {tipo_doc}: {doc_fmt}")
    c.setFont("Helvetica", 8)
    c.drawString(LM + 1.5 * mm, y + h - 13 * mm, end_linha1)
    if end_linha2:
        c.drawString(LM + 1.5 * mm, y + 3 * mm, end_linha2)
    return y


def _draw_beneficiario_final(c, y_top, label_secao):
    """Linha 'Beneficiário final' + label da seção (Recibo / Ficha) à direita."""
    h   = 12 * mm
    y   = y_top - h
    sep = LM + TW * 0.72

    _box_outline(c, LM, y, sep - LM, h)
    _label(c, LM + 1.5 * mm, y + h - 3.8 * mm, "Beneficiario final")
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.black)
    c.drawString(LM + 1.5 * mm, y + h - 7.5 * mm,
                 f"{EMPRESA_NOME}  |  CNPJ: {EMPRESA_CNPJ}")
    c.drawString(LM + 1.5 * mm, y + 2 * mm,
                 "SCS, QD 2, ED BRADESCO - 1 SS  -  70329-900 - BRASILIA - DF")

    _box_outline(c, sep, y, RM - sep, h)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(sep + (RM - sep) / 2, y + h / 2 - 3.5 * mm, label_secao)
    return y


def _draw_autenticacao(c, y_top):
    h = 9 * mm
    y = y_top - h
    _box_outline(c, LM, y, TW, h)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawRightString(RM - 2 * mm, y + h / 2 - 2 * mm, "Autenticacao Mecanica")
    c.setFillColor(colors.black)
    return y


def _draw_barcode(c, y_top, bc):
    """Código de barras ITF (I2of5) sem dígito verificador impresso."""
    h = 18 * mm
    y = y_top - h

    bc_draw = createBarcodeDrawing(
        "I2of5", value=bc,
        barWidth=0.9,
        barHeight=14 * mm,
        checksum=0,
        humanReadable=False,
    )
    renderPDF.draw(bc_draw, c, LM, y + 2 * mm)
    return y


def _draw_linha_corte(c, y):
    """Linha tracejada de corte com indicação textual."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.setDash([2, 3], 0)
    c.line(LM, y, RM, y)
    c.setDash()
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(W / 2, y + 1.5 * mm, "- - - Recorte aqui - - -")
    c.setFillColor(colors.black)


# ─── Montagem do boleto ───────────────────────────────────────────────────────
def _build_instrucoes(vcto: date, valor: float, config: dict) -> list[str]:
    multa_pct      = float(config.get("multa_percentual",     2.0))
    juros_mes_pct  = float(config.get("juros_mes_percentual", 1.0))
    abatimento_pct = float(config.get("abatimento_percentual", 0.0))
    desconto_val   = float(config.get("desconto_valor",        50.0))
    mensagem       = str(config.get("mensagem", "")).strip()

    mes_ano = f"{MESES_PT[vcto.month - 1]}/{vcto.year}"
    linhas  = ["** VALORES EXPRESSOS EM REAIS **"]

    if juros_mes_pct > 0:
        juros_dia = valor * juros_mes_pct / 100 / 30
        linhas.append(f"JUROS POR DIA DE ATRASO: {_fmt_valor(juros_dia)}")

    if multa_pct > 0:
        linhas.append(f"APOS {_fmt_data(vcto)} MULTA: {_fmt_valor(valor * multa_pct / 100)}")

    linhas.append(f"MENSALIDADE DE ESTACIONAMENTO - {mes_ano}")

    if abatimento_pct > 0:
        linhas.append(f"ABATIMENTO DE {abatimento_pct:.2f}% SOBRE O VALOR.")

    if desconto_val > 0:
        linhas.append(f"DESCONTO DE R$ {_fmt_valor(desconto_val)} SE PAGO ATE O VENCIMENTO.")

    if mensagem:
        linhas.append(mensagem)

    return linhas


def _draw_boleto(c, boleto: dict, config: dict | None = None) -> None:
    nn      = int(boleto["nosso_numero"])
    dv_nn   = str(boleto["dv_nosso_numero"])
    valor   = float(boleto["valor"])
    vcto    = _to_date(boleto["data_vencimento"])
    emissao = _to_date(boleto["data_emissao"])
    nome    = boleto.get("nome_pagador", "")
    insc    = boleto.get("inscricao_pagador", "")
    end     = boleto.get("endereco", "")
    num_doc = boleto.get("num_documento", "")
    cep     = boleto.get("cep", "")
    cep_sfx = boleto.get("cep_sufixo", "")
    tipo_doc = "CNPJ" if len("".join(ch for ch in str(insc) if ch.isdigit())) == 14 else "CPF"

    bc        = calcular_barcode(nn, valor, vcto)
    linha_dig = calcular_linha_digitavel(bc)

    vcto_str    = _fmt_data(vcto)
    emissao_str = _fmt_data(emissao)
    valor_str   = _fmt_valor(valor)
    nosso_str   = _fmt_nosso(nn, dv_nn)
    agencia_str = _fmt_agencia_conta()

    end_linha2 = f"CEP: {cep}-{cep_sfx}" if cep else ""
    instrucoes = _build_instrucoes(vcto, valor, config or {})

    # ── RECIBO DO PAGADOR (topo) ───────────────────────────────────────────────
    y = H - 7 * mm   # topo da seção Recibo

    y = _draw_header(c, y, linha_dig)
    y = _draw_local_pag(c, y, vcto_str)
    y = _draw_beneficiario(c, y, agencia_str)
    y = _draw_row4(c, y, emissao_str, num_doc, nosso_str)
    y = _draw_row5(c, y, valor_str)
    y = _draw_pagador(c, y, nome, insc, tipo_doc, end, end_linha2)
    y = _draw_beneficiario_final(c, y, "Recibo do Pagador")
    y = _draw_autenticacao(c, y)

    # ── LINHA DE CORTE ─────────────────────────────────────────────────────────
    y_corte = y - 4 * mm
    _draw_linha_corte(c, y_corte)
    y = y_corte - 4 * mm

    # ── FICHA DE COMPENSAÇÃO (base) ───────────────────────────────────────────
    y = _draw_header(c, y, linha_dig)
    y = _draw_local_pag(c, y, vcto_str)
    y = _draw_beneficiario(c, y, agencia_str)
    y = _draw_row4(c, y, emissao_str, num_doc, nosso_str)
    y = _draw_row5(c, y, valor_str)
    y = _draw_instrucoes(c, y, instrucoes)
    y = _draw_pagador(c, y, nome, insc, tipo_doc, end, end_linha2)
    y = _draw_beneficiario_final(c, y, "Ficha de Compensacao")
    y = _draw_barcode(c, y, bc)
    y = _draw_autenticacao(c, y)

    # ── RODAPÉ (SAC Bradesco) ──────────────────────────────────────────────────
    footer_y = y - 3 * mm
    c.setFont("Helvetica", 6.5)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawCentredString(
        W / 2, footer_y,
        "A transacao acima foi realizada por meio do Bradesco Net Empresa."
        "  |  SAC: 0800 704 8383  |  Ouvidoria: 0800 727 9933",
    )
    c.setFillColor(colors.black)


# ─── API pública ──────────────────────────────────────────────────────────────
def gerar_boleto_pdf(boleto: dict, config: dict | None = None) -> bytes:
    """Gera PDF de um único boleto."""
    buf = BytesIO()
    c   = rl_canvas.Canvas(buf, pagesize=A4)
    _draw_boleto(c, boleto, config)
    c.save()
    buf.seek(0)
    return buf.read()


def gerar_lote_pdf(boletos: list[dict], config: dict | None = None) -> bytes:
    """Gera PDF com todos os boletos, um por página."""
    buf = BytesIO()
    c   = rl_canvas.Canvas(buf, pagesize=A4)
    for i, boleto in enumerate(boletos):
        if i > 0:
            c.showPage()
        _draw_boleto(c, boleto, config)
    c.save()
    buf.seek(0)
    return buf.read()
