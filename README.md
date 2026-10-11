# strava-skill

Agent skill para consultar seus próprios dados do Strava (somente leitura) via API v3: perfil, atividades, estatísticas, zonas, voltas, streams, equipamentos, rotas e clubes. / Read-only [Agent Skill](https://skills.sh) for your own Strava data.

## Instalação

```bash
npx skills add danielmnunes/strava-skill
```

Funciona com Claude Code, Copilot, Codex, Cursor e outros agentes suportados pelo [skills CLI](https://github.com/vercel-labs/skills).

## Configuração (uma vez)

1. Crie um app em <https://www.strava.com/settings/api> (exige assinatura Strava) com **Authorization Callback Domain** = `localhost`.
2. Rode `python3 scripts/strava.py auth` (dentro da pasta da skill instalada) e informe Client ID e Client Secret.

Detalhes em [`skills/strava/references/setup.md`](skills/strava/references/setup.md).

## Uso

Depois de instalada, peça ao agente, por exemplo: "resuma meus treinos da última semana", "mostre meus treinos de corrida de setembro" ou "quantos km já fiz com meus tênis?".

Ferramentas cobertas (via API v3): `eligibility`, `health`, `get_athlete_profile`, `list_activities`, `get_activity_performance`, `get_activity_streams`, `get_athlete_zones`, `get_gear`, `get_club_info`.

**Não suportado** (a API pública do Strava não expõe): plano de treino, detalhes de treino de força, foco de treino, zonas de ritmo e eventos de clube.

O script também funciona sozinho (Python 3, só stdlib):

```bash
python3 skills/strava/scripts/strava.py list_activities --json '{"first":10,"ordering":"StartDateLocalDesc"}'
python3 skills/strava/scripts/strava.py activities --after 30d --type Run --compact
```

## Segurança

- Tokens ficam em `~/.config/strava-skill/credentials.json` (chmod 600), fora do repositório.
- Somente leitura: não cria, edita nem apaga nada no Strava.
- Respeita os limites da API (200 req/15 min, 2.000/dia) e o [Strava API Agreement](https://www.strava.com/legal/api).

## Licença

MIT
