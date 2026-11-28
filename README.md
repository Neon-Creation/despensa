# Despensa

Aplicação de controle de estoque por código de barras. A ESP32-CAM captura fotos
pela USB e a API lê o EAN, identifica produtos novos em bases públicas e
incrementa a quantidade.

## Como funciona

1. No PC, `visualizador.py` (http://localhost:8090) mostra a câmera ao vivo e
   comanda a placa pela serial USB (921600 baud). O Wi-Fi da placa está desligado.
2. Um clique no botão verde da página — ou no botão físico do pino 42 — inicia
   uma **rajada de até 5 fotos**. Todas são salvas em `imagens/fotos/<sessao>/`
   e decodificadas localmente; a sessão para após 2 leituras iguais.
3. A melhor foto é enviada uma única vez à API como `multipart/form-data`, com o
   header `X-API-Key` e um campo `sessao` que evita contagem duplicada.
4. A API lê o EAN em memória. Se o produto existir, soma 1 ao estoque. Se for
   novo, consulta Open Food Facts → UPCitemdb → Cosmos (e, opcionalmente, Groq em
   background); se identificar, cadastra e já conta (`acao: "cadastrado"`).
5. Se nenhuma fonte identificar o EAN, a API responde `acao: "erro"`, emite
   `produto-nao-cadastrado` e o dashboard abre o modal com os dados sugeridos
   para o usuário conferir e cadastrar via `PUT /produto/{codigo}`.

## Teste rápido da API

Os testes automatizados rodam localmente, não chamam serviços externos e usam
uma cópia temporária do banco para não alterar `despensa.db`:

```bash
python3 -m venv .venv
source .venv/bin/activate
brew install zbar                 # macOS; no Debian/Ubuntu: sudo apt install libzbar0
pip install -r requirements-dev.txt
pytest -q
```

Em macOS com Apple Silicon, o `zbar_compat.py` localiza automaticamente a
`libzbar` instalada pelo Homebrew, que o `pyzbar` não encontra sozinho.

Para subir a API localmente com Docker:

```bash
cp .env.example .env
# Edite .env e troque API_KEY por uma chave local de teste.
docker compose up --build
```

Em outro terminal, confirme que a API e o banco estão ativos:

```bash
curl http://localhost:8000/health
```

O resultado esperado é `{"status":"ok","database":"connected"}`. A interface
interativa dos endpoints fica em <http://localhost:8000/docs>. Para testar o
envio de uma imagem:

```bash
curl -X POST http://localhost:8000/upload \
  -H "X-API-Key: SUA_CHAVE_LOCAL" \
  -F "file=@imagens/miojo.jpg"
```

O Docker inicia com um banco vazio; nesse caso, um EAN lido será reconhecido,
mas a resposta informará que o produto ainda não está cadastrado. O banco do
Docker fica em um volume persistente chamado `despensa_data`.

Para cadastrar esse produto e já permitir a contagem no próximo upload:

```bash
curl -X PUT http://localhost:8000/produto/7896051111030 \
  -H "X-API-Key: SUA_CHAVE_LOCAL" \
  -H "Content-Type: application/json" \
  -d '{"nome":"Leite integral","marca":"Marca X","conteudo":"1 L"}'
```

O `PUT` é idempotente: em produto novo a quantidade começa em 1; em produto
já existente os metadados são atualizados sem zerar a contagem, a menos que o
campo opcional `quantidade` seja enviado.

## Captura pela USB (visualizador)

Com a placa ligada em COM5 e o firmware atual gravado:

```bash
python visualizador.py COM5 8090
```

Abra <http://localhost:8090>, aponte a câmera para o código de barras (a ~10–20 cm,
com boa luz, dentro da caixa verde) e clique em **Bater foto** ou no botão do
pino 42. Cada sessão salva 5 JPEGs em `imagens/fotos/<sessao>/` e envia a melhor
foto à API. O status da última sessão aparece na página.

As variáveis `PRODUTO_LOOKUP_ATIVO`, `PRODUTO_LOOKUP_GROQ` e
`PRODUTO_LOOKUP_TIMEOUT_S` controlam a identificação automática (veja
`.env.example`). O Groq roda em background e avisa o dashboard por SSE.

## API

| Método | Rota | Autenticação | Função |
|---|---|---|---|
| `GET` | `/health` | Não | Verifica a API e a conexão com o banco |
| `POST` | `/upload` | `X-API-Key` | Recebe uma imagem e tenta contar o produto |
| `GET` | `/estoque` | `X-API-Key` | Lista os produtos e o total |
| `GET` | `/produto/{codigo}` | `X-API-Key` | Consulta um produto pelo EAN |
| `GET` | `/produto/{codigo}/sugestao` | `X-API-Key` | Sugere nome/marca de um EAN novo (fontes rápidas, sem gravar) |
| `GET` | `/produto/{codigo}/identificar-ia` | chave do dashboard | Busca com IA sob demanda (Groq); pode demorar segundos |
| `PUT` | `/produto/{codigo}` | `X-API-Key` | Cadastra ou atualiza um produto pelo EAN |
| `DELETE` | `/produto/{codigo}` | chave do dashboard | Apaga um produto (ESP32 recebe 403) |
| `GET` | `/events/estoque` | `X-API-Key` | SSE: eventos de atualização de quantidade |

O upload aceita imagens de até `MAX_UPLOAD_MB` (5 MB por padrão) e tem limite
de 10 requisições por minuto por IP. As consultas de estoque têm limite de 30
por minuto. O healthcheck não requer chave.

### Formato do EAN e o campo `total`

O EAN nas rotas `/produto/{codigo}` deve ter de 8 a 14 dígitos numéricos
(`^[0-9]{8,14}$`, cobrindo EAN-8, UPC-A/EAN-13 e EAN-14). Um código fora desse
formato recebe `400` em `GET` e `PUT`; a resposta e o corpo de cadastro não
mudaram — o EAN continua sendo apenas o parâmetro da URL.

`GET /estoque` devolve `total` como o número de produtos únicos cadastrados
(uma linha por EAN), **não** a soma das quantidades.

**Dados legados:** não há limpeza automática nem alteração do banco. Registros
já existentes com EAN fora do padrão são omitidos de `GET /estoque` (para não
quebrar a validação da resposta) e não podem ser consultados ou atualizados
pela API até serem corrigidos manualmente. Para localizá-los:

```sql
SELECT codigo, nome FROM produtos
WHERE codigo IS NULL
   OR length(codigo) NOT BETWEEN 8 AND 14
   OR codigo GLOB '*[^0-9]*';
```

A correção é uma decisão operacional do proprietário dos dados; a API apenas
deixa de expor esses registros até que tenham um EAN válido.

## Configuração da ESP32-CAM

Copie `firmware/secrets.h.example` para `firmware/secrets.h` e configure SSID,
senha Wi-Fi, URL completa da rota `/upload` e a mesma chave definida em
`API_KEY`. O arquivo `secrets.h` é ignorado pelo Git; não publique credenciais.
Para uma URL `https://`, mantenha configurado o certificado CA correspondente.
Compile e grave `firmware/esp32_cam.ino` com as bibliotecas da placa, incluindo
ArduinoJson. A câmera envia a foto à API e sinaliza o resultado pelos bipes e
pela saída serial.

## Atualizações em tempo real (SSE)

`GET /events/estoque` mantém uma conexão SSE aberta e envia um evento sempre
que a quantidade de um produto muda (upload da ESP32 ou cadastro via `PUT`):

```
event: estoque-update
data: {"codigo": "7891079013373", "quantidade": 5, "acao": "incrementado"}
```

O campo `acao` é `incrementado` para uploads e `atualizado` para `PUT`. Cada
cliente pode manter **uma** conexão SSE por vez; uma segunda conexão recebe
`429`. A vaga é liberada ao desconectar. O envio de keep-alive usa ping de 15 s.

```bash
curl -N -H "X-API-Key: $DASHBOARD_KEY" http://localhost:8000/events/estoque
```

O consumo no dashboard (React Query + fallback para polling) é uma tarefa
separada. Nos testes, `tests/test_sse.py` sobe um servidor real porque o
`TestClient` do Starlette não suporta respostas SSE de duração indefinida.

## Simulação da ESP32 (sem hardware)

O script `simular_esp32.py` reproduz o caminho completo da câmera: captura a
foto (lê o JPEG), monta o **mesmo** `multipart/form-data` do firmware, envia por
HTTP com o header `X-API-Key` e interpreta a resposta com as regras de
bipes/mensagens seriais da ESP32. Por padrão ele sobe uma API local em segundo
plano usando uma cópia temporária do banco, sem alterar `despensa.db`:

```bash
python3 simular_esp32.py imagens/miojo.jpg
```

Para apontar para uma API já em execução (Docker, por exemplo):

```bash
python3 simular_esp32.py imagens/miojo.jpg \
  --url http://localhost:8000/upload --chave SUA_CHAVE_LOCAL
```

Saída esperada para um produto já cadastrado: HTTP 200, `lido: true`,
`acao: "contado"`, `quantidade` incrementada e a sequência "bip curto + longo".
Para um produto novo, `--cadastrar` executa o fluxo completo (upload → `erro` →
`PUT` → upload → `contado`):

```bash
python3 simular_esp32.py imagens/leite.jpg --cadastrar \
  --nome "Leite integral" --marca "Marca X" --conteudo "1 L"
```

Os testes `tests/test_simulacao_esp32.py` e `tests/test_cadastro.py` cobrem o
contrato HTTP e o fluxo upload → PUT → upload.

## Scripts locais

Os scripts de terminal continuam disponíveis para cadastro manual, leitura de
imagens e consulta a fontes públicas. Use Python 3.10 ou superior; instale
`zbar` (no macOS, `brew install zbar`) e as dependências:

```bash
pip install -r requirements.txt
cp .env.example .env
```

Preencha no `.env` somente as credenciais dos serviços que pretende usar. O
arquivo `.env` não deve ser compartilhado nem versionado.

```bash
# Leitor com Gemini (busca web e, opcionalmente, Google Imagens)
python3 leitor_gemini.py imagens/miojo.jpg

# Leitor com Groq e busca DuckDuckGo
python3 leitor_groq.py imagens/miojo.jpg

# Cadastro manual, sem IA
python3 registro.py imagens/miojo.jpg

# Consultas de EAN
python3 consulta_ean_imagem.py imagens/miojo.jpg
python3 consulta_ean.py

# Recepção de fotos por serial ou scanner local
python3 recebe_foto.py
python3 servidor_scanner.py
```

Caminhos de imagem relativos são procurados primeiro no diretório atual e,
depois, em `imagens/`.

### Credenciais opcionais

- `GEMINI_API_KEY`: chave do [Google AI Studio](https://aistudio.google.com/apikey).
- `GOOGLE_CSE_KEY` e `GOOGLE_CSE_CX`: busca de imagens via Custom Search.
- `GROQ_API_KEY`: chave de [console.groq.com/keys](https://console.groq.com/keys).
- `GROQ_MODEL` e `GROQ_API_URL`: modelo e endpoint Groq (opcionais).
- `SI5_API_TOKEN` e `SI5_API_URL`: consulta opcional à API SI5 Global.

Sem essas credenciais, as consultas públicas e o cadastro manual continuam
disponíveis. O serviço local `servidor_scanner.py` roda na porta 5000 e não tem
autenticação; use-o somente em uma rede confiável.
