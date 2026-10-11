# Notas da API Strava v3

Base: `https://www.strava.com/api/v3` · Docs: https://developers.strava.com/docs/reference/

## Endpoints usados

| Comando | Endpoint | Escopo |
|---|---|---|
| athlete | `GET /athlete` | `read` (`profile:read_all` p/ perfil completo) |
| stats | `GET /athletes/{id}/stats` | `read` |
| zones | `GET /athlete/zones` | `profile:read_all` |
| activities | `GET /athlete/activities?before&after&page&per_page` | `activity:read` / `activity:read_all` (privadas) |
| activity | `GET /activities/{id}` | idem |
| laps | `GET /activities/{id}/laps` | idem |
| activity-zones | `GET /activities/{id}/zones` | idem (requer assinatura) |
| streams | `GET /activities/{id}/streams?keys&key_by_type=true` | idem |
| gear | `GET /gear/{id}` | `profile:read_all` |
| routes | `GET /athletes/{id}/routes` | `read` / `read_all` (privadas) |
| clubs | `GET /athlete/clubs` | `read` |
| starred-segments | `GET /segments/starred` | `read` |

OAuth: `GET /oauth/authorize` → `POST /oauth/token` (`authorization_code` e `refresh_token`). Access token expira em 6 h.

## Streams — chaves

`time, distance, latlng, altitude, velocity_smooth, heartrate, cadence, watts, temp, moving, grade_smooth`. Nem toda atividade tem todas; chaves ausentes simplesmente não retornam.

## Campos úteis (atividade)

`id, name, sport_type, start_date_local, distance (m), moving_time (s), elapsed_time (s), total_elevation_gain (m), average_speed (m/s), max_speed, average_heartrate, max_heartrate, average_watts, kilojoules, calories (só em detalhe), gear_id, splits_metric, best_efforts (corridas), segment_efforts`.

## Limites

- Padrão: 200 req/15 min e 2.000 req/dia (leitura). Headers `X-RateLimit-Limit` / `X-RateLimit-Usage` (formato `15min,dia`).
- `per_page` máx. 200. Listas retornam `[]` ao fim da paginação.
- Ao usar dados de outros atletas ou publicá-los, respeite o [API Agreement](https://www.strava.com/legal/api).
