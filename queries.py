from datetime import date, datetime, timezone

from supabase_client import get_client


def listar_mensalistas_ativos() -> list[dict]:
    """Mensalistas ativos com os dados cadastrais necessários para o boleto."""
    client = get_client()
    resp = (
        client.table("clientes")
        .select("cod_cliente, nome_cliente, id_filial, cpf, email, cep, rua, numero, complemento, bairro")
        .eq("tipo_de_cliente", "Mensalista")
        .eq("status", "Ativo")
        .order("cod_cliente")
        .execute()
    )
    return resp.data


def ultimos_valores_entrada(cod_clientes: list[str]) -> dict[str, float]:
    """Último valor_entrada (mais recente por data_entrada) para cada cod_cliente."""
    if not cod_clientes:
        return {}
    client = get_client()
    resp = (
        client.table("entradas")
        .select("cod_cliente, valor_entrada, data_entrada")
        .in_("cod_cliente", cod_clientes)
        .order("data_entrada", desc=True)
        .execute()
    )
    valores: dict[str, float] = {}
    for row in resp.data:
        cod = row["cod_cliente"]
        if cod not in valores and row["valor_entrada"] is not None:
            valores[cod] = float(row["valor_entrada"])
    return valores


def reservar_nosso_numeros(quantidade: int, carteira: str = "09") -> int:
    """Reserva `quantidade` Nosso Número consecutivos e retorna o primeiro."""
    client = get_client()
    resp = client.rpc(
        "reservar_nosso_numeros", {"p_carteira": carteira, "p_quantidade": quantidade}
    ).execute()
    return resp.data


def proxima_remessa(carteira: str = "09") -> int:
    """Reserva e retorna o próximo número de remessa."""
    client = get_client()
    resp = client.rpc("proxima_remessa", {"p_carteira": carteira}).execute()
    return resp.data


def inserir_boletos_emitidos(linhas: list[dict]) -> None:
    client = get_client()
    client.table("boletos_emitidos").insert(linhas).execute()


def listar_boletos_emitidos(
    competencia: date | None = None, status: str | None = None
) -> list[dict]:
    client = get_client()
    query = client.table("boletos_emitidos").select("*").order("criado_em", desc=True)
    if competencia is not None:
        query = query.eq("competencia", competencia.isoformat())
    if status is not None:
        query = query.eq("status", status)
    return query.execute().data


def listar_competencias_distintas() -> list[str]:
    client = get_client()
    resp = client.table("boletos_emitidos").select("competencia").execute()
    return sorted({row["competencia"] for row in resp.data}, reverse=True)


def atualizar_status_boleto(id_boleto: int, novo_status: str) -> None:
    client = get_client()
    client.table("boletos_emitidos").update(
        {"status": novo_status, "atualizado_em": datetime.now(timezone.utc).isoformat()}
    ).eq("id", id_boleto).execute()


def buscar_boletos_para_email(competencia: date) -> list[dict]:
    """
    Boletos com status 'Emitido' para a competência, prontos para gerar PDF e enviar.
    Enriquece com e-mail atual do cadastro do cliente.
    """
    client = get_client()
    boletos = (
        client.table("boletos_emitidos")
        .select("*")
        .eq("competencia", competencia.isoformat())
        .eq("status", "Emitido")
        .order("cod_cliente")
        .execute()
        .data or []
    )
    if not boletos:
        return []

    cods = [b["cod_cliente"] for b in boletos]
    email_map = {
        c["cod_cliente"]: (c.get("email") or "").strip()
        for c in (
            client.table("clientes")
            .select("cod_cliente, email")
            .in_("cod_cliente", cods)
            .execute()
            .data or []
        )
    }

    result = []
    for b in boletos:
        nn = b["nosso_numero"]
        dv = b["nosso_numero_dv"]
        result.append({
            "cod_cliente":           b["cod_cliente"],
            "nome_pagador":          b["nome_cliente"],
            "num_documento":         b["seu_numero"],
            "nosso_numero":          nn,
            "dv_nosso_numero":       dv,
            "nosso_numero_completo": f"{str(nn).zfill(11)}-{dv}",
            "valor":                 float(b["valor"]),
            "data_vencimento":       date.fromisoformat(b["data_vencimento"]),
            "data_emissao":          date.fromisoformat(b["data_emissao"]),
            "inscricao_pagador":     b["inscricao_pagador"],
            "endereco":              b.get("endereco_snapshot") or "",
            "cep":                   b.get("cep") or "",
            "email":                 email_map.get(b["cod_cliente"], ""),
            "competencia":           b["competencia"],
        })
    return result


def atualizar_status_lote(registros: list[dict]) -> int:
    """
    Atualiza status de múltiplos boletos pelo nosso_numero.
    Cada item: {nosso_numero: str, status_novo: str, descricao: str}
    Ignora registros com status_novo=None. Não sobrescreve boletos Cancelados.
    Retorna o número de registros efetivamente atualizados.
    """
    client = get_client()
    agora  = datetime.now(timezone.utc).isoformat()
    total  = 0
    for r in registros:
        if not r.get("status_novo"):
            continue
        nn = r["nosso_numero"].lstrip("0") or "0"
        resp = (
            client.table("boletos_emitidos")
            .update({
                "status":       r["status_novo"],
                "observacao":   r.get("descricao", ""),
                "atualizado_em": agora,
            })
            .eq("nosso_numero", int(nn))
            .neq("status", "Cancelado")
            .execute()
        )
        total += len(resp.data or [])
    return total


def obter_sequencia(carteira: str = "09") -> dict:
    client = get_client()
    return client.table("sequencia_boletos").select("*").eq("carteira", carteira).single().execute().data


def definir_sequencia(carteira: str, ultimo_nosso_numero: int, ultimo_numero_remessa: int) -> None:
    client = get_client()
    client.table("sequencia_boletos").update(
        {"ultimo_nosso_numero": ultimo_nosso_numero, "ultimo_numero_remessa": ultimo_numero_remessa}
    ).eq("carteira", carteira).execute()
