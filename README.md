# Assistente virtual do Residencial Aurora

API em FastAPI com um assistente construído em Google ADK 2.11. Pelo chat, o morador reserva e cancela áreas
comuns, autoriza visitantes e tira dúvidas sobre o regulamento. As regras que não podem ser quebradas ficam no
código e no banco, não no prompt.

```
aurora/
├── api.py          rotas HTTP, Runner, sessões persistidas e confirmações
├── agentes.py      agente principal e especialistas
├── ferramentas.py  tools que leem e gravam reservas e visitantes
├── banco.py        SQLite do condomínio (reservas, visitantes, áreas)
├── regulamento.py  busca por capítulo/artigo em dados/regulamento.md
└── restaurar.py    volta os dados ao estado de dados/
```

## Arquitetura

O morador sempre fala com `assistente_aurora`, o agente principal. Ele não tem tool de dados e não recebe o
regulamento nas instruções: sua função é entender o pedido e entregar ao especialista certo.

| Agente | Responsabilidade | Como é acionado | Tools |
|---|---|---|---|
| `assistente_aurora` | conversa e roteamento | raiz do `App` | `regulamento` (AgentTool) |
| `reservas` | reservar, cancelar, listar e conferir disponibilidade das áreas | sub-agent, por transferência | `listar_areas`, `minhas_reservas`, `consultar_disponibilidade`, `reservar_area`, `cancelar_reserva` |
| `portaria` | autorizar e listar visitantes | sub-agent, por transferência | `meus_visitantes`, `autorizar_visitante` |
| `regulamento` | responder dúvidas sobre o regulamento | `AgentTool` do agente principal | `consultar_regulamento` |

Por que cada forma de acionamento:

- **`reservas` e `portaria` são sub-agents.** São eles que pedem confirmação ao morador. A resposta da
  confirmação precisa voltar para o agente que fez a chamada, e o Runner só faz isso para agentes da árvore de
  transferência. Com o `App` resumível, o Runner encontra o autor do `adk_request_confirmation` e retoma nele,
  inclusive depois de reiniciar a API. Os dois podem devolver a conversa ao principal quando o assunto muda.
- **`regulamento` é uma `AgentTool`.** O `AgentTool` roda o especialista numa sessão própria, em memória, e
  devolve ao agente principal só a resposta final. A busca no regulamento e os artigos que ela traz ficam nessa
  sessão descartável e nunca entram nos eventos da conversa. Ele não precisa de confirmação, então não perde
  nada por não estar na árvore de transferência.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- `aurora/ferramentas.py`, linhas 132 e 138: `reservar_area` é registrada com
  `FunctionTool(reservar_area, require_confirmation=_gera_cobranca)` e `autorizar_visitante` com
  `require_confirmation=True`. `_gera_cobranca` (linha 53) olha a taxa da área no banco, então a quadra (taxa 0)
  não pede confirmação e o salão e a churrasqueira pedem. Quem decide é o dado, não o modelo.
- As próprias tools conferem `tool_context.tool_confirmation` antes de gravar (linhas 72 e 112). O ADK já impede
  a execução sem confirmação; essa segunda checagem faz a garantia valer mesmo que alguém registre a função
  de outro jeito.
- `aurora/api.py`, `confirmacoes_pendentes` (linha 82): as pendências são lidas dos eventos da sessão, ou seja,
  chamadas `adk_request_confirmation` que ainda não têm resposta. `responder_confirmacao` (linha 141) só aceita
  um id que esteja nessa lista e responde 409 para qualquer outro, inclusive um já respondido (linha 145).
  Como a lista vem dos eventos persistidos, ela continua certa depois de reiniciar a API.

Escrever "já estou confirmando" no chat não muda nada: o texto do morador nunca vira `FunctionResponse`. A única
forma de produzir a resposta é a rota de confirmações.

### 2. Cada sessão pertence a um apartamento

- `aurora/api.py`, linha 127: o apartamento é gravado no state da sessão quando ela é criada. Nenhuma rota
  altera esse valor depois.
- `aurora/ferramentas.py`, linha 18: todas as tools leem o apartamento de `tool_context.state`. Nenhuma tool tem
  parâmetro de apartamento, então não há valor escolhido pelo modelo a validar.
- `aurora/banco.py`: as consultas de reservas e visitantes filtram por apartamento, e `cancelar` só cancela
  reserva do próprio apartamento (`WHERE apartamento = ?`). `consultar_disponibilidade` devolve só
  `livre: true/false`, sem código nem dono, e uma reserva perdida devolve só "já está reservado".

Dados de outros apartamentos nunca chegam ao modelo, então não há o que ele vazar, mesmo que o morador diga
ser de outra unidade.

### 3. Nada se perde no reinício

- `aurora/api.py`, linha 65: as sessões usam `DatabaseSessionService` sobre SQLite (`estado/sessoes.db`).
  Eventos e state ficam no disco.
- `aurora/api.py`, linha 54: `ResumabilityConfig(is_resumable=True)` faz a resposta a uma confirmação pendente
  voltar ao especialista que a pediu, também depois de um reinício. Testei aprovando uma confirmação criada
  antes de derrubar a API.
- `aurora/banco.py`: reservas e visitantes ficam em `estado/condominio.db`. Na subida, `preparar()` só cria o
  banco se ele não existir; quem volta ao estado inicial é o comando de restauração.

### 4. O regulamento é consultado, não carregado

- `aurora/agentes.py`, linha 76: o agente principal não tem o regulamento nas instruções.
- `aurora/agentes.py`, linha 90: o especialista entra como `AgentTool`, cuja sessão interna não é gravada na
  sessão do morador. Na sessão aparecem só a chamada à tool e a resposta final do especialista.
- `aurora/regulamento.py`, `consultar` (linha 71): mesmo dentro do especialista, a busca escolhe um capítulo só e
  devolve até três artigos dele. O texto inteiro nunca é enviado ao modelo.

### 5. Dois moradores, uma reserva

- `aurora/banco.py`, linha 41: índice único parcial
  `CREATE UNIQUE INDEX uma_reserva_por_area_e_data ON reservas (area, data) WHERE cancelada = 0`.
  A exclusividade é verificada pelo SQLite no momento do `INSERT`, e não numa consulta anterior.
- `aurora/banco.py`, `reservar` (linha 157): quem perde a disputa recebe `IntegrityError`, convertido em
  `DataOcupada` (linha 167), e a tool responde `indisponivel`. Para a API é uma resposta normal: as duas
  aprovações voltam 200 e só uma reserva existe.
- O código das reservas novas vem de uma coluna `AUTOINCREMENT` (`RSV-<id>`, a partir de 10001). O SQLite nunca
  reaproveita esse id, nem depois de cancelar ou restaurar, então um código não se repete. Reservas canceladas
  continuam na tabela, só marcadas como canceladas.

## Como rodar

Pré-requisitos: Python 3.12 ou superior, [uv](https://docs.astral.sh/uv/) e uma chave do
[Google AI Studio](https://aistudio.google.com/apikey).

```bash
cp .env.example .env      # preencha GOOGLE_API_KEY
uv sync
```

Variáveis do `.env`:

| Variável | Para que serve |
|---|---|
| `GOOGLE_API_KEY` | chave do Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | `FALSE`, para usar a Gemini API do AI Studio |
| `GEMINI_MODEL` | modelo dos agentes (padrão `gemini-3.5-flash`) |

Restaurar os dados iniciais (reservas e visitantes de `dados/`). Rode com a API parada; com `--sessoes`, apaga
também as conversas:

```bash
uv run python -m aurora.restaurar
```

Subir a API em http://localhost:8000:

```bash
uv run uvicorn aurora.api:api --port 8000
```

Não há serviço externo: os dois bancos SQLite ficam em `estado/`, criado na primeira subida.

Exemplo rápido:

```bash
curl -s -X POST localhost:8000/sessoes -H 'Content-Type: application/json' -d '{"apartamento": "101"}'
curl -s -X POST localhost:8000/sessoes/<session_id>/mensagens -H 'Content-Type: application/json' \
  -d '{"texto": "Reserve o salão de festas para 2030-04-20."}'
curl -s -X POST localhost:8000/sessoes/<session_id>/confirmacoes -H 'Content-Type: application/json' \
  -d '{"id": "<id da confirmação>", "confirmado": true}'
curl -s localhost:8000/apartamentos/101/reservas
```
