---
name: strava
description: Consulta os dados do Strava do usuário (somente leitura) via API v3 — perfil, atividades, corridas, pedaladas, pace, distância, frequência cardíaca, voltas, streams, estatísticas, zonas, equipamentos, rotas, clubes. Use quando o usuário pedir análise de treinos, resumo semanal/mensal, evolução, recordes ou qualquer pergunta sobre seus dados do Strava. Reads the user's Strava data (read-only).
---

# Strava (somente leitura)

CLI em Python (só stdlib): `scripts/strava.py` (caminho relativo a esta skill). Saída sempre JSON; erros em JSON no stderr com exit code 1. Tokens ficam em `~/.config/strava-skill/credentials.json` (chmod 600), com refresh automático.

## Fluxo

1. `python3 scripts/strava.py status` (sem rede). Se `authenticated` for `false`, siga [references/setup.md](references/setup.md): o usuário cria o app em strava.com/settings/api (exige assinatura Strava) e você roda `python3 scripts/strava.py auth` (abre o navegador; use `--manual` se não houver navegador/porta livre).
2. Consulte os dados com os comandos abaixo.
3. Resuma para o usuário convertendo unidades (veja "Unidades").

## Ferramentas (pedido → comando)

Preferidas para perguntas comuns. Parâmetros via `--json '{...}'` (ou `--param k=v`, repetível). Saída JSON normalizada.

| Pedido do usuário | Comando |
|---|---|
| "Minha conta está habilitada?" | `eligibility` (assinatura + escopos) |
| "O conector/API está funcionando?" | `health` (credenciais, API, uso do rate limit) |
| "Qual meu perfil / peso / unidades?" | `get_athlete_profile` |
| "Mostre meus treinos de corrida de setembro" | `list_activities --json '{"range_start":"2026-09-01T00:00:00","range_end":"2026-09-30T23:59:59","first":30,"ordering":"StartDateLocalDesc"}'` e filtre `sport_type` |
| "Como foi a atividade 123?" | `get_activity_performance --json '{"activity_id":"123"}'` (FC, potência, cadência, calorias, voltas, segmentos, melhores esforços) |
| "Mostre a FC ao longo da atividade 123" | `get_activity_streams --json '{"activity_id":"123","streams":["time","heart_rate","altitude"],"resolution":100}'` |
| "Quais minhas zonas de FC?" | `get_athlete_zones` |
| "Quantos km já fiz com meus tênis?" | `get_gear --json '{"gear_types":["Shoe"]}'` |
| "Quais clubes eu participo?" | `get_club_info --json '{"clubs_first":10}'` |

Detalhes:
- `list_activities`: `ordering` = `StartDateLocalDesc` (padrão) ou `StartDateLocalAsc`; `first` até 200; `after` = `page_info.end_cursor` da resposta anterior; `include_polyline` (rota codificada) e `include_tags` (commute, trainer, manual, private, workout_type, gear_id) opcionais. Datas ISO em horário local da atividade. Traz `relative_effort`, `kudos_count`, `pr_count` quando a API fornece.
- `get_activity_streams`: `streams` aceita `time, distance, heart_rate, altitude, speed, cadence, power, temperature, gps, grade`; `resolution` = nº máximo de pontos (amostragem uniforme).
- `get_gear` (`Bike`/`Shoe`) e `get_club_info` aceitam paginação (`clubs_first`, `clubs_after`).

### Não suportado (limitação da API v3 do Strava)

Não existem na API pública, então a skill **não** responde a: plano de treino, detalhes de treino de força (exercícios/séries), foco de treino atual, zonas de ritmo de corrida, eventos de clubes e zonas em data passada (`as_of_date`). Parâmetros desse tipo são ignorados com aviso em `notes`. Diga ao usuário que isso só está no app do Strava ou no conector MCP oficial (restrito a clientes Claude); não invente os dados.

## Comandos de baixo nível

| Comando | Uso |
|---|---|
| `athlete` / `stats` / `zones` | Perfil bruto; totais recent/ytd/all-time; zonas brutas |
| `activities [--after 30d] [--before D] [--type Run,Ride] [--all] [--limit N] [--compact]` | Lista atividades. Datas: `YYYY-MM-DD`, ISO, epoch ou `Nd` |
| `activity <id> [--all-efforts]` | Detalhe bruto (splits, segmentos, calorias, descrição) |
| `laps <id>` / `activity-zones <id>` | Voltas / tempo por zona |
| `streams <id> [--keys time,heartrate,...] [--every N]` | Séries temporais brutas |
| `gear <id>` | Equipamento (id vem de `gear_id`, ex.: `b12345`) |
| `routes` / `clubs` / `starred-segments` | Rotas, clubes, segmentos favoritos |
| `raw <path> [--param k=v]` | GET em qualquer endpoint da API v3 |
| `status` / `auth` / `logout` | Estado, autorização, remover tokens locais |

Exemplos:
```bash
python3 scripts/strava.py list_activities --json '{"first":10}'
python3 scripts/strava.py activities --after 7d --compact
python3 scripts/strava.py streams 123456789 --keys time,heartrate --every 10
```

## Boas práticas

- Prefira as ferramentas acima; busque `get_activity_performance`/`get_activity_streams` só quando necessário.
- `list_activities` com `ordering` diferente da ordem natural da API ou com filtro `range_start` lê até 10 páginas de 200 para ordenar corretamente.
- Ordem do comando `activities`: sem `--after` da mais recente para a mais antiga; com `--after` da mais antiga para a mais recente (e `--limit` corta as mais novas).
- Rate limit: 200 req/15 min e 2.000/dia. `--all` pagina até `--max-pages` (padrão 10 × `--per-page`, máx. 200 por página). O script avisa em stderr acima de 80%. Em 429, pare e informe o usuário.
- Nunca imprima, cole em respostas ou comite `client_secret`, `access_token` ou `refresh_token`. `status` não os exibe.
- 403/escopo insuficiente: reautorize com `auth` e marque todas as caixas (atividades privadas exigem `activity:read_all`).
- Somente leitura: não há comandos de escrita/upload nesta skill.

## Unidades (API → exibição)

- `distance` m → km (÷1000); `moving_time`/`elapsed_time` s → h:min.
- `average_speed` m/s → km/h (×3,6); pace corrida = 1000/velocidade s/km (`--compact` já traz `avg_pace`).
- `total_elevation_gain` em m; datas `start_date_local` já no fuso do atleta.

Mais detalhes de endpoints e campos: [references/api-notes.md](references/api-notes.md).
