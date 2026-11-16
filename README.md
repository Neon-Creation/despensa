# Despensa

Leitor de códigos de barras e controle de produtos em estoque. O projeto lê o
EAN de uma imagem, consulta fontes públicas e registra a quantidade na despensa
em um banco SQLite local. A busca principal segue esta ordem:
**banco local → bases web → Google Imagens (opcional) → Gemini (opcional)**.

## Recursos

- Leitura de códigos de barras em imagens, com ajustes de orientação e contraste.
- Cadastro e contagem de produtos em banco SQLite local.
- Consulta de informações em Open Food Facts, UPCitemdb e outras fontes.
- Captura de imagens por ESP32-CAM via USB ou envio de quadros para scanner web
  em tempo real.

## Arquivos

```text
.
├── leitor_produto.py    # leitura de imagem, busca de produto e cadastro
├── registro.py          # cadastro manual, sem IA
├── consulta_ean.py      # consulta interativa de EAN em fontes públicas
├── api_codigo.py        # consulta de EAN identificado em uma imagem
├── recebe_foto.py       # recebe imagens da ESP32-CAM pela porta serial
├── produtos.py          # servidor Flask para scanner em tempo real
├── config.py            # carregamento opcional das variáveis de ambiente
├── imagens/             # imagens de exemplo e destino da foto da ESP32
├── firmware/FOTO.ino    # firmware da ESP32-CAM
├── .env.example         # modelo de configuração (sem credenciais)
└── requirements.txt
```

O arquivo `despensa.db` é criado localmente quando o primeiro produto é
cadastrado. Ele e o `.env` são ignorados pelo Git e não devem ser publicados.

## Instalação

Use Python 3.10 ou superior. A biblioteca `pyzbar` também precisa da biblioteca
nativa `zbar`. No macOS, instale-a com `brew install zbar`; no Fedora/RHEL, use
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
# Leitor principal: imagem -> banco local -> fontes web -> Google Imagens -> Gemini
python3 leitor_produto.py miojo.jpg

# Cadastro manual, sem busca automática
python3 registro.py miojo.jpg

# Consultas por código EAN
python3 api_codigo.py miojo.jpg
python3 consulta_ean.py

# Scanner serial da ESP32-CAM e scanner HTTP ao vivo
python3 recebe_foto.py
python3 produtos.py
```

`leitor_produto.py` e `registro.py` também procuram imagens em `imagens/` quando
o caminho informado não existe no diretório atual. Para `api_codigo.py`, informe
o caminho da imagem.

### Scanner em tempo real

`produtos.py` inicia um servidor Flask na porta 5000. O firmware da ESP32 pode
enviar quadros para o endpoint `/scan`; a interface de vídeo fica disponível em
`http://localhost:5000/`. Use apenas em uma rede confiável: o servidor não
implementa autenticação.

### ESP32-CAM via USB

Configure a porta serial em `recebe_foto.py` conforme o sistema (`/dev/ttyACM0`
é o valor padrão) e grave `firmware/FOTO.ino` na placa. O receptor grava a foto
recebida em `imagens/foto.jpg`; execute-o a partir da raiz do projeto.

## Configuração opcional

- `GEMINI_API_KEY`: permite que `leitor_produto.py` use o Gemini como último
  recurso. Gere a chave em [Google AI Studio](https://aistudio.google.com/apikey).
- `GOOGLE_CSE_KEY` e `GOOGLE_CSE_CX`: habilitam a busca de imagens pela Custom
  Search JSON API.
- `SI5_API_TOKEN` e `SI5_API_URL`: habilitam a consulta à API SI5 Global nos
  scripts de consulta de EAN.

Sem essas credenciais, as consultas públicas e o cadastro manual continuam
disponíveis. O `.env` é carregado por `config.py` e não sobrescreve variáveis já
definidas no ambiente, exceto nos scripts que explicitamente recarregam o
arquivo.
