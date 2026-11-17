# Despensa — leitor de código de barras

Projeto para ler códigos de barras de produtos, identificar o item e controlar
a quantidade em estoque em `despensa.db`. A identificação segue a ordem:
**banco local → sites web → IA (Groq)**.

## Estrutura

```
despensa/
├── .env                  # chaves (GROQ_API_KEY, SI5_API_TOKEN, ...) — não versionar
├── despensa.db           # banco SQLite de produtos
├── config.py             # carregador do .env (sem dependência extra)
├── teste_16.py           # leitor principal: foto -> banco -> sites web -> IA
├── registro.py           # versão manual (sem IA)
├── recebe_foto.py        # recebe fotos da ESP32 pela porta serial
├── api_codigo.py         # consulta a APIs/open facts por código EAN
├── teste.py              # consulta interativa por código EAN
├── produtos.py           # servidor Flask para scanner em tempo real
├── imagens/              # fotos de teste e foto enviada pela ESP32
├── firmware/
│   └── FOTO.ino          # sketch da ESP32-CAM (envia foto via USB)
└── requirements.txt
```

## Uso

Rode os comandos de dentro desta pasta:

```bash
pip install -r requirements.txt

# leitor principal (IA é o último recurso)
python3 teste_16.py miojo.jpg

# cadastro manual, sem IA
python3 registro.py miojo.jpg

# consulta de código em bases públicas
python3 api_codigo.py miojo.jpg
python3 teste.py

# receptores de câmera
python3 recebe_foto.py
python3 produtos.py
```

Caminhos de imagem relativos são procurados primeiro no diretório atual e, se
não existirem, dentro de `imagens/`.

## Configuração

As chaves ficam no `.env` ao lado dos scripts e são carregadas por `config.py`.
O `.env` tem prioridade: cole uma chave nova no arquivo e rode o script
novamente para recarregá-la, sem editar código nem reiniciar o terminal.

- `GROQ_API_KEY`: usada por `teste_16.py` na última etapa da busca. É gratuita,
  sem cartão, e dá 30 req/min e 1.000 req/dia no modelo `gpt-oss-120b`.
- `GROQ_MODEL`: opcional; por padrão `openai/gpt-oss-120b`.
- `SI5_API_TOKEN` / `SI5_API_URL`: usadas por `api_codigo.py` e `teste.py`.

Para configurar a IA (uma vez):

```bash
# 1) gere uma chave gratuita em https://console.groq.com/keys
# 2) cole no .env:
GROQ_API_KEY=sua_chave_aqui
# 3) rode novamente
python3 teste_16.py miojo.jpg
```

A etapa de IA busca o EAN no DuckDuckGo (sem chave e sem custo) e envia os
trechos encontrados ao Groq, que devolve nome, marca e conteúdo em JSON.

> `pyzbar` depende da biblioteca de sistema `libzbar`. Em Fedora/RHEL:
> `sudo dnf install zbar`.

## Observações

- Ordem de busca: **banco local → sites web → IA**. Cada etapa que encontra o
  produto encerra a busca, sem chamar as seguintes.
- Sites web: Open Food Facts → Open Beauty Facts → Open Products Facts →
  UPCitemdb. Todas gratuitas e sem cota paga.
- A IA (Groq) só é chamada se nenhuma etapa anterior encontrar o produto.
  Erros `401` (chave inválida) e `429` (limite atingido) exibem aviso claro e
  caem direto no cadastro manual.
- Se nenhuma fonte identificar, ele pede os dados manualmente. Sem terminal
  interativo, avisa e encerra sem cadastrar um produto com dados vazios.
- O firmware envia a foto para `despensa/imagens/foto.jpg`. Se a pasta do
  projeto for movida, atualize `CAMINHO_FOTO_NOTEBOOK` em `firmware/FOTO.ino`.

### Busca usada pela IA

A etapa de IA busca o EAN no **DuckDuckGo em HTML** (`html.duckduckgo.com`), que
devolve títulos e trechos direto no HTML, sem JavaScript e sem chave de API.
Isso evita a Google Custom Search (que exigiria `GOOGLE_CSE_KEY`/`GOOGLE_CSE_CX`
e uma etapa extra no meio do fluxo) e mantém a identificação enxuta:
**banco local → sites web → Groq**.

### Por que não Google Photos

A Photos Library API serve para **guardar e organizar** fotos (upload, álbuns,
busca por data/texto). Não existe nela nenhum endpoint que receba uma imagem e
devolva produto, marca, peso ou EAN. Além disso exige OAuth 2.0 com tela de
consentimento e verificação do app, e enviaria as fotos para a conta do usuário.
Para reconhecimento de imagem o caminho do Google seria o Cloud Vision, que
também exige OAuth/service account (a chave de API atual recebe `401`). Por isso
a identificação continua sendo feita pelo EAN nas bases abertas, com o Groq
apenas como último recurso.
