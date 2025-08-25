import os, subprocess, shutil, threading, re
from pathlib import Path
from typing import Optional, List, Literal, Any
import tomllib
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
from pydantic import BaseModel, Field

import pyautogui
import mss
from PIL import Image
from pywinauto import Desktop

ROOT = Path(__file__).parent
CFG = tomllib.loads(Path(ROOT/'config.toml').read_text(encoding='utf-8'))

HOST = CFG['server']['host']
PORT = int(CFG['server']['port'])
TOKEN = (CFG.get('security',{}).get('token') or "").strip()
DISABLED = bool(CFG.get('security',{}).get('disabled', False))
FEAT = CFG.get('features', {})
ALLOW = CFG.get('run',{}).get('allowlist',{})

SHOTS = ROOT/'shots'; SHOTS.mkdir(exist_ok=True)

app = FastAPI(title="CopilotPC Lite", version="0.1")

# --- Optional Playwright imports (only if enabled) ---
PW_ENABLED = bool(FEAT.get('browser_playwright', False))
if PW_ENABLED:
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=False, args=['--start-maximized'])
    _ctx = _browser.new_context(viewport={'width':1600,'height':900})
    _page = _ctx.new_page()

# --------- Models for browser script ----------
class OneAction(BaseModel):
    type: Literal['goto','click','fill','type','press','wait','eval','screenshot']
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    expression: Optional[str] = None
    timeout_ms: Optional[int] = 10000
    path: Optional[str] = None
class ScriptBody(BaseModel):
    steps: List[OneAction] = Field(default_factory=list)

# --------------- Helpers -----------------
def require_enabled():
    global DISABLED
    if DISABLED:
        raise HTTPException(423, "CopilotPC is disabled (panic mode)")

def auth(request: Request):
    if TOKEN:
        t = request.query_params.get('token','')
        if t != TOKEN:
            raise HTTPException(401, "Unauthorized")

# --------------- Routes ------------------
@app.get("/panic")
def panic():
    global DISABLED
    DISABLED = True
    return {"status":"ok","disabled":True}

@app.get("/enable")
def enable():
    global DISABLED
    DISABLED = False
    return {"status":"ok","disabled":False}

# OS: Mouse
@app.get("/os/mouse/move")
def mouse_move(request: Request, x: int = Query(...), y: int = Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('mouse', True): raise HTTPException(403, "mouse disabled")
    pyautogui.moveTo(x, y, duration=0.1)
    return {"status":"ok"}

@app.get("/os/mouse/click")
def mouse_click(request: Request, button: str = Query("left"), clicks: int = Query(1)):
    auth(request); require_enabled()
    if not FEAT.get('mouse', True): raise HTTPException(403, "mouse disabled")
    pyautogui.click(button=button, clicks=int(clicks))
    return {"status":"ok"}

# OS: Keyboard
@app.get("/os/keyboard/type")
def kb_type(request: Request, text: str = Query("")):
    auth(request); require_enabled()
    if not FEAT.get('keyboard', True): raise HTTPException(403, "keyboard disabled")
    pyautogui.typewrite(text)
    return {"status":"ok"}

@app.get("/os/keyboard/hotkey")
def kb_hotkey(request: Request, keys: str = Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('keyboard', True): raise HTTPException(403, "keyboard disabled")
    parts = [k.strip() for k in keys.split('+') if k.strip()]
    if not parts: raise HTTPException(400, "keys required")
    pyautogui.hotkey(*parts)
    return {"status":"ok","keys":parts}

# Window: activate by title contains
@app.get("/window/activate")
def win_activate(request: Request, title: str = Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('window', True): raise HTTPException(403, "window feature disabled")
    try:
        d = Desktop(backend="uia")
        for w in d.windows():
            try:
                t = w.window_text()
                if title.lower() in (t or '').lower():
                    w.set_focus()
                    return {"status":"ok","window":t}
            except Exception:
                continue
        raise HTTPException(404, "window not found")
    except Exception as e:
        raise HTTPException(500, str(e))

# Screen: screenshot
@app.get("/screen/screenshot")
def screenshot():
    if not FEAT.get('screenshot', True): raise HTTPException(403, "screenshot disabled")
    with mss.mss() as sct:
        path = SHOTS / "shot.png"
        sct.shot(mon=-1, output=str(path))
    return {"status":"ok","path":str(path),"url":f"/shots/{path.name}"}

@app.get("/shots/{name}")
def serve_shot(name: str):
    return FileResponse(SHOTS / name)

# Run allowlisted apps
@app.get("/app/run")
def app_run(request: Request, name: str = Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('run_apps', True): raise HTTPException(403, "run apps disabled")
    if name not in ALLOW:
        raise HTTPException(403, f"name '{name}' not in allowlist")
    cmd = ALLOW[name]
    exe = cmd[0]
    if not shutil.which(exe):
        raise HTTPException(404, f"executable not found: {exe}")
    try:
        subprocess.Popen(cmd)
        return {"status":"ok","launched":cmd}
    except Exception as e:
        raise HTTPException(500, str(e))

# Browser: open URL (simple)
@app.get("/browser/open")
def browser_open(request: Request, url: str = Query(...)):
    auth(request); require_enabled()
    if not FEAT.get('browser_open', True): raise HTTPException(403, "browser_open disabled")
    import webbrowser
    if not (url.startswith('http://') or url.startswith('https://')):
        url = 'https://' + url
    webbrowser.open_new_tab(url)
    return {"status":"ok","opened":url}

# Browser: Playwright script (advanced, optional)
@app.post("/browser/script")
def browser_script(request: Request, body: ScriptBody):
    auth(request); require_enabled()
    if not PW_ENABLED: raise HTTPException(403, "Playwright not enabled. Set features.browser_playwright = true and install it.")
    results = []
    for a in body.steps:
        t = a.type
        if t == 'goto':
            if not a.url: raise HTTPException(400, "url required")
            _page.goto(a.url)
            results.append({"ok":True}); continue
        if t == 'click':
            if not a.selector: raise HTTPException(400, "selector required")
            _page.click(a.selector, timeout=a.timeout_ms)
            results.append({"ok":True}); continue
        if t == 'fill':
            if not a.selector: raise HTTPException(400, "selector required")
            _page.fill(a.selector, a.text or "", timeout=a.timeout_ms)
            results.append({"ok":True}); continue
        if t == 'type':
            _page.keyboard.type(a.text or "")
            results.append({"ok":True}); continue
        if t == 'press':
            if not a.key: raise HTTPException(400, "key required")
            _page.keyboard.press(a.key)
            results.append({"ok":True}); continue
        if t == 'wait':
            if not a.selector: raise HTTPException(400, "selector required")
            _page.wait_for_selector(a.selector, timeout=a.timeout_ms)
            results.append({"ok":True}); continue
        if t == 'eval':
            expr = a.expression or "document.title"
            res = _page.evaluate(f"(function(){{ try{{ return {expr}; }}catch(e){{ return String(e); }} }})()" )
            results.append({"ok":True,"result":res}); continue
        if t == 'screenshot':
            out = ROOT/'shots'/'playwright.png'
            _page.screenshot(path=str(out), full_page=True)
            results.append({"ok":True,"path":str(out)}); continue
        raise HTTPException(400, f"Unknown action: {t}")
    return {"ok":True,"results":results}

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
