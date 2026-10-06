"""Tools dos especialistas.

Nenhuma tool recebe apartamento como argumento: o apartamento vem sempre do
state da sessão, gravado uma única vez quando a sessão é criada pela API.
"""

from __future__ import annotations

from datetime import date

from google.adk.tools import FunctionTool, ToolContext

from aurora import banco, regulamento

CHAVE_APARTAMENTO = "apartamento"


def _apartamento(tool_context: ToolContext) -> str:
    return tool_context.state[CHAVE_APARTAMENTO]


def _data_valida(data: str) -> bool:
    try:
        date.fromisoformat(data)
    except ValueError:
        return False
    return len(data) == 10


def _erro_de_data(data: str) -> dict:
    return {"status": "erro", "mensagem": f"Data invalida: {data}. Use o formato AAAA-MM-DD."}


def listar_areas() -> dict:
    """Lista as áreas comuns que podem ser reservadas, com o id e a taxa de cada uma."""
    return {"areas": [{"id": a.id, "nome": a.nome, "taxa": a.taxa} for a in banco.areas()]}


def minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador."""
    return {"reservas": banco.reservas_do_apartamento(_apartamento(tool_context))}


def consultar_disponibilidade(area: str, data: str) -> dict:
    """Diz se uma área (id ou nome) está livre em uma data (AAAA-MM-DD). Não revela de quem é a reserva."""
    encontrada = banco.area(area)
    if encontrada is None:
        return {"status": "erro", "mensagem": f"Area desconhecida: {area}. Consulte listar_areas."}
    if not _data_valida(data):
        return _erro_de_data(data)
    return {"area": encontrada.id, "data": data, "livre": banco.data_livre(encontrada.id, data)}


def _gera_cobranca(area: str, data: str, tool_context: ToolContext) -> bool:
    encontrada = banco.area(area)
    return encontrada is not None and encontrada.taxa > 0


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum (id ou nome) para o apartamento do morador na data (AAAA-MM-DD).

    Áreas com taxa geram cobrança e só são reservadas depois que o morador
    aprova a confirmação enviada pelo sistema.
    """
    encontrada = banco.area(area)
    if encontrada is None:
        return {"status": "erro", "mensagem": f"Area desconhecida: {area}. Consulte listar_areas."}
    if not _data_valida(data):
        return _erro_de_data(data)

    # O ADK já barra a execução sem confirmação; a checagem aqui garante que
    # nenhuma outra montagem da tool consiga gravar uma cobrança sem ela.
    confirmacao = tool_context.tool_confirmation
    if encontrada.taxa > 0 and not (confirmacao and confirmacao.confirmed):
        return {"status": "erro", "mensagem": "Reserva com cobranca exige confirmacao do morador."}

    try:
        codigo = banco.reservar(_apartamento(tool_context), encontrada.id, data)
    except banco.DataOcupada:
        return {"status": "indisponivel", "mensagem": f"{encontrada.nome} ja esta reservado em {data}."}
    resultado = {"status": "reservado", "codigo": codigo, "area": encontrada.id, "data": data}
    if encontrada.taxa > 0:
        resultado["cobranca"] = encontrada.taxa
    return resultado


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela a reserva do apartamento do morador para a área (id ou nome) na data (AAAA-MM-DD)."""
    encontrada = banco.area(area)
    if encontrada is None:
        return {"status": "erro", "mensagem": f"Area desconhecida: {area}. Consulte listar_areas."}
    if not _data_valida(data):
        return _erro_de_data(data)
    codigo = banco.cancelar(_apartamento(tool_context), encontrada.id, data)
    if codigo is None:
        return {"status": "nao_encontrada", "mensagem": "O seu apartamento nao tem reserva ativa dessa area nessa data."}
    return {"status": "cancelada", "codigo": codigo, "area": encontrada.id, "data": data}


def meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados para o apartamento do morador."""
    return {"visitantes": banco.visitantes_do_apartamento(_apartamento(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante, pelo nome completo, na data (AAAA-MM-DD).

    Sempre passa pela confirmação do morador antes de gravar.
    """
    nome = " ".join(nome.split())
    if not nome:
        return {"status": "erro", "mensagem": "Informe o nome do visitante."}
    if not _data_valida(data):
        return _erro_de_data(data)

    confirmacao = tool_context.tool_confirmation
    if not (confirmacao and confirmacao.confirmed):
        return {"status": "erro", "mensagem": "Autorizacao de visitante exige confirmacao do morador."}

    banco.autorizar_visitante(_apartamento(tool_context), nome, data)
    return {"status": "autorizado", "nome": nome, "data": data}


def consultar_regulamento(assunto: str) -> dict:
    """Busca no regulamento interno os artigos sobre um assunto (ex.: "piscina domingo").

    Devolve só artigos do capítulo mais relacionado ao assunto.
    """
    return regulamento.consultar(assunto)


RECUSA_DO_ADK = {"error": "This tool call is rejected."}


def explicar_recusa(tool, args: dict, tool_context: ToolContext, tool_response: dict) -> dict | None:
    """Troca a recusa genérica do ADK por uma resposta que o modelo não confunda com falha."""
    if tool_response == RECUSA_DO_ADK:
        return {
            "status": "recusado_pelo_morador",
            "mensagem": "O morador recusou a confirmacao e nada foi feito. Nao chame a tool de novo.",
        }
    return None


ferramentas_de_reservas = [
    listar_areas,
    minhas_reservas,
    consultar_disponibilidade,
    FunctionTool(reservar_area, require_confirmation=_gera_cobranca),
    cancelar_reserva,
]

ferramentas_de_portaria = [
    meus_visitantes,
    FunctionTool(autorizar_visitante, require_confirmation=True),
]

ferramentas_de_regulamento = [consultar_regulamento]
