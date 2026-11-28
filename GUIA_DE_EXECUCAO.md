# Guia de execução — Despensa (API + Dashboard + ESP32-CAM)

Este documento descreve **como rodar os dois projetos juntos, na ordem correta**.
Os repositórios permanecem separados por propósito:

| Pasta | Repositório | Papel | Tecnologia | Porta |
| --- | --- | --- | --- | --- |
| `despensa/` | `Neon-Creation/despensa` | API, firmware e capturaserial | Python/FastAPI + Docker | `8000` |
| `dashboard-despesa/` | `Neon-Creation/dashboard-despesa` | Interface web | Bun + TanStack Start | `8080` |
| (dentro de `despensa/`) | mesmo repo | Visualizador da câmera | Python + pyserial | `8090` |

> **Não é um monorepo.** O contrato entre eles é HTTP/SSE (`docs/ESP32_CONTEXT.md`
> no dashboard e `README.md` na API). Rodar um não depende do outro, exceto pelo
> dashboard, que precisa da API para mostrar dados.

---

## 1. Pré-requisitos

- Windows com **Docker Desktop** rodando (API).
- **Bun** instalado (dashboard).
- **Python 3.11+** com `pip` (visualizador e testes da API).
- **arduino-cli** se precisar regravar o firmware.
- ESP32-S3-CAM ligada via USB na porta `COM5` (ou ajuste o comando).
- `pyserial`, `pillow`, `pyzbar`, `requests`, `beautifulsoup4` no Python local:

```powershell
python -m pip install pyserial pillow pyzbar requests beautifulsoup4
```

> No Windows, o `pyzbar` precisa da DLL `libzbar-64.dll` no `PATH`. Se a busca
> falhar, o módulo `zbar_compat.py` cuida do caminho no macOS; no Windows,
> instale o zbar ou use o container Docker (que já traz `libzbar0`).

---

## 2. Configuração (uma vez)

Cada repositório tem seu próprio `.env`. **Nunca commite nenhum dos dois.**

### `despensa/.env`

```dotenv
API_KEY_ESP32=<chave-usada-pelo-visualizador>
API_KEY_DASHBOARD=<mesma-chave-configurada-no-dashboard>
API_KEY=<fallback-legado>
API_URL=http://localhost:8000/upload
MAX_UPLOAD_MB=5
PRODUTO_LOOKUP_ATIVO=true
PRODUTO_LOOKUP_GROQ=true
PRODUTO_LOOKUP_TIMEOUT_S=6
GROQ_API_KEY=<sua-chave-groq>
GROQ_MODEL=openai/gpt-oss-120b
GROQ_API_URL=https://api.groq.com/openai/v1/chat/completions
```

Modelo completo em `despensa/.env.example`. A identificação por IA usa Groq
apenas se `PRODUTO_LOOKUP_GROQ=true` e `GROQ_API_KEY` estiver preenchida.

### `dashboard-despesa/.env`

```dotenv
DESPENSA_API_URL=http://localhost:8000
DESPENSA_API_KEY=<mesma API_KEY_DASHBOARD do despensa/.env>
```

> São **duas chaves diferentes**: a ESP32/visualizador usa `API_KEY_ESP32`;
> o dashboard usa `API_KEY_DASHBOARD`. O `DELETE /produto/{codigo}` e o
> `GET /produto/{codigo}/identificar-ia` **rejeitam a chave da ESP32 com 403**.

---

## 3. Ordem correta de subida

Sempre nesta ordem — a API primeiro, depois o dashboard, depois a câmera:

### Terminal 1 — API (Docker)

```powershell
cd "C:\Users\Admin\Documents\github-projects\Projeto Xaxa\despensa"
docker compose up -d --build
curl.exe http://localhost:8000/health
```

Esperado: `{"status":"ok","database":"connected"}`.
Documentação interativa: <http://localhost:8000/docs>.

### Terminal 2 — Dashboard

```powershell
cd "C:\Users\Admin\Documents\github-projects\Projeto Xaxa\dashboard-despesa"
bun install --frozen-lockfile
bun run dev
```

Abra <http://localhost:8080> e dê **Ctrl+F5** se já estava aberto.

### Terminal 3 — Câmera ao vivo

```powershell
cd "C:\Users\Admin\Documents\github-projects\Projeto Xaxa\despensa"
python visualizador.py COM5 8090
```

Abra <http://localhost:8090>. O visualize mostra o vídeo e o botão verde.

> **Atenção:** apenas um processo pode usar a `COM5`. Feche o Monitor Serial da
> Arduino IDE antes de iniciar. Para reiniciar:
>
> ```powershell
> $pids = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
> foreach ($p in $pids) { Stop-Process -Id $p -Force }
> ```

---

## 4. Fluxo completo — o que acontece

1. A câmera ao vivo mostra o vídeo pela USB (sem Wi-Fi na placa).
2. Um clique no **botão verde** da página ou no **botão físico do pino 42**
   dispara uma sessão: o PC pede até **5 frames** à placa com o comando `FOTO`.
3. Cada frame é salvo em `despensa/imagens/fotos/<sessao>/` e decodificado
   localmente. A sessão para no segundo código igual.
4. **Uma única foto** é enviada a `POST /upload` com o campo `sessao`, o que
   evita contagem duplicada em retry.
5. A API lê o EAN:
   - produto existente → `acao: "contado"`, `estoque-update` no SSE;
   - produto novo → consulta Open Food Facts → UPCitemdb → Cosmos. Se achar,
     cadastra e responde `acao: "cadastrado"`;
   - se não achar, responde `acao: "erro"` e dispara identificação com Groq em
     `BackgroundTasks`, emitindo `produto-identificando`.
6. No dashboard:
   - `produto-identificando` → card temporário animado/inativo na lista;
   - `produto-identificado` → o produto real substitui o card, modal **não abre**;
   - `produto-nao-identificado` → o modal de cadastro abre para preenchimento manual.

---

## 5. Verificações rápidas

```powershell
curl.exe http://localhost:8000/health
curl.exe -s -m 3 -o "$env:TEMP\stream_test.bin" http://localhost:8090/stream
(Get-Item "$env:TEMP\stream_test.bin").Length
```

O último número precisa ser diferente de zero (frames JPEG chegando).
Status da última captura: `curl.exe http://localhost:8090/status`.

### Testes

```powershell
# API — dentro de um container (o Python local pode não ter as libs)
cd despensa
docker run --rm -v "${PWD}:/src" -w /src despensa-api:latest `
  sh -c "pip install -q pytest httpx && python -m pytest tests -q"
# 92 testes

# Dashboard
cd dashboard-despesa
bun run lint
bun run test      # 179 testes
bun run build
```

---

## 6. Problemas comuns

| Sintoma | Causa / solução |
| --- | --- |
| Dashboard mostra "Serviço não configurado" | `.env` do dashboard ausente ou chave diferente de `API_KEY_DASHBOARD` |
| Dashboard não atualiza sozinho | Reconecte o SSE (reiniciar a API libera a conexão travada); 1 conexão por IP |
| `Acesso negado` na serial | Outro processo (Monitor Serial/IDE) segurando a COM5 |
| Foto sem EAN | Aponte para o código a ~10–20 cm, dentro da caixa verde, com boa luz |
| `produto-nao-identificado` | Nenhuma fonte encontrou o EAN; cadastre manualmente pelo modal |
| Lint falha só no CI | Erros de CRLF no Windows; rode `eslint --fix` num checkout com LF |
| 403 ao excluir | A chave usada é a da ESP32; exclusão exige `API_KEY_DASHBOARD` |

---

## 7. Atualizando os projetos (git)

Cada pasta é um repositório independente. Mudanças passam por PR:

```powershell
git -C despensa checkout main
git -C despensa pull --ff-only

git -C dashboard-despesa checkout main
git -C dashboard-despesa pull --ff-only
```

Se o merge trouxer mudanças na API, reconstrua o container:

```powershell
cd despensa
docker compose up -d --build
```

> No Windows, `core.autocrlf=true` pode gerar milhares de diffs falsos de CRLF.
> Use `git diff --ignore-space-at-eol --name-only` para ver mudanças reais e
> `git restore .` quando não houver nada além de quebra de linha.
