"""
Envio de boletos por e-mail via Outlook / Microsoft 365 (SMTP STARTTLS).

Configurar no .env:
    EMAIL_ADDRESS  = seu_email@outlook.com   (ou @seudominio.com.br)
    EMAIL_PASSWORD = sua_senha_ou_app_password
"""
import os
import smtplib
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

_CORPO = """\
Prezado(a) {nome},

Segue em anexo o boleto referente à mensalidade de estacionamento de {mes_ano}.

  Valor:      R$ {valor}
  Vencimento: {vencimento}

Para aproveitar o desconto, efetue o pagamento até a data de vencimento.

Em caso de dúvidas, entre em contato conosco.

Atenciosamente,
BSB PARK Administradora de Estacionamentos
"""


# ─── Formatadores internos ────────────────────────────────────────────────────
def _fmt_valor(v: float) -> str:
    integer, decimal = f"{v:,.2f}".split(".")
    return integer.replace(",", ".") + "," + decimal


def _fmt_data(d) -> str:
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    import pandas as pd
    return pd.Timestamp(d).strftime("%d/%m/%Y")


# ─── Conexão ─────────────────────────────────────────────────────────────────
def conectar() -> smtplib.SMTP:
    """Abre e retorna conexão SMTP autenticada com Outlook."""
    email_from = os.environ.get("EMAIL_ADDRESS", "")
    password   = os.environ.get("EMAIL_PASSWORD", "")
    if not email_from or not password:
        raise EnvironmentError(
            "Defina EMAIL_ADDRESS e EMAIL_PASSWORD no arquivo .env."
        )
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    server.starttls()
    server.login(email_from, password)
    return server


# ─── Envio individual ─────────────────────────────────────────────────────────
def enviar_boleto(boleto: dict, pdf_bytes: bytes, mes_ano: str, server: smtplib.SMTP) -> None:
    """
    Envia o PDF de um boleto para o e-mail do cliente.
    Usa a conexão SMTP já aberta pelo chamador (não abre nem fecha a conexão).

    boleto precisa conter: email, nome_pagador, valor, data_vencimento, num_documento.
    """
    email_from = os.environ["EMAIL_ADDRESS"]
    email_to   = boleto["email"].strip()
    nome       = boleto.get("nome_pagador", "Cliente").strip().title()
    valor_fmt  = _fmt_valor(float(boleto["valor"]))
    vcto_fmt   = _fmt_data(boleto["data_vencimento"])
    num_doc    = boleto.get("num_documento", "boleto")

    msg = MIMEMultipart()
    msg["From"]    = f"BSB PARK <{email_from}>"
    msg["To"]      = email_to
    msg["Subject"] = f"BSB PARK - Boleto Mensalidade {mes_ano}"

    corpo = _CORPO.format(
        nome=nome,
        mes_ano=mes_ano,
        valor=valor_fmt,
        vencimento=vcto_fmt,
    )
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition", "attachment",
        filename=f"Boleto_{num_doc}.pdf",
    )
    msg.attach(part)

    server.sendmail(email_from, email_to, msg.as_bytes())
