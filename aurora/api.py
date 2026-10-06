"""API HTTP do assistente do Residencial Aurora."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from google.adk.agents import RunConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, Session
from google.genai import types
from pydantic import BaseModel

load_dotenv()

from aurora import banco  # noqa: E402
from aurora.agentes import assistente  # noqa: E402
from aurora.ferramentas import CHAVE_APARTAMENTO  # noqa: E402

NOME_DO_APP = "residencial_aurora"
USUARIO = "morador"
PEDIDO_DE_CONFIRMACAO = "adk_request_confirmation"
# Teto de chamadas ao modelo por mensagem, para um desvio de roteamento não
# virar um laço caro.
LIMITE_DE_CHAMADAS = RunConfig(max_llm_calls=25)
SESSOES = os.environ.get("AURORA_SESSOES", f"sqlite+aiosqlite:///{banco.RAIZ / 'estado' / 'sessoes.db'}")


class NovaSessao(BaseModel):
    apartamento: str


class NovaMensagem(BaseModel):
    texto: str


class RespostaDeConfirmacao(BaseModel):
    id: str
    confirmado: bool


# O App é resumível para que a resposta de uma confirmação volte ao
# especialista que a pediu, inclusive depois de reiniciar a API.
app_adk = App(
    name=NOME_DO_APP,
    root_agent=assistente,
    resumability_config=ResumabilityConfig(is_resumable=True),
)

estado: dict[str, Any] = {}
travas: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    banco.preparar()
    (banco.RAIZ / "estado").mkdir(exist_ok=True)
    servico = DatabaseSessionService(db_url=SESSOES)
    estado["sessoes"] = servico
    estado["runner"] = Runner(app=app_adk, session_service=servico)
    yield
    await estado["runner"].close()


api = FastAPI(title="Assistente do Residencial Aurora", lifespan=ciclo_de_vida)


async def _sessao(session_id: str) -> Session:
    sessao = await estado["sessoes"].get_session(app_name=NOME_DO_APP, user_id=USUARIO, session_id=session_id)
    if sessao is None:
        raise HTTPException(status_code=404, detail="sessao nao encontrada")
    return sessao


def confirmacoes_pendentes(sessao: Session) -> list[dict[str, Any]]:
    """Pedidos de confirmação da sessão que ainda não receberam resposta."""
    respondidas = {
        resposta.id
        for evento in sessao.events
        for resposta in evento.get_function_responses()
        if resposta.name == PEDIDO_DE_CONFIRMACAO
    }
    pendentes = []
    for evento in sessao.events:
        for chamada in evento.get_function_calls():
            if chamada.name != PEDIDO_DE_CONFIRMACAO or chamada.id in respondidas:
                continue
            original = (chamada.args or {}).get("originalFunctionCall") or {}
            detalhes = dict(original.get("args") or {})
            if original.get("name") == "reservar_area" and (area := banco.area(detalhes.get("area", ""))):
                detalhes["taxa"] = area.taxa
            pendentes.append({"id": chamada.id, "acao": original.get("name"), "detalhes": detalhes})
    return pendentes


def _texto(evento: Event) -> str:
    if evento.author == "user" or evento.partial or not evento.content or not evento.content.parts:
        return ""
    return "".join(parte.text for parte in evento.content.parts if parte.text and not parte.thought)


async def _executar(session_id: str, mensagem: types.Content) -> dict[str, Any]:
    textos = []
    async for evento in estado["runner"].run_async(
        user_id=USUARIO, session_id=session_id, new_message=mensagem, run_config=LIMITE_DE_CHAMADAS
    ):
        if texto := _texto(evento):
            textos.append(texto)
    sessao = await _sessao(session_id)
    return {"resposta": "\n".join(textos), "confirmacoes_pendentes": confirmacoes_pendentes(sessao)}


@api.post("/sessoes", status_code=201)
async def criar_sessao(pedido: NovaSessao) -> dict[str, str]:
    if not banco.apartamento_existe(pedido.apartamento):
        raise HTTPException(status_code=422, detail="apartamento inexistente")
    sessao = await estado["sessoes"].create_session(
        app_name=NOME_DO_APP,
        user_id=USUARIO,
        state={CHAVE_APARTAMENTO: pedido.apartamento},
    )
    return {"session_id": sessao.id}


@api.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, pedido: NovaMensagem) -> dict[str, Any]:
    async with travas[session_id]:
        await _sessao(session_id)
        mensagem = types.Content(role="user", parts=[types.Part(text=pedido.texto)])
        return await _executar(session_id, mensagem)


@api.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, pedido: RespostaDeConfirmacao) -> dict[str, Any]:
    async with travas[session_id]:
        sessao = await _sessao(session_id)
        if pedido.id not in {p["id"] for p in confirmacoes_pendentes(sessao)}:
            raise HTTPException(status_code=409, detail="nao ha confirmacao pendente com esse id nesta sessao")
        resposta = types.Part(
            function_response=types.FunctionResponse(
                id=pedido.id,
                name=PEDIDO_DE_CONFIRMACAO,
                response={"confirmed": pedido.confirmado},
            )
        )
        return await _executar(session_id, types.Content(role="user", parts=[resposta]))


@api.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str) -> list[dict[str, Any]]:
    sessao = await _sessao(session_id)
    return [evento.model_dump(mode="json", by_alias=True, exclude_none=True) for evento in sessao.events]


@api.get("/apartamentos/{numero}/reservas")
def reservas_do_apartamento(numero: str) -> list[dict[str, Any]]:
    return banco.reservas_do_apartamento(numero)


@api.get("/apartamentos/{numero}/visitantes")
def visitantes_do_apartamento(numero: str) -> list[dict[str, Any]]:
    return banco.visitantes_do_apartamento(numero)
