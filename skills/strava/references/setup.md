# Setup (uma vez)

1. Tenha uma conta Strava com assinatura (requisito para criar apps de API).
2. Em https://www.strava.com/settings/api crie um app. Em **Authorization Callback Domain** use `localhost`.
3. Copie **Client ID** e **Client Secret** (confidencial).
4. Rode:
   ```bash
   python3 scripts/strava.py auth
   ```
   Informe Client ID/Secret quando pedido (ou use `--client-id`/`--client-secret`, ou as variáveis `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET`). O navegador abre a tela de autorização do Strava; o script captura o callback em `http://localhost:8754/exchange_token`.
   - Porta ocupada: `--port 9000`.
   - Sem navegador local / callback não chega: `--manual` (autorize e cole a URL final, mesmo que a página dê erro).
   - Marque **todas** as caixas de escopo (`read`, `read_all`, `profile:read_all`, `activity:read_all`).
5. Confirme: `python3 scripts/strava.py status` e `python3 scripts/strava.py athlete`.

Novos apps ficam em "single-player mode": só a sua conta consegue autorizar — suficiente para uso pessoal.

## Arquivos e variáveis

- Credenciais: `~/.config/strava-skill/credentials.json` (0600). Altere o diretório com `STRAVA_CONFIG_DIR`.
- Access token dura 6 h; o script renova sozinho com o refresh token.
- `logout` apaga tokens locais. Para revogar de fato, use https://www.strava.com/settings/apps.
