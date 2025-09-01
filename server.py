import os, subprocess, shutil, re, asyncio
from pathlib import Path
from typing import Optional, List, Literal
import tomllib
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query, HTTPException, Body
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn
from pydantic import BaseModel, Field

import pyautogui
import mss
from pywinauto import Desktop
import pyperclip

# ================== Config & Paths ==================
ROOT = Path(__file__).parent
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))

HOST = CFG['server']['host']
PORT = int(CFG['server']['port'])
TOKEN = (CFG.get('security', {}).get('token') or "").strip()
DISABLED = bool(CFG.get('security', {}).get('disabled', False))
FEAT = CFG.get('features', {})
ALLOW = CFG.get('run', {}).get('allowlist', {})

STATIC = ROOT / "static"
SHOTS = ROOT / 'shots'
SHOTS.mkdir(exist_ok=True)

app = FastAPI(title="CopilotPC Lite", version="0.4")

# ================== Logging ==================
import logging
from datetime import datetime

LOGFILE = ROOT / "copilotpc.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def log_event(event: str, detail=None):
    msg = event
    if detail is not None:
        import json
        try:
            msg += " " + json.dumps(detail, ensure_ascii=False)
        except:
            msg += " " + str(detail)
    logging.info(msg)

# ================== UI ==================
@app.get("/", response_class=HTMLResponse)
def ui():
    return FileResponse(STATIC / "ui.html")

# ================== Playwright (optionnel, async) ==================
PW_ENABLED = bool(FEAT.get('browser_playwright', False))
if PW_ENABLED:
    from playwright.async_api import async_playwright, BrowserContext, Page
    pw = None
    ctx: Optional[BrowserContext] = None
    page: Optional[Page] = None

    @app.on_event("startup")
    async def _pw_start():
        global pw, ctx, page
        pw = await async_playwright().start()
        user_data = str(ROOT / "pw-user-data")
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=False,
            args=["--start-maximized"],
        )
        page = await ctx.new_page()

    @app.on_event("shutdown")
    async def _pw_stop():
        global pw, ctx, page
        try:
            if page and not page.is_closed(): await page.close()
            if ctx: await ctx.close()
            if pw: await pw.stop()
        except Exception:
            pass
else:
    page = None

# ================== Models ==================
class OneAction(BaseModel):
    type: Literal['goto','click','fill','type','press','wait','eval','screenshot']
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    expression: Optional[str] = None
    timeout_ms: Optional[int] = 10000

class ScriptBody(BaseModel):
    steps: List[OneAction] = Field(default_factory=list)

# ================== Security ==================
def require_enabled():
    global DISABLED
    if DISABLED:
        raise HTTPException(423, "CopilotPC is disabled (panic mode)")

def auth(request: Request):
    if TOKEN:
        t = request.query_params.get('token','')
        if t != TOKEN:
            raise HTTPException(401, "Unauthorized")

# ================== Basic Routes ==================
@app.get("/panic")
def panic():
    global DISABLED; DISABLED = True
    return {"status":"ok","disabled":True}

@app.get("/enable")
def enable():
    global DISABLED; DISABLED = False
    return {"status":"ok","disabled":False}

# OS: mouse / keyboard / clipboard
@app.get("/os/mouse/move")
def mouse_move(request: Request, x:int=Query(...), y:int=Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('mouse',True): raise HTTPException(403,"mouse disabled")
    pyautogui.moveTo(x,y,duration=0.1)
    return {"status":"ok"}

@app.get("/os/mouse/click")
def mouse_click(request: Request, button:str="left", clicks:int=1):
    auth(request); require_enabled()
    if not FEAT.get('mouse',True): raise HTTPException(403,"mouse disabled")
    pyautogui.click(button=button, clicks=int(clicks))
    return {"status":"ok"}

@app.get("/os/clipboard/set")
def cb_set(request: Request, text: str = Query("")):
    auth(request); require_enabled()
    pyperclip.copy(text or "")
    return {"status": "ok", "len": len(text or "")}

@app.get("/os/keyboard/paste")
def kb_paste(request: Request):
    auth(request); require_enabled()
    pyautogui.hotkey('ctrl', 'v')
    return {"status":"ok","action":"paste"}

@app.get("/os/keyboard/type")
def kb_type(request: Request, text:str=Query("")):
    auth(request); require_enabled()
    if not FEAT.get('keyboard',True): raise HTTPException(403,"keyboard disabled")
    pyautogui.typewrite(text)
    return {"status":"ok"}

@app.get("/os/keyboard/hotkey")
def kb_hotkey(request: Request, keys:str=Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('keyboard',True): raise HTTPException(403,"keyboard disabled")
    parts = [k.strip() for k in keys.split('+') if k.strip()]
    pyautogui.hotkey(*parts)
    return {"status":"ok","keys":parts}

# Window
@app.get("/window/activate")
def win_activate(request: Request, title:str=Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('window',True): raise HTTPException(403,"window feature disabled")
    d = Desktop(backend="uia")
    for w in d.windows():
        t = w.window_text() or ""
        if title.lower() in t.lower():
            w.set_focus()
            return {"status":"ok","window":t}
    raise HTTPException(404,"window not found")

@app.get("/window/click_center")
def win_click_center(request: Request, title:str=Query(...)):
    auth(request); require_enabled()
    d = Desktop(backend="uia")
    for w in d.windows():
        t = w.window_text() or ""
        if title.lower() in t.lower():
            w.set_focus()
            rect = w.rectangle()
            cx,cy = (rect.left+rect.right)//2,(rect.top+rect.bottom)//2
            pyautogui.moveTo(cx,cy,duration=0.1); pyautogui.click()
            return {"status":"ok","window":t,"x":cx,"y":cy}
    raise HTTPException(404,"window not found")

# Screenshot
@app.get("/screen/screenshot")
def screenshot():
    if not FEAT.get('screenshot',True): raise HTTPException(403,"screenshot disabled")
    with mss.mss() as sct:
        path = SHOTS / "shot.png"
        sct.shot(mon=-1, output=str(path))
    return {"status":"ok","path":str(path),"url":f"/shots/{path.name}"}

@app.get("/shots/{name}")
def serve_shot(name:str):
    return FileResponse(SHOTS / name)

# Run allowlisted apps
@app.get("/app/run")
def app_run(request: Request, name:str=Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('run_apps',True): raise HTTPException(403,"run apps disabled")
    if name not in ALLOW: raise HTTPException(403,f"{name} not in allowlist")
    cmd = ALLOW[name]; exe=cmd[0]
    if not shutil.which(exe): raise HTTPException(404,f"not found: {exe}")
    subprocess.Popen(cmd); return {"status":"ok","launched":cmd}

# Browser open
@app.get("/browser/open")
def browser_open(request: Request, url:str=Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('browser_open',True): raise HTTPException(403,"browser_open disabled")
    import webbrowser
    if not url.startswith("http"): url="https://"+url
    webbrowser.open_new_tab(url)
    return {"status":"ok","opened":url}

# Browser (Playwright)
@app.post("/browser/script")
async def browser_script(request: Request, body: ScriptBody = Body(...)):
    auth(request); require_enabled()
    if not PW_ENABLED: raise HTTPException(403, "Playwright not enabled. Set features.browser_playwright = true and install it.")
    if page is None:  raise HTTPException(500, "Playwright page not ready.")
    results = []
    for a in body.steps:
        t = a.type
        if t == 'goto':
            if not a.url: raise HTTPException(400, "url required")
            await page.goto(a.url); results.append({"ok":True}); continue
        if t == 'click':
            if not a.selector: raise HTTPException(400, "selector required")
            await page.click(a.selector, timeout=a.timeout_ms); results.append({"ok":True}); continue
        if t == 'fill':
            if not a.selector: raise HTTPException(400, "selector required")
            await page.fill(a.selector, a.text or "", timeout=a.timeout_ms); results.append({"ok":True}); continue
        if t == 'type':
            await page.keyboard.type(a.text or ""); results.append({"ok":True}); continue
        if t == 'press':
            if not a.key: raise HTTPException(400, "key required")
            await page.keyboard.press(a.key); results.append({"ok":True}); continue
        if t == 'wait':
            if not a.selector: raise HTTPException(400, "selector required")
            await page.wait_for_selector(a.selector, timeout=a.timeout_ms); results.append({"ok":True}); continue
        if t == 'eval':
            expr = a.expression or "document.title"
            res = await page.evaluate(f"(function(){{ try{{ return {expr}; }}catch(e){{ return String(e); }} }})()")
            results.append({"ok":True,"result":res}); continue
        if t == 'screenshot':
            out = ROOT/'shots'/'playwright.png'
            await page.screenshot(path=str(out), full_page=True)
            results.append({"ok":True,"path":str(out)}); continue
        raise HTTPException(400, f"Unknown action: {t}")
    return {"ok":True,"results":results}

# ================== Helpers (OS + plans) ==================
def _open_url(u:str):
    import webbrowser
    if not u.startswith("http"): u="https://"+u
    webbrowser.open_new_tab(u); return u

def _focus(title:str):
    d=Desktop(backend="uia")
    for w in d.windows():
        t = w.window_text() or ""
        if title.lower() in t.lower():
            w.set_focus(); return t
    raise HTTPException(404,"window not found")

def _screenshot_json():
    with mss.mss() as sct:
        path=SHOTS/"shot.png"; sct.shot(mon=-1,output=str(path))
    return {"status":"ok","path":str(path),"url":f"/shots/{path.name}"}

WINDOW_TITLES = {
    "notepad": ["Bloc-notes", "Notepad", "Sans titre", "Untitled"],
}

def _focus_best(app_key: str, fallback_title: str = ""):
    titles = WINDOW_TITLES.get(app_key.lower(), [])
    tried = set()
    for t in titles + ([fallback_title] if fallback_title else []):
        tt = (t or "").strip()
        if not tt or tt.lower() in tried:
            continue
        tried.add(tt.lower())
        try:
            return _focus(tt)
        except HTTPException:
            continue
    return _focus("Chrome")

# Intents FR (regex)
INTENT_PATTERNS = [
    ("open_app",       re.compile(r"\b(ouvre|ouvrir|lance|d[ée]marre)\b\s+([a-z0-9 .+-]+)", re.I)),
    ("open_coinbase",  re.compile(r"\b(ouvre|ouvrir|va(?:s)? sur)\b.*\bcoinbase\b", re.I)),
    ("open_url",       re.compile(r"\b(ouvre|ouvrir|va(?:s)? sur)\b\s+(https?://\S+|\S+\.\S+)", re.I)),
    ("focus",          re.compile(r"\b(focus|active|mets au premier plan|donne le focus)\b\s+(.+)", re.I)),
    ("enter",          re.compile(r"\b(appuie|valide|enter|entr(é|e)e)\b", re.I)),
    ("tab",            re.compile(r"\b(tab|onglet suivant|passe au champ suivant)\b", re.I)),
    ("type_text",      re.compile(r"\b(tape|écrit|ecris|saisis)\b\s+(?:\"([^\"]+)\"|'([^']+)'|(.+))", re.I)),
    ("screenshot",     re.compile(r"\b(capture|screenshot|photo d'?écran)\b", re.I)),
]
DEFAULT_FOCUS = "Chrome"

# Alias appli -> clé allowlist
APP_ALIASES = {
    "notepad":"notepad","bloc-notes":"notepad","bloc notes":"notepad",
    "chrome":"chrome","google chrome":"chrome",
    "vscode":"vscode","vs code":"vscode","code":"vscode",
    "word":"word","microsoft word":"word",
    "excel":"excel","microsoft excel":"excel",
}
def resolve_app(name:str)->str:
    key = re.sub(r"\s+"," ",name.strip().lower())
    return APP_ALIASES.get(key,key)

async def run_plan(steps: list[dict])->list[dict]:
    out=[]
    for s in steps:
        k=s["type"]
        try:
            if k=="open":
                u=_open_url(s["url"]); out.append({"ok":True,"opened":u})
            elif k=="focus_best":
                app_key = s.get("app",""); fb = s.get("fallback","")
                t = _focus_best(app_key, fb); out.append({"ok": True, "window": t})
            elif k=="focus":
                t=_focus(s.get("title",DEFAULT_FOCUS)); out.append({"ok":True,"window":t})
            elif k=="type":
                pyautogui.typewrite(s.get("text","")); out.append({"ok":True})
            elif k=="hotkey":
                pyautogui.hotkey(*[x for x in s.get("keys","").split("+") if x]); out.append({"ok":True})
            elif k=="sleep":
                await asyncio.sleep(s.get("sec",0.8)); out.append({"ok":True})
            elif k=="screenshot":
                out.append(_screenshot_json())
            elif k=="run_app":
                name=s["name"]
                if name not in ALLOW: raise HTTPException(403,f"{name} not in allowlist")
                cmd = ALLOW[name]; exe=cmd[0]
                if not shutil.which(exe): raise HTTPException(404,f"not found: {exe}")
                subprocess.Popen(cmd); out.append({"ok":True,"launched":cmd})
            elif k=="playwright_script":
                if not PW_ENABLED: raise HTTPException(403,"Playwright not enabled")
                if page is None: raise HTTPException(500,"Playwright page not ready")
                res=[]
                for a in s.get("steps",[]):
                    t=a.get("type")
                    if t=="goto":      await page.goto(a["url"]); res.append({"ok":True})
                    elif t=="click":   await page.click(a["selector"], timeout=a.get("timeout_ms",10000)); res.append({"ok":True})
                    elif t=="fill":    await page.fill(a["selector"], a.get("text",""), timeout=a.get("timeout_ms",10000)); res.append({"ok":True})
                    elif t=="press":   await page.keyboard.press(a["key"]); res.append({"ok":True})
                    elif t=="wait":    await page.wait_for_selector(a["selector"], timeout=a.get("timeout_ms",10000)); res.append({"ok":True})
                    elif t=="screenshot":
                        outp = ROOT/'shots'/'playwright.png'
                        await page.screenshot(path=str(outp), full_page=True); res.append({"ok":True,"path":str(outp)})
                    else: res.append({"ok":False,"error":f"unknown {t}"})
                out.append({"ok":True,"playwright_results":res})
            else:
                out.append({"ok":False,"error":f"unknown {k}"})
            log_event("run_step_done", {"type": k, "status": "ok"})
        except Exception as e:
            log_event("run_step_error", {"type": k, "error": str(e)})
            out.append({"ok":False,"error":str(e),"step":s}); break
    return out

# -------- Interpréteur "mono-commande" --------
def interpret_single(text: str) -> list[dict]:
    t = text.strip()

    # open_app
    m = INTENT_PATTERNS[0][1].search(t)
    if m:
        raw = m.group(2)
        app = resolve_app(raw)
        return [
            {"type":"run_app","name":app},
            {"type":"sleep","sec":0.8},
            {"type":"focus_best","app":app, "fallback": raw},
        ]

    # coinbase
    m = INTENT_PATTERNS[1][1].search(t)
    if m:
        return [{"type":"focus","title":DEFAULT_FOCUS},
                {"type":"open","url":"https://www.coinbase.com/signin"},
                {"type":"sleep","sec":1.0}]

    # open_url
    m = INTENT_PATTERNS[2][1].search(t)
    if m:
        return [{"type":"focus","title":DEFAULT_FOCUS},{"type":"open","url":m.group(2)}]

    # focus
    m = INTENT_PATTERNS[3][1].search(t)
    if m:
        return [{"type":"focus","title":m.group(2)}]

    # enter
    m = INTENT_PATTERNS[4][1].search(t)
    if m:
        return [{"type":"type","text":"\r"}]

    # tab
    m = INTENT_PATTERNS[5][1].search(t)
    if m:
        return [{"type":"type","text":"\t"}]

    # type_text (avec guillemets gérés)
    m = INTENT_PATTERNS[6][1].search(t)
    if m:
        txt = m.group(2) or m.group(3) or m.group(4) or ""
        return [{"type":"type","text": txt}]

    # screenshot
    m = INTENT_PATTERNS[7][1].search(t)
    if m:
        return [{"type":"screenshot"}]

    # fallback "ouvre ..."
    if t.lower().startswith("ouvre "):
        url = t[6:].strip()
        return [{"type":"focus","title":DEFAULT_FOCUS},{"type":"open","url":url}]

    return []

# -------- Interpréteur multi-clauses --------
_CLAUSE_SPLIT = re.compile(r'\s*(?:\bet\b|\bpuis\b|\bensuite\b|\baprès\b|,|;)\s+', re.IGNORECASE)

def interpret_command(text: str) -> list[dict]:
    raw = text.strip()
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(raw) if c.strip()]
    whole: list[dict] = []
    for i, clause in enumerate(clauses):
        sub = interpret_single(clause)
        if not sub:
            m = re.search(r'\b(écrit|ecris|tape|saisis)\b\s+(.+)', clause, re.IGNORECASE)
            if m:
                sub = [{"type":"type","text": m.group(2)}]
        whole.extend(sub)
        if i < len(clauses)-1:
            whole.append({"type":"sleep","sec":0.4})
    return whole

# ================== Endpoints Agent ==================
class AgentCommand(BaseModel):
    text: str

@app.post("/agent/command")
async def agent_command(request: Request, payload: AgentCommand = Body(...)):
    auth(request); require_enabled()
    log_event("agent_command", {"text": payload.text})
    plan = interpret_command(payload.text)
    if not plan:
        log_event("no_intent_detected", {"text": payload.text})
        return {"ok": False, "reason": "no_intent_detected"}
    results = await run_plan(plan)
    log_event("plan_executed", {"plan": plan, "results": results})
    return {"ok": True, "plan": plan, "results": results}

# =============== Mode LLM (Chat Completions + Tools) ===============
try:
    import json, httpx
    HAVE_HTTPX = True
except Exception:
    HAVE_HTTPX = False



load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""   # <-- via env
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

class LLMPlanner:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    KNOWN_SITES = {
        "coinbase": "https://www.coinbase.com/signin",
        "gmail": "https://mail.google.com/",
        "youtube": "https://www.youtube.com/",
        "linkedin": "https://www.linkedin.com/login",
        "tradingview": "https://www.tradingview.com/",
    }

    def tool_schema(self):
        return [
            {
                "type":"function",
                "function":{
                    "name":"run_app",
                    "description":"Lancer une application depuis l'allowlist (config.toml [run.allowlist]).",
                    "parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"focus_window",
                    "description":"Donner le focus à une fenêtre dont le titre contient 'title'.",
                    "parameters":{"type":"object","properties":{"title":{"type":"string"}},"required":["title"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"open_url",
                    "description":"Ouvrir une URL dans le navigateur par défaut (ou Chrome).",
                    "parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"type_text",
                    "description":"Taper du texte au clavier dans la fenêtre active (petits textes ASCII).",
                    "parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"paste_text",
                    "description":"Colle un texte (UTF-8/multi-lignes) via presse-papier dans la fenêtre active.",
                    "parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"hotkey",
                    "description":"Envoyer une combinaison de touches (ex: 'ctrl+l', 'ctrl+enter').",
                    "parameters":{"type":"object","properties":{"keys":{"type":"string"}},"required":["keys"]}
                }
            },
            {
                "type":"function",
                "function":{
                    "name":"screenshot",
                    "description":"Faire une capture d'écran et renvoyer l'URL locale.",
                    "parameters":{"type":"object","properties":{}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sleep",
                    "description": "Pause en secondes (utile pour laisser une fenêtre s'ouvrir).",
                    "parameters": {"type": "object",
                                   "properties": {"sec": {"type": "number", "minimum": 0, "maximum": 5}},
                                   "required": ["sec"]}
                }
            },
        ]

    async def tool_dispatch(self, name, args):
        # Utilitaires réseau locaux
        async def _get(path, params=None):
            params = params or {}
            if self.token:
                params["token"] = self.token
            async with httpx.AsyncClient(timeout=30) as cli:
                r = await cli.get(f"{self.base}{path}", params=params)
                ok = r.status_code < 400
                try:
                    data = r.json()
                except Exception:
                    data = {"raw": await r.aread()}
                return ok, r.status_code, data

        if name == "run_app":
            app_name = args.get("name", "")
            try:
                app_key = resolve_app(app_name)
            except Exception:
                app_key = app_name
            ok, status, data = await _get("/app/run", {"name": app_key})
            if not ok:
                return {"ok": False, "status": status, "error": data, "tried_name": app_name, "normalized_key": app_key}
            return data

        if name == "sleep":
            sec = float(args.get("sec", 0.5))
            sec = max(0.0, min(5.0, sec))
            await asyncio.sleep(sec)
            return {"ok": True, "slept": sec}

        if name == "focus_window":
            ok, status, data = await _get("/window/activate", {"title": args["title"]})
            return data if ok else {"ok": False, "status": status, "error": data}

        if name == "open_url":
            ok, status, data = await _get("/browser/open", {"url": args["url"]})
            return data if ok else {"ok": False, "status": status, "error": data}

        if name == "paste_text":
            txt = args.get("text")
            if not isinstance(txt, str) or not txt.strip():
                return {"ok": False, "status": 400, "error": "missing_text_argument"}
            ok1, st1, d1 = await _get("/os/clipboard/set", {"text": txt})
            ok2, st2, d2 = await _get("/os/keyboard/paste")
            return {"ok": ok1 and ok2, "clipboard": d1, "paste": d2}

        if name == "type_text":
            txt = args.get("text")
            if not isinstance(txt, str) or not txt.strip():
                return {"ok": False, "status": 400, "error": "missing_text_argument"}
            multiline = ("\n" in txt) or ("\r" in txt)
            too_long = len(txt) > 300
            has_non_ascii = any(ord(c) > 127 for c in txt)
            if multiline or too_long or has_non_ascii:
                return await self.tool_dispatch("paste_text", {"text": txt})
            ok, status, data = await _get("/os/keyboard/type", {"text": txt})
            return data if ok else {"ok": False, "status": status, "error": data}

        if name == "hotkey":
            ok, status, data = await _get("/os/keyboard/hotkey", {"keys": args["keys"]})
            return data if ok else {"ok": False, "status": status, "error": data}

        if name == "screenshot":
            ok, status, data = await _get("/screen/screenshot")
            return data if ok else {"ok": False, "status": status, "error": data}

        return {"error": f"unknown tool {name}"}

    async def run(self, user_text: str):
        if not OPENAI_API_KEY:
            return {"ok": False, "error": "OPENAI_API_KEY missing"}
        if not HAVE_HTTPX:
            return {"ok": False, "error": "httpx not installed (pip install httpx)"}

        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

        # ----------------- (1) Pré-annotation facultative -----------------
        KNOWN_SITES = {
            "coinbase": "https://www.coinbase.com/signin",
            "gmail": "https://mail.google.com/",
            "youtube": "https://www.youtube.com/",
            "linkedin": "https://www.linkedin.com/login",
            "tradingview": "https://www.tradingview.com/",
        }
        import re
        hint = ""
        m = re.search(r"\bouvre(?:r)?\s+([a-z0-9\-_.]+)\b", user_text, flags=re.I)
        if m:
            key = m.group(1).lower()
            if key in KNOWN_SITES:
                hint = f"(Astuce: {key} → {KNOWN_SITES[key]}) "
        user_text = hint + user_text

        # ----------------- (2) Prompt système + messages -----------------
        try:
            allowed_apps = ", ".join(sorted(ALLOW.keys())) or "(aucune)"
        except Exception:
            allowed_apps = "(indisponible)"

        system_prompt = (
            "Tu es un planificateur d'actions pour un PC local.\n"
            "- Si la cible est un SITE/Service (ex: coinbase, gmail, linkedin, youtube), utilise l'outil open_url avec l'URL complète.\n"
            "- N'utilise run_app QUE pour les applications installées et autorisées.\n"
            f"- Applications autorisées (allowlist): {allowed_apps}\n"
            "- Pour ouvrir une application puis écrire dedans, séquence recommandée : run_app -> sleep(0.8) -> focus_window('Bloc-notes' ou 'Notepad') -> type_text (ou paste_text pour les longs textes).\n"
            "- Après chaque étape, si l'action échoue (ex: not in allowlist), essaie une stratégie alternative (ex: open_url).\n"
            "- Utilise les outils pour agir; donne une courte réponse finale quand c'est terminé."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]

        while True:
            payload = {
                "model": OPENAI_MODEL,
                "messages": messages,
                "tools": self.tool_schema(),
                "tool_choice": "auto",
                "temperature": 0.2,
            }

            url = f"{OPENAI_BASE}/chat/completions"
            async with httpx.AsyncClient(timeout=120) as cli:
                resp = await cli.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    try:
                        return {"ok": False, "error": f"{resp.status_code} {resp.reason_phrase}", "body": resp.json()}
                    except Exception:
                        return {"ok": False, "error": f"{resp.status_code} {resp.reason_phrase}",
                                "body": await resp.aread()}
                data = resp.json()

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls") or []
            final_text = message.get("content")

            if tool_calls:
                # 1) Ajouter le message assistant contenant les tool_calls
                messages.append(message)

                # 2) Exécuter chaque tool et répondre avec tool_call_id
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args_json = tc["function"].get("arguments") or "{}"
                    try:
                        import json
                        args = json.loads(args_json) if isinstance(args_json, str) else args_json
                    except Exception:
                        args = {}
                    result = await self.tool_dispatch(name, args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False)
                    })

                # boucle
                continue

            return {"ok": True, "final": final_text or "(ok)"}

class LLMCommand(BaseModel):
    text: str

@app.post("/agent/llm")
async def agent_llm(request: Request, payload: LLMCommand = Body(...)):
    auth(request); require_enabled()
    log_event("agent_llm_request", {"text": payload.text})
    planner = LLMPlanner(base_url=f"http://{HOST}:{PORT}", token=(TOKEN or ""))
    res = await planner.run(payload.text)
    log_event("agent_llm_result", res)
    return res

# ================== Runner ==================
if __name__=="__main__":
    uvicorn.run(app,host=HOST,port=PORT)
