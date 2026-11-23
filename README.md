# Despensa

Aplicação de controle de estoque por código de barras. Uma foto tirada pela
ESP32-CAM pode ser enviada à API; ela lê o EAN e incrementa a quantidade quando
o produto já está cadastrado. Produtos novos precisam ser cadastrados pelos
scripts locais, que também podem identificar itens com fontes web e IA.

## Como funciona

1. A ESP32-CAM captura uma foto ao pressionar o botão.
2. Ela tenta enviá-la por Wi-Fi para a API como `multipart/form-data`, protegida
   pelo header `X-API-Key`. Se não conseguir, mantém o envio serial USB como
   alternativa.
3. A API lê o código de barras em memória, sem salvar a foto. Se o EAN existir
   no SQLite, soma 1 ao estoque; se não existir, informa que o cadastro deve
   ser feito localmente.
4. A API também oferece consultas do estoque e de um produto por EAN.

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

## API

| Método | Rota | Autenticação | Função |
|---|---|---|---|
| `GET` | `/health` | Não | Verifica a API e a conexão com o banco |
| `POST` | `/upload` | `X-API-Key` | Recebe uma imagem e tenta contar o produto |
| `GET` | `/estoque` | `X-API-Key` | Lista os produtos e o total |
| `GET` | `/produto/{codigo}` | `X-API-Key` | Consulta um produto pelo EAN |

O upload aceita imagens de até `MAX_UPLOAD_MB` (5 MB por padrão) e tem limite
de 10 requisições por minuto por IP. As consultas de estoque têm limite de 30
por minuto. O healthcheck não requer chave.

## Configuração da ESP32-CAM

Copie `firmware/secrets.h.example` para `firmware/secrets.h` e configure SSID,
senha Wi-Fi, URL completa da rota `/upload` e a mesma chave definida em
`API_KEY`. O arquivo `secrets.h` é ignorado pelo Git; não publique credenciais.
Para uma URL `https://`, mantenha configurado o certificado CA correspondente.
Compile e grave `firmware/esp32_cam.ino` com as bibliotecas da placa, incluindo
ArduinoJson. A câmera envia a foto à API e sinaliza o resultado pelos bipes e
pela saída serial.

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
