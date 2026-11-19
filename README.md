# Despensa — leitor de código de barras

Projeto para ler códigos de barras de produtos, identificar o item e controlar a
quantidade em estoque em `despensa.db`. A identificação segue a ordem:

**banco local → bases web → (opcional) Google Imagens → IA**

Existem dois leitores principais, com backends de IA diferentes:

- `leitor_gemini.py` — usa **Gemini** (Google Search Grounding) como último recurso.
- `leitor_groq.py` — usa **Groq** (gratuito) com busca no DuckDuckGo como último recurso.

Ambos compartilham a mesma lógica de leitura de imagem e de banco de dados.

## Estrutura

```text
despensa/
├── .env.example           # modelo de configuração (sem credenciais)
├── .gitignore
├── despensa.db            # banco SQLite criado localmente (não versionar)
├── config.py              # carregador do .env (sem dependência extra)
├── comum.py               # banco, leitura de imagem, cadastro e buscas web
├── fontes_ean.py          # consultas de EAN compartilhadas
├── leitor_gemini.py       # leitor principal com Gemini
├── leitor_groq.py         # leitor principal com Groq
├── registro.py            # cadastro manual, sem IA
├── consulta_ean.py        # consulta interativa de EAN em fontes públicas
├── consulta_ean_imagem.py # lê o EAN de uma imagem e consulta bases públicas
├── recebe_foto.py         # recebe fotos da ESP32-CAM pela porta serial
├── servidor_scanner.py    # servidor Flask para scanner em tempo real
├── imagens/               # fotos de teste e destino da foto da ESP32
├── firmware/
│   └── esp32_cam.ino      # sketch da ESP32-CAM (envia foto via USB)
└── requirements.txt
```

O arquivo `despensa.db` é criado localmente quando o primeiro produto é
cadastrado. Ele e o `.env` são ignorados pelo Git e não devem ser publicados.

## Instalação

Use Python 3.10 ou superior. A biblioteca `pyzbar` também precisa da biblioteca
nativa `zbar`. No macOS, instale com `brew install zbar`; no Fedora/RHEL, use
`sudo dnf install zbar`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edite `.env` e preencha apenas as credenciais dos serviços que pretende usar.
Nunca compartilhe nem versione esse arquivo.

## Uso

Execute os comandos na raiz do projeto:

```bash
# Leitor com Gemini: imagem -> banco -> fontes web -> Google Imagens -> Gemini
python3 leitor_gemini.py miojo.jpg

# Leitor com Groq: imagem -> banco -> fontes web -> DuckDuckGo + Groq
python3 leitor_groq.py miojo.jpg

# Cadastro manual, sem IA
python3 registro.py miojo.jpg

# Consultas por código EAN
python3 consulta_ean_imagem.py miojo.jpg
python3 consulta_ean.py

# Receptores de câmera
python3 recebe_foto.py
python3 servidor_scanner.py
```

Caminhos de imagem relativos são procurados primeiro no diretório atual e, se
não existirem, dentro de `imagens/`.

## Configuração

As chaves ficam no `.env` ao lado dos scripts e são carregadas por `config.py`.
O `.env` tem prioridade: cole uma chave nova no arquivo e rode o script
novamente para recarregá-la, sem editar código nem reiniciar o terminal.

### Leitor com Gemini (`leitor_gemini.py`)

- `GEMINI_API_KEY`: gere em [Google AI Studio](https://aistudio.google.com/apikey).
- `GOOGLE_CSE_KEY` e `GOOGLE_CSE_CX`: opcionais; habilitam a busca de imagens
  pela Custom Search JSON API.

### Leitor com Groq (`leitor_groq.py`)

- `GROQ_API_KEY`: chave gratuita, sem cartão, gerada em
  [console.groq.com/keys](https://console.groq.com/keys).
- `GROQ_MODEL`: opcional; por padrão `openai/gpt-oss-120b`.
- `GROQ_API_URL`: opcional; por padrão o endpoint OpenAI-compatible do Groq.

A etapa de IA do Groq busca o EAN no DuckDuckGo (sem chave e sem custo) e envia
os trechos encontrados ao modelo, que devolve nome, marca e conteúdo em JSON.

### Consultas de EAN

- `SI5_API_TOKEN` / `SI5_API_URL`: opcionais; habilitam a consulta à API SI5
  Global em `consulta_ean_imagem.py` e `consulta_ean.py`.

Sem nenhuma dessas credenciais, as consultas públicas (Open Food Facts, Open
Beauty Facts, Open Products Facts, UPCitemdb) e o cadastro manual continuam
disponíveis.

## Observações

- Cada etapa que encontra o produto encerra a busca, sem chamar as seguintes.
- Erros de autenticação/cota das IAs exibem aviso claro e caem no cadastro manual.
- Se nenhuma fonte identificar, o script pede os dados manualmente. Sem terminal
  interativo, ele avisa e encerra sem cadastrar um produto com dados vazios.
- `servidor_scanner.py` inicia um servidor Flask na porta 5000. O firmware da ESP32 pode
  enviar quadros para o endpoint `/scan`; a interface fica em
  `http://localhost:5000/`. Use apenas em rede confiável: não há autenticação.
- Configure a porta serial em `recebe_foto.py` conforme o sistema
  (`/dev/ttyACM0` é o padrão) e grave `firmware/esp32_cam.ino` na placa. O receptor
  salva a foto em `imagens/foto.jpg`; execute-o a partir da raiz do projeto.
- O firmware envia a foto para `imagens/foto.jpg`. Se o projeto for movido,
  ajuste `CAMINHO_FOTO_NOTEBOOK` em `firmware/esp32_cam.ino`.
