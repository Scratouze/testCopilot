# CopilotPC Lite v0.1 (Windows-first)

Objectif : un **co-pilote PC** local avec contrôle OS (souris, clavier, fenêtres), screenshots, exécution d'apps autorisées, 
et **automatisation navigateur** (option Playwright). Pensé pour être piloté par ChatGPT (mode agent) ou par toi via des endpoints HTTP.

## Installation (Windows)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# (Option navigateur avancé)
pip install playwright==1.45.0
playwright install chromium
python server.py
```
UI/API locale: http://127.0.0.1:8730

### Configuration rapide
- L'application charge automatiquement un fichier `.env` (grâce à `python-dotenv`).
- Variables disponibles :
  - `COPILOTPC_HOST` / `COPILOTPC_PORT`
  - `COPILOTPC_TOKEN` (prioritaire sur `config.toml`)
  - `COPILOTPC_DISABLED` (met le mode panic à `true` au démarrage)
  - `COPILOTPC_BASE_URL` (pour le planner LLM)
  - `COPILOTPC_FEATURE_<NOM>` pour surcharger `features` du `config.toml` (ex: `COPILOTPC_FEATURE_MOUSE=false`).
  - `COPILOTPC_CONFIG` pour pointer vers un autre fichier TOML (chemin absolu ou relatif au dossier du serveur).

## Sécurité
- Par défaut: **local only** (127.0.0.1).
- Mets un `TOKEN` dans `server.py` pour exiger `?token=...`.
- Allowlist d'apps dans `config.toml` (section [run.allowlist]).
- Endpoints OS sensibles sont activables/désactivables par flags dans `config.toml`.

## Endpoints clés
**OS / Input**
- `GET /os/mouse/move?x=100&y=200`
- `GET /os/mouse/click?button=left&clicks=1`
- `GET /os/keyboard/type?text=Bonjour`
- `GET /os/keyboard/hotkey?keys=ctrl+s`  (sépare par `+`, ex: `alt+tab` => à éviter si non désiré)

**Fenêtres**
- `GET /window/activate?title=Notepad`  (active fenêtre dont le titre contient la chaîne)

**Écran**
- `GET /screen/screenshot`  (retour JSON avec chemin + sert l'image à `/shots/<file>`)
- `GET /status` (nécessite le token si configuré) pour vérifier les features actifs, le chemin de config chargé et l'état du serveur.

**Apps (allowlist)**
- `GET /app/run?name=calc`  (voir `[run.allowlist]` dans `config.toml`)

**Navigateur**
- Simple: `GET /browser/open?url=https://...`
- Avancé (Playwright, optionnel):
  - `POST /browser/script` avec JSON:
    ```json
    { "steps": [
      {"type":"goto","url":"https://google.com"},
      {"type":"fill","selector":"input[name=q]","text":"airbus rdt"},
      {"type":"press","key":"Enter"}
    ]}
    ```

**Presse-papiers**
- `GET /clipboard/get` / `GET /clipboard/set?text=...`

**Panic / Kill-switch**
- `GET /panic`  (désactive temporairement les actions OS jusqu’au redémarrage ou `GET /enable`)

## Notes
- `pyautogui` peut demander des permissions d’accessibilité selon l’OS.
- `pywinauto` utilise l’accessibilité Windows pour activer des fenêtres.
- Pour exposition publique (Tunnel), active `TOKEN` et garde une allowlist stricte.
