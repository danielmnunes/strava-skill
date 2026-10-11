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

## Comandos

| Comando | Uso |
|---|---|
| `athlete` / `stats` / `zones` | Perfil; totais recent/ytd/all-time; zonas de FC e potência |
| `activities [--after 30d] [--before D] [--type Run,Ride] [--all] [--limit N] [--compact]` | Lista atividades. Datas: `YYYY-MM-DD`, ISO, epoch ou `Nd` |
| `activity <id> [--all-efforts]` | Detalhe (splits, segmentos, calorias, descrição) |
| `laps <id>` / `activity-zones <id>` | Voltas / tempo por zona |
| `streams <id> [--keys time,heartrate,...] [--every N]` | Séries temporais (grandes: use `--every`) |
| `gear <id>` | Equipamento (id vem de `gear_id`, ex.: `b12345`) |
| `routes` / `clubs` / `starred-segments` | Rotas, clubes, segmentos favoritos |
| `raw <path> [--param k=v]` | GET em qualquer endpoint da API v3 |
| `status` / `auth` / `logout` | Estado, autorização, remover tokens locais |

Exemplos:
```bash
python3 scripts/strava.py activities --after 7d --compact
python3 scripts/strava.py activities --after 2026-01-01 --type Run --all --compact
python3 scripts/strava.py streams 123456789 --keys time,heartrate --every 10
```

## Boas práticas

- Prefira `--compact` em listagens; busque `activity <id>`/`streams` só quando necessário.
- Ordem: sem `--after` a lista vem da mais recente para a mais antiga; com `--after` vem da mais antiga para a mais recente (e `--limit` corta as mais novas).
- Rate limit: 200 req/15 min e 2.000/dia. `--all` pagina até `--max-pages` (padrão 10 × `--per-page`, máx. 200 por página). O script avisa em stderr acima de 80%. Em 429, pare e informe o usuário.
- Nunca imprima, cole em respostas ou comite `client_secret`, `access_token` ou `refresh_token`. `status` não os exibe.
- 403/escopo insuficiente: reautorize com `auth` e marque todas as caixas (atividades privadas exigem `activity:read_all`).
- Somente leitura: não há comandos de escrita/upload nesta skill.

## Unidades (API → exibição)

- `distance` m → km (÷1000); `moving_time`/`elapsed_time` s → h:min.
- `average_speed` m/s → km/h (×3,6); pace corrida = 1000/velocidade s/km (`--compact` já traz `avg_pace`).
- `total_elevation_gain` em m; datas `start_date_local` já no fuso do atleta.

Mais detalhes de endpoints e campos: [references/api-notes.md](references/api-notes.md).
