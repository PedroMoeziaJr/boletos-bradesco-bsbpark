import io
from datetime import date

import pandas as pd
import streamlit as st

import boleto_pdf
import cnab
import email_sender
import queries
import retorno

MESES_ABREV = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
MESES_NOME = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

st.set_page_config(page_title="Boletos Bradesco - BSB PARK", layout="wide")

COLUMN_ORDER = [
    "selecionar", "cod_cliente", "nome_cliente", "valor",
    "data_vencimento", "seu_numero", "cadastro_completo", "motivo_incompleto",
]
COLUMN_CONFIG = {
    "selecionar": st.column_config.CheckboxColumn("Selecionar"),
    "cod_cliente": st.column_config.TextColumn("Código", disabled=True),
    "nome_cliente": st.column_config.TextColumn("Nome", disabled=True),
    "valor": st.column_config.NumberColumn("Valor (R$)", min_value=0.0, step=10.0, format="%.2f"),
    "data_vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
    "seu_numero": st.column_config.TextColumn("Seu Número", disabled=True),
    "cadastro_completo": st.column_config.CheckboxColumn("Cadastro OK", disabled=True),
    "motivo_incompleto": st.column_config.TextColumn("Pendências", disabled=True),
}


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================
def label_filial(id_filial: str) -> str:
    """'01_SCS' -> 'SCS', '03_Ed_Prime' -> 'Ed Prime'"""
    return id_filial.split("_", 1)[-1].replace("_", " ")


def montar_endereco(cliente: dict) -> str:
    partes = [cliente.get("rua"), cliente.get("numero"), cliente.get("complemento"), cliente.get("bairro")]
    return ", ".join(str(p) for p in partes if p)


def validar_cadastro(cliente: dict) -> tuple[bool, str]:
    faltando = []
    cpf_digits = "".join(c for c in str(cliente.get("cpf") or "") if c.isdigit())
    if len(cpf_digits) not in (11, 14):
        faltando.append("CPF/CNPJ")
    if not cliente.get("cep"):
        faltando.append("CEP")
    if not cliente.get("rua"):
        faltando.append("Endereço")
    return (len(faltando) == 0, "; ".join(faltando))


def to_date(val) -> date:
    if isinstance(val, date):
        return val
    return pd.Timestamp(val).date()


def carregar_clientes(competencia: date, mes_abrev: str, vencimento_default: date) -> None:
    clientes = queries.listar_mensalistas_ativos()
    cods = [c["cod_cliente"] for c in clientes]
    valores = queries.ultimos_valores_entrada(cods)

    linhas = []
    for c in clientes:
        completo, motivo = validar_cadastro(c)
        linhas.append({
            "selecionar": False,
            "cod_cliente": c["cod_cliente"],
            "nome_cliente": c["nome_cliente"],
            "id_filial": c.get("id_filial") or "",
            "valor": valores.get(c["cod_cliente"], 0.0),
            "data_vencimento": vencimento_default,
            "seu_numero": f"{c['cod_cliente']}{mes_abrev}".upper(),
            "cadastro_completo": completo,
            "motivo_incompleto": motivo,
        })

    df_todos = pd.DataFrame(linhas)
    filiais = sorted(df_todos["id_filial"].unique().tolist())

    st.session_state.filiais = filiais
    st.session_state.df_por_filial = {
        fil: df_todos[df_todos["id_filial"] == fil].reset_index(drop=True).copy()
        for fil in filiais
    }
    st.session_state.clientes_raw = {c["cod_cliente"]: c for c in clientes}
    st.session_state.competencia_carregada = competencia
    st.session_state.editor_version = {fil: 1 for fil in filiais}
    st.session_state.pop("cnab_bytes", None)
    st.session_state.pop("relatorio_bytes", None)


def gerar_relatorio_excel(boletos_gerados: list[dict]) -> bytes:
    df = pd.DataFrame([{
        "Código Cliente": b["cod_cliente"],
        "Nome": b["nome_pagador"],
        "Seu Número": b["num_documento"],
        "Nosso Número": b["nosso_numero_completo"],
        "Valor (R$)": b["valor"],
        "Vencimento": to_date(b["data_vencimento"]).strftime("%d/%m/%Y"),
        "Emissão": to_date(b["data_emissao"]).strftime("%d/%m/%Y"),
        "Status": "Emitido",
    } for b in boletos_gerados])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Boletos")
    return buffer.getvalue()


def executar_geracao(selecionados: pd.DataFrame, competencia: date, mes_abrev: str, ano: int, config: dict) -> None:
    boletos_input = []
    for _, row in selecionados.iterrows():
        cliente = st.session_state.clientes_raw[row["cod_cliente"]]
        inscricao = "".join(c for c in str(cliente["cpf"]) if c.isdigit())
        cep_digits = "".join(c for c in str(cliente["cep"]) if c.isdigit())
        boletos_input.append({
            "cod_cliente": row["cod_cliente"],
            "competencia": competencia.isoformat(),
            "num_documento": row["seu_numero"],
            "valor": float(row["valor"]),
            "data_vencimento": to_date(row["data_vencimento"]),
            "data_emissao": date.today(),
            "nome_pagador": cliente["nome_cliente"],
            "inscricao_pagador": inscricao,
            "endereco": montar_endereco(cliente),
            "cep": cep_digits[:5],
            "cep_sufixo": cep_digits[5:8] if len(cep_digits) == 8 else "000",
            "email": (cliente.get("email") or "").strip(),
        })

    try:
        nn_inicial = queries.reservar_nosso_numeros(len(boletos_input))
        num_remessa = queries.proxima_remessa()
        cnab_bytes, boletos_gerados = cnab.gerar_cnab(boletos_input, nn_inicial, num_remessa, config=config)
        nome_arquivo_rem = f"CB{date.today():%d%m}{num_remessa:02d}.REM"

        linhas_db = []
        for b in boletos_gerados:
            inscricao = b["inscricao_pagador"]
            tipo_inscricao = "CNPJ" if len(inscricao) == 14 else "CPF"
            linhas_db.append({
                "cod_cliente": b["cod_cliente"],
                "nome_cliente": b["nome_pagador"],
                "competencia": b["competencia"],
                "seu_numero": b["num_documento"],
                "nosso_numero": b["nosso_numero"],
                "nosso_numero_dv": b["dv_nosso_numero"],
                "carteira": cnab.CARTEIRA,
                "numero_remessa": num_remessa,
                "valor": b["valor"],
                "data_vencimento": to_date(b["data_vencimento"]).isoformat(),
                "data_emissao": to_date(b["data_emissao"]).isoformat(),
                "inscricao_pagador": inscricao,
                "tipo_inscricao": tipo_inscricao,
                "endereco_snapshot": b["endereco"],
                "cep": b["cep"],
                "arquivo_remessa": nome_arquivo_rem,
            })

        queries.inserir_boletos_emitidos(linhas_db)

        st.session_state.cnab_bytes = cnab_bytes
        st.session_state.relatorio_bytes = gerar_relatorio_excel(boletos_gerados)
        st.session_state.config_cobranca = config
        st.session_state.boletos_pdf_bytes = boleto_pdf.gerar_lote_pdf(boletos_gerados, config=config)
        st.session_state.nome_arquivo_rem = nome_arquivo_rem
        st.session_state.nome_relatorio = f"relatorio_boletos_{mes_abrev}{ano}.xlsx"
        st.session_state.nome_pdf = f"boletos_{mes_abrev}{ano}.pdf"
        st.session_state.boletos_gerados = boletos_gerados
        st.session_state.mes_ano_email = f"{MESES_NOME[mes - 1].upper()}/{ano}"
        st.session_state.pop("email_resultados", None)

        st.success(
            f"{len(boletos_gerados)} boleto(s) gerado(s) — remessa nº {num_remessa} "
            f"(Nosso Número {nn_inicial} a {nn_inicial + len(boletos_gerados) - 1})."
        )
    except Exception as exc:
        st.error(f"Erro ao gerar boletos: {exc}")


def render_tab_filial(filial: str, competencia: date, mes_abrev: str, ano: int, config: dict) -> None:
    df_fil = st.session_state.df_por_filial[filial]
    ev = st.session_state.editor_version.get(filial, 1)

    incompletos = df_fil[~df_fil["cadastro_completo"]]
    if not incompletos.empty:
        st.warning(
            "Cadastro incompleto (bloqueados): "
            + ", ".join(f"{r.cod_cliente} ({r.motivo_incompleto})" for r in incompletos.itertuples())
        )

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("Selecionar todos (cadastro OK)", key=f"sel_{filial}"):
            df_fil = df_fil.copy()
            df_fil.loc[df_fil["cadastro_completo"], "selecionar"] = True
            st.session_state.df_por_filial[filial] = df_fil
            st.session_state.editor_version[filial] = ev + 1
            st.rerun()
    with col2:
        if st.button("Limpar seleção", key=f"limpar_{filial}"):
            df_fil = df_fil.copy()
            df_fil["selecionar"] = False
            st.session_state.df_por_filial[filial] = df_fil
            st.session_state.editor_version[filial] = ev + 1
            st.rerun()
    with col3:
        valor_bulk = st.number_input(
            "Valor para aplicar (R$)", min_value=0.0, step=10.0, format="%.2f", key=f"vbulk_{filial}"
        )
        if st.button("Aplicar valor aos selecionados", key=f"aplicar_{filial}"):
            df_fil = df_fil.copy()
            df_fil.loc[df_fil["selecionar"], "valor"] = float(valor_bulk)
            st.session_state.df_por_filial[filial] = df_fil
            st.session_state.editor_version[filial] = ev + 1
            st.rerun()

    edited_df = st.data_editor(
        df_fil,
        key=f"editor_{filial}_{ev}",
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_order=COLUMN_ORDER,
        column_config=COLUMN_CONFIG,
    )
    edited_df.loc[~edited_df["cadastro_completo"], "selecionar"] = False
    st.session_state.df_por_filial[filial] = edited_df

    selecionados = edited_df[edited_df["selecionar"]]

    enderecos_longos = [
        row["cod_cliente"]
        for _, row in selecionados.iterrows()
        if len(montar_endereco(st.session_state.clientes_raw[row["cod_cliente"]])) > 40
    ]
    if enderecos_longos:
        st.warning(f"Endereço > 40 chars (será truncado no CNAB): {', '.join(enderecos_longos)}")

    st.caption(f"{len(selecionados)} de {len(df_fil)} cliente(s) selecionado(s).")

    if st.button("Gerar boletos desta filial", key=f"gerar_{filial}", type="primary"):
        if selecionados.empty:
            st.error("Nenhum cliente selecionado.")
        else:
            executar_geracao(selecionados, competencia, mes_abrev, ano, config)


def render_tab_todas(filiais: list[str], competencia: date, mes_abrev: str, ano: int, config: dict) -> None:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Selecionar todos (cadastro OK)", key="sel_global"):
            ev = st.session_state.editor_version
            for f in filiais:
                df = st.session_state.df_por_filial[f].copy()
                df.loc[df["cadastro_completo"], "selecionar"] = True
                st.session_state.df_por_filial[f] = df
                ev[f] = ev.get(f, 1) + 1
            st.session_state.editor_version = ev
            st.rerun()
    with col2:
        if st.button("Limpar toda a seleção", key="limpar_global"):
            ev = st.session_state.editor_version
            for f in filiais:
                df = st.session_state.df_por_filial[f].copy()
                df["selecionar"] = False
                st.session_state.df_por_filial[f] = df
                ev[f] = ev.get(f, 1) + 1
            st.session_state.editor_version = ev
            st.rerun()

    # Resumo por filial
    resumo = []
    for f in filiais:
        df_f = st.session_state.df_por_filial[f]
        sel = df_f["selecionar"].sum()
        resumo.append({
            "Filial": label_filial(f),
            "Clientes": len(df_f),
            "Selecionados": int(sel),
            "Valor total (R$)": round(df_f.loc[df_f["selecionar"], "valor"].sum(), 2),
        })
    st.dataframe(pd.DataFrame(resumo), hide_index=True, use_container_width=True)

    total_sel = sum(r["Selecionados"] for r in resumo)
    st.caption(f"Total selecionado: **{total_sel}** clientes em todas as filiais.")

    if st.button("Gerar todos os selecionados", key="gerar_todos", type="primary"):
        todos_sel = pd.concat(
            [st.session_state.df_por_filial[f][st.session_state.df_por_filial[f]["selecionar"]] for f in filiais],
            ignore_index=True,
        )
        if todos_sel.empty:
            st.error("Nenhum cliente selecionado em nenhuma filial.")
        else:
            executar_geracao(todos_sel, competencia, mes_abrev, ano, config)


# ============================================================================
# SIDEBAR
# ============================================================================
with st.sidebar:
    st.markdown("### Sequência CNAB (carteira 09)")
    seq = queries.obter_sequencia("09")
    st.caption(
        f"Próximo Nosso Número: **{seq['ultimo_nosso_numero'] + 1}**  \n"
        f"Próxima remessa: **{seq['ultimo_numero_remessa'] + 1}**"
    )
    with st.expander("Ajustar sequência (confirmar com o Bradesco antes do 1º uso)"):
        novo_nn = st.number_input(
            "Último Nosso Número usado", min_value=0, step=1, value=int(seq["ultimo_nosso_numero"])
        )
        nova_remessa = st.number_input(
            "Último número de remessa usado", min_value=0, step=1, value=int(seq["ultimo_numero_remessa"])
        )
        if st.button("Salvar sequência"):
            queries.definir_sequencia("09", int(novo_nn), int(nova_remessa))
            st.success("Sequência atualizada.")
            st.rerun()


# ============================================================================
# ABAS PRINCIPAIS
# ============================================================================
tab_gerar, tab_email, tab_retorno, tab_historico = st.tabs([
    "Gerar Boletos", "Enviar E-mails", "Retorno Bradesco", "Histórico"
])

with tab_gerar:
    st.title("Geração de Boletos CNAB 400 — BSB PARK")

    # --- Competência ---
    st.subheader("1. Competência")
    col_mes, col_ano = st.columns(2)
    with col_mes:
        mes = st.selectbox(
            "Mês de cobrança",
            options=list(range(1, 13)),
            format_func=lambda m: MESES_NOME[m - 1],
            index=date.today().month - 1,
        )
    with col_ano:
        ano = st.number_input("Ano", min_value=2024, max_value=2100, value=date.today().year, step=1)

    competencia = date(int(ano), int(mes), 1)
    mes_abrev = MESES_ABREV[mes - 1]
    vencimento_default = date(int(ano), int(mes), 5)

    if st.button("Carregar mensalistas ativos") or "df_por_filial" not in st.session_state:
        carregar_clientes(competencia, mes_abrev, vencimento_default)

    if st.session_state.get("competencia_carregada") != competencia:
        st.info("A competência mudou. Clique em 'Carregar mensalistas ativos' para atualizar.")

    # --- Configurações da cobrança ---
    st.subheader("2. Configurações da cobrança")
    col_m, col_j, col_ab, col_desc = st.columns(4)
    with col_m:
        multa_pct = st.number_input(
            "Multa após vencto (%)", min_value=0.0, max_value=100.0,
            value=2.0, step=0.5, format="%.2f",
        )
    with col_j:
        juros_pct = st.number_input(
            "Juros ao mês (%)", min_value=0.0, max_value=100.0,
            value=1.0, step=0.1, format="%.2f",
        )
    with col_ab:
        abatimento_pct = st.number_input(
            "Abatimento (%)", min_value=0.0, max_value=100.0,
            value=0.0, step=0.5, format="%.2f",
        )
    with col_desc:
        desconto_val = st.number_input(
            "Desconto até vcto (R$)", min_value=0.0,
            value=50.0, step=5.0, format="%.2f",
        )
    mensagem_extra = st.text_area(
        "Mensagem adicional (opcional — max 400 chars)", value="",
        max_chars=400, height=80,
    )
    config_cobranca = {
        "multa_percentual":     multa_pct,
        "juros_mes_percentual": juros_pct,
        "abatimento_percentual": abatimento_pct,
        "desconto_valor":       desconto_val,
        "mensagem":             mensagem_extra,
    }

    # --- Seleção por filial ---
    st.subheader("3. Selecionar clientes e ajustar valor/vencimento")

    if "filiais" in st.session_state:
        filiais = st.session_state.filiais
        tab_labels = ["Todas"] + [label_filial(f) for f in filiais]
        tabs_filiais = st.tabs(tab_labels)

        with tabs_filiais[0]:
            render_tab_todas(filiais, competencia, mes_abrev, int(ano), config_cobranca)

        for i, filial in enumerate(filiais):
            with tabs_filiais[i + 1]:
                render_tab_filial(filial, competencia, mes_abrev, int(ano), config_cobranca)

    # --- Downloads (aparecem após qualquer geração) ---
    if "cnab_bytes" in st.session_state:
        st.subheader("4. Download")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.download_button(
                "📄 Arquivo CNAB (.REM)",
                data=st.session_state.cnab_bytes,
                file_name=st.session_state.nome_arquivo_rem,
                mime="text/plain",
            )
        with col_b:
            st.download_button(
                "📊 Relatório Excel (.xlsx)",
                data=st.session_state.relatorio_bytes,
                file_name=st.session_state.nome_relatorio,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with col_c:
            st.download_button(
                "🖨️ Boletos para impressão (.pdf)",
                data=st.session_state.boletos_pdf_bytes,
                file_name=st.session_state.nome_pdf,
                mime="application/pdf",
            )

    if "cnab_bytes" in st.session_state:
        st.info("Boletos gerados. Suba o arquivo .REM no Bradesco Net Empresas, aguarde o retorno de confirmação e depois use a aba **Enviar E-mails**.")


# ============================================================================
# ABA ENVIAR E-MAILS
# ============================================================================
with tab_email:
    st.title("Enviar Boletos por E-mail")
    st.caption("Envie os boletos APÓS confirmar o registro no Bradesco (arquivo retorno com ocorrência 02).")

    col_mes_e, col_ano_e = st.columns(2)
    with col_mes_e:
        mes_e = st.selectbox(
            "Competência — Mês", range(1, 13),
            format_func=lambda m: MESES_NOME[m - 1],
            index=date.today().month - 1,
            key="email_mes",
        )
    with col_ano_e:
        ano_e = st.number_input(
            "Ano", min_value=2024, max_value=2100,
            value=date.today().year, step=1, key="email_ano",
        )

    competencia_e = date(int(ano_e), int(mes_e), 1)
    mes_ano_e = f"{MESES_NOME[mes_e - 1].upper()}/{ano_e}"

    if st.button("Buscar boletos para envio", key="btn_buscar_email"):
        with st.spinner("Buscando boletos no banco de dados…"):
            boletos_e = queries.buscar_boletos_para_email(competencia_e)
        st.session_state.boletos_para_email    = boletos_e
        st.session_state.competencia_email_sel = competencia_e
        st.session_state.pop("email_resultados_v2", None)

    if (
        "boletos_para_email" in st.session_state
        and st.session_state.competencia_email_sel == competencia_e
    ):
        boletos_e  = st.session_state.boletos_para_email
        com_email_e = [b for b in boletos_e if b.get("email")]
        sem_email_e = [b for b in boletos_e if not b.get("email")]

        if not boletos_e:
            st.info("Nenhum boleto com status 'Emitido' para esta competência.")
        else:
            if sem_email_e:
                st.warning(
                    f"{len(sem_email_e)} cliente(s) sem e-mail cadastrado (não serão enviados): "
                    + ", ".join(b["cod_cliente"] for b in sem_email_e)
                )

            if not com_email_e:
                st.info("Nenhum cliente desta competência tem e-mail cadastrado.")
            else:
                if "email_resultados_v2" in st.session_state:
                    res = st.session_state.email_resultados_v2
                    df_res = pd.DataFrame([
                        {
                            "Cliente": b["cod_cliente"],
                            "Nome": b["nome_pagador"],
                            "E-mail": b["email"],
                            "Status": res.get(b["cod_cliente"], "—"),
                        }
                        for b in com_email_e
                    ])
                    enviados = sum(1 for v in res.values() if v == "Enviado")
                    erros    = sum(1 for v in res.values() if v.startswith("Erro"))
                    st.success(f"Último envio: **{enviados} enviado(s)**, **{erros} erro(s)**.")
                    st.dataframe(df_res, hide_index=True, use_container_width=True)
                    if st.button("Reenviar e-mails", key="btn_reenviar_v2"):
                        st.session_state.pop("email_resultados_v2", None)
                        st.rerun()
                else:
                    st.info(f"**{len(com_email_e)}** cliente(s) com e-mail prontos para envio.")
                    if st.button(
                        f"Enviar {len(com_email_e)} boleto(s) por e-mail",
                        type="primary",
                        key="btn_enviar_email_v2",
                    ):
                        resultados_e = {}
                        bar_e  = st.progress(0, text="Conectando ao servidor de e-mail…")
                        log_e  = st.empty()
                        cfg_e  = st.session_state.get("config_cobranca", {})
                        try:
                            server_e = email_sender.conectar()
                            for i, b in enumerate(com_email_e):
                                log_e.info(f"Enviando para {b['nome_pagador']} ({b['email']})…")
                                try:
                                    pdf_e = boleto_pdf.gerar_boleto_pdf(b, config=cfg_e)
                                    email_sender.enviar_boleto(b, pdf_e, mes_ano_e, server_e)
                                    resultados_e[b["cod_cliente"]] = "Enviado"
                                except Exception as ex:
                                    resultados_e[b["cod_cliente"]] = f"Erro: {ex}"
                                bar_e.progress(
                                    (i + 1) / len(com_email_e),
                                    text=f"{i + 1}/{len(com_email_e)} processados…",
                                )
                            server_e.quit()
                        except Exception as ex:
                            st.error(f"Falha na conexão SMTP: {ex}")
                        else:
                            st.session_state.email_resultados_v2 = resultados_e
                            log_e.empty()
                            bar_e.empty()
                            st.rerun()


# ============================================================================
# ABA RETORNO BRADESCO
# ============================================================================
with tab_retorno:
    st.title("Processar Arquivo Retorno Bradesco")
    st.caption("Baixe o arquivo retorno (.RET) no Bradesco Net Empresas e faça o upload aqui.")

    arquivo_ret = st.file_uploader(
        "Selecione o arquivo retorno (.RET ou .TXT)", type=None, key="uploader_ret"
    )

    if arquivo_ret:
        conteudo_ret = arquivo_ret.read()
        try:
            registros_ret = retorno.processar_retorno(conteudo_ret)
        except Exception as ex:
            st.error(f"Erro ao ler arquivo: {ex}")
            registros_ret = []

        if not registros_ret:
            st.warning("Nenhum registro de transação encontrado no arquivo.")
        else:
            # Resumo por ocorrência
            resumo_occ: dict[str, list] = {}
            for r in registros_ret:
                occ = f"{r['ocorrencia']} — {r['descricao']}"
                resumo_occ.setdefault(occ, []).append(r)

            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            confirmados = sum(1 for r in registros_ret if r["ocorrencia"] == "02")
            rejeitados  = sum(1 for r in registros_ret if r["ocorrencia"] == "03")
            pagos       = sum(1 for r in registros_ret if r["ocorrencia"] in {"06", "15", "17"})
            outros      = len(registros_ret) - confirmados - rejeitados - pagos
            col_s1.metric("Confirmados (02)", confirmados)
            col_s2.metric("Rejeitados (03)",  rejeitados)
            col_s3.metric("Pagos (06/15/17)", pagos)
            col_s4.metric("Outros",           outros)

            # Tabela detalhada
            df_ret = pd.DataFrame([
                {
                    "Seu Número":      r["seu_numero"],
                    "Nosso Número":    r["nosso_numero"],
                    "Ocorrência":      r["ocorrencia"],
                    "Descrição":       r["descricao"],
                    "Vencimento":      r["data_vencimento"].strftime("%d/%m/%Y") if r["data_vencimento"] else "—",
                    "Valor (R$)":      f"{r['valor_titulo']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                    "Data Crédito":    r["data_credito"].strftime("%d/%m/%Y") if r["data_credito"] else "—",
                    "Novo Status":     r["status_novo"] or "—",
                }
                for r in registros_ret
            ])
            st.dataframe(df_ret, hide_index=True, use_container_width=True)

            # Quais boletos serão atualizados
            para_atualizar = [r for r in registros_ret if r["status_novo"]]
            if not para_atualizar:
                st.info("Nenhuma alteração de status a aplicar (apenas confirmações ou ocorrências informativas).")
            else:
                st.warning(
                    f"**{len(para_atualizar)}** boleto(s) terão status atualizado no banco: "
                    + ", ".join(
                        f"{r['seu_numero']} → {r['status_novo']}"
                        for r in para_atualizar
                    )
                )
                if st.button("Aplicar atualizações no banco de dados", type="primary", key="btn_aplicar_ret"):
                    with st.spinner("Atualizando…"):
                        total_att = queries.atualizar_status_lote(registros_ret)
                    st.success(f"{total_att} boleto(s) atualizado(s) com sucesso.")


# ============================================================================
# ABA HISTÓRICO
# ============================================================================
with tab_historico:
    st.title("Histórico de boletos emitidos")

    competencias = queries.listar_competencias_distintas()
    col1, col2 = st.columns(2)
    with col1:
        filtro_competencia = st.selectbox("Competência", ["Todas"] + competencias)
    with col2:
        filtro_status = st.selectbox("Status", ["Todos", "Emitido", "Pago", "Cancelado", "Vencido"])

    comp = None if filtro_competencia == "Todas" else date.fromisoformat(filtro_competencia)
    status = None if filtro_status == "Todos" else filtro_status
    boletos = queries.listar_boletos_emitidos(comp, status)

    if boletos:
        df_hist = pd.DataFrame(boletos)

        colunas_exibir = [
            "id", "cod_cliente", "nome_cliente", "competencia", "seu_numero",
            "nosso_numero", "nosso_numero_dv", "valor", "data_vencimento",
            "status", "numero_remessa", "arquivo_remessa",
        ]
        st.dataframe(df_hist[colunas_exibir], hide_index=True, use_container_width=True)

        st.markdown("#### Ações por boleto")
        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
        with col1:
            opcoes = {
                f"#{row['id']} — {row['cod_cliente']} {row['nome_cliente']} ({row['competencia'][:7]}) [{row['status']}]": row["id"]
                for _, row in df_hist.iterrows()
            }
            sel_label = st.selectbox("Boleto", list(opcoes.keys()))
            id_sel = opcoes[sel_label]
        with col2:
            novo_status = st.selectbox("Novo status", ["Emitido", "Pago", "Cancelado", "Vencido"])
        with col3:
            st.write("")
            st.write("")
            if st.button("Atualizar status"):
                queries.atualizar_status_boleto(int(id_sel), novo_status)
                st.success("Status atualizado.")
                st.rerun()
        with col4:
            st.write("")
            st.write("")
            if st.button("Cancelar boleto", type="secondary"):
                queries.atualizar_status_boleto(int(id_sel), "Cancelado")
                st.success("Boleto cancelado.")
                st.rerun()
    else:
        st.info("Nenhum boleto emitido ainda.")
