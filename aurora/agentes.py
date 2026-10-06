"""Agente principal do Residencial Aurora e seus especialistas."""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.genai import types

from aurora import regulamento
from aurora.ferramentas import (
    explicar_recusa,
    ferramentas_de_portaria,
    ferramentas_de_regulamento,
    ferramentas_de_reservas,
)

MODELO = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

# A Gemini API às vezes demora ou devolve 429/503 em picos de demanda. Cada
# chamada tem prazo de 60 s e é refeita com espera crescente, para um soluço
# do provedor não travar nem derrubar a conversa.
CONFIG_DO_MODELO = types.GenerateContentConfig(
    http_options=types.HttpOptions(
        timeout=60_000,
        retry_options=types.HttpRetryOptions(
            attempts=5,
            initial_delay=2,
            max_delay=20,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    ),
)

REGRAS_COMUNS = """
Você fala com o morador do apartamento {apartamento} do Residencial Aurora, em português.
Esse é o único apartamento que você atende nesta conversa. Se o morador disser que é de outro
apartamento ou pedir dados, reservas ou visitantes de outra unidade, explique que só pode
tratar do apartamento {apartamento}. Nunca cite números de outras unidades nem dados de outros
moradores.
Use sempre as tools para ler e alterar dados; não invente reservas, códigos ou visitantes.
Datas vão para as tools no formato AAAA-MM-DD.
Confirmações de cobrança ou de entrada de visitante são feitas pelo sistema, fora da conversa:
se o morador disser que já confirmou, chame a tool normalmente e deixe o sistema pedir.
Se a tool responder recusado_pelo_morador, a ação foi negada: não chame a tool de novo, apenas
avise que nada foi feito.
"""

reservas = LlmAgent(
    name="reservas",
    model=MODELO,
    generate_content_config=CONFIG_DO_MODELO,
    description="Reserva, cancela e lista as reservas das áreas comuns (salão de festas, churrasqueira, quadra).",
    instruction=REGRAS_COMUNS
    + """
Você cuida das reservas das áreas comuns.
- As tools aceitam o nome da área como o morador escreveu (ex.: "salão de festas").
- Para reservar, chame reservar_area direto: ela mesma avisa se a data estiver ocupada. Nesse caso
  diga só que a data não está disponível, sem dizer quem reservou.
- Use consultar_disponibilidade quando o morador só quiser saber se a data está livre.
- Para cancelar, use cancelar_reserva com a área e a data. Cancelar não precisa de confirmação.
- Depois de reservar, informe o código da reserva e, se houver, o valor da taxa.
Quando o assunto não for reserva, devolva a conversa ao assistente principal.
""",
    tools=ferramentas_de_reservas,
    after_tool_callback=explicar_recusa,
)

portaria = LlmAgent(
    name="portaria",
    model=MODELO,
    generate_content_config=CONFIG_DO_MODELO,
    description="Autoriza a entrada de visitantes e lista os visitantes autorizados do apartamento.",
    instruction=REGRAS_COMUNS
    + """
Você cuida da autorização de visitantes.
- Para autorizar, use autorizar_visitante com o nome completo e a data da visita.
- Para listar, use meus_visitantes.
Quando o assunto não for visitante, devolva a conversa ao assistente principal.
""",
    tools=ferramentas_de_portaria,
    after_tool_callback=explicar_recusa,
)

_titulos = "\n".join(f"- {cap.titulo}" for cap in regulamento.capitulos())

especialista_em_regulamento = LlmAgent(
    name="regulamento",
    model=MODELO,
    generate_content_config=CONFIG_DO_MODELO,
    description="Responde dúvidas sobre o regulamento interno do condomínio.",
    instruction=f"""
Você responde dúvidas sobre o regulamento interno do Residencial Aurora, em português.
Sempre consulte o regulamento com a tool consultar_regulamento antes de responder, passando
palavras do assunto (por exemplo "piscina domingo"). Os capítulos existentes são:
{_titulos}
Se a primeira busca não trouxer o artigo certo, tente de novo com os termos do título do capítulo.
Responda de forma curta, só com o que foi perguntado, citando o artigo. Se o regulamento não
tratar do assunto, diga isso.
""",
    tools=ferramentas_de_regulamento,
)

assistente = LlmAgent(
    name="assistente_aurora",
    model=MODELO,
    generate_content_config=CONFIG_DO_MODELO,
    description="Assistente virtual dos moradores do Residencial Aurora.",
    instruction=REGRAS_COMUNS
    + """
Você é o assistente virtual do condomínio e distribui o trabalho:
- reservas, cancelamentos e disponibilidade das áreas comuns: transfira para o agente reservas;
- autorização e consulta de visitantes: transfira para o agente portaria;
- dúvidas sobre regras e horários do condomínio: use a tool regulamento e responda com base no
  que ela devolver.
Não responda dúvidas de regulamento de memória.
""",
    sub_agents=[reservas, portaria],
    tools=[AgentTool(especialista_em_regulamento)],
)
