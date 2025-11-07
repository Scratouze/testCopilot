# agent_llm.py
import os, json, httpx

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")  # rapide et bon pour tool-calling
DEFAULT_BASE_URL = os.getenv("COPILOTPC_BASE_URL", "http://127.0.0.1:8730")
DEFAULT_TOKEN = os.getenv("COPILOTPC_TOKEN", "")

class LLMPlanner:
    """
    Transforme une demande NL -> suite d'appels d'outils CopilotPC.
    Utilise OpenAI Responses API + Tool Calling.
    """
    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str = DEFAULT_TOKEN):
        self.base = base_url.rstrip("/")
        self.token = token

    # ---- Outils que l'IA peut appeler ----
    def tool_schema(self):
        return [
            {
                "type":"function",
                "function":{
                    "name":"run_app",
                    "description":"Lancer une application depuis l'allowlist (config.toml [run.allowlist]).",
                    "parameters":{
                        "type":"object",
                        "properties":{"name":{"type":"string"}},
                        "required":["name"]
                    }
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
                    "description":"Taper du texte au clavier dans la fenêtre active.",
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
        ]

    # ---- Exécution côté CopilotPC ----
    async def _call_local(self, path, params=None):
        params = params or {}
        if self.token:
            params["token"] = self.token
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(f"{self.base}{path}", params=params)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _needs_clipboard(text: str) -> bool:
        multiline = ("\n" in text) or ("\r" in text)
        too_long = len(text) > 300
        has_non_ascii = any(ord(c) > 127 for c in text)
        return multiline or too_long or has_non_ascii

    async def tool_dispatch(self, name, args):
        if name == "run_app":
            return await self._call_local("/app/run", {"name": args["name"]})
        if name == "focus_window":
            return await self._call_local("/window/activate", {"title": args["title"]})
        if name == "open_url":
            return await self._call_local("/browser/open", {"url": args["url"]})
        if name == "type_text":
            txt = args.get("text")
            if not isinstance(txt, str) or not txt.strip():
                return {"ok": False, "status": 400, "error": "missing_text_argument"}
            if self._needs_clipboard(txt):
                return await self.tool_dispatch("paste_text", {"text": txt})
            return await self._call_local("/os/keyboard/type", {"text": txt})
        if name == "paste_text":
            txt = args.get("text")
            if not isinstance(txt, str) or not txt.strip():
                return {"ok": False, "status": 400, "error": "missing_text_argument"}
            clip = await self._call_local("/os/clipboard/set", {"text": txt})
            paste = await self._call_local("/os/keyboard/paste")
            return {"ok": True, "clipboard": clip, "paste": paste}
        if name == "hotkey":
            return await self._call_local("/os/keyboard/hotkey", {"keys": args["keys"]})
        if name == "screenshot":
            return await self._call_local("/screen/screenshot")
        return {"error": f"unknown tool {name}"}

    # ---- Boucle tool-calling ----
    async def run(self, user_text:str):
        """
        1) Envoie la demande à OpenAI avec la liste des tools
        2) Si le modèle veut appeler des tools, on exécute localement
        3) On renvoie la sortie des tools au modèle, on boucle jusqu'à ce qu'il réponde "final"
        """
        if not OPENAI_API_KEY:
            return {"ok":False, "error":"OPENAI_API_KEY missing"}

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        messages = [
            {"role":"system","content":
             "Tu es un planificateur d'actions pour un PC local. "
             "Si nécessaire, appelle les fonctions pour exécuter des actions (ouvrir appli/URL, taper, hotkeys, focus, screenshot). "
             "Toujours respecter les étapes et l'ordre. Ne propose pas de code, exécute via tools."},
            {"role":"user","content": user_text}
        ]

        while True:
            payload = {
                "model": MODEL,
                "messages": messages,
                "tools": self.tool_schema(),
                "tool_choice": "auto",
                "temperature": 0.2,
            }
            # Responses API
            async with httpx.AsyncClient(timeout=120) as cli:
                resp = await cli.post(f"{OPENAI_BASE}/responses", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

            # La forme exacte dépend des mises à jour du SDK; on lit "output" / "choices"
            # Structure courante: data['output'] -> list d'items; sinon data['choices'][0]['message']
            tool_calls = []
            final_text = None

            # Essayons d'être robustes:
            output = data.get("output") or data.get("choices")
            if isinstance(output, list):
                # Responses API "output" items
                for item in output:
                    if isinstance(item, dict) and item.get("type") == "message":
                        msg = item.get("message", {})
                        # tool calls ?
                        if "tool_calls" in msg:
                            tool_calls.extend(msg["tool_calls"])
                        # content text ?
                        parts = msg.get("content", [])
                        texts = [p.get("text","") for p in parts if p.get("type")=="text"]
                        if texts:
                            final_text = "\n".join(texts).strip() or final_text
            else:
                # fallback ChatCompletions-like
                choice = data.get("choices",[{}])[0]
                message = choice.get("message",{})
                if "tool_calls" in message:
                    tool_calls = message["tool_calls"]
                if message.get("content"):
                    final_text = message["content"]

            if tool_calls:
                # Exécuter chaque tool, puis renvoyer les résultats au modèle
                for tc in tool_calls:
                    fn = tc.get("function",{})
                    name = fn.get("name")
                    args_json = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(args_json) if isinstance(args_json, str) else args_json
                    except Exception:
                        args = {}
                    result = await self.tool_dispatch(name, args)
                    messages.append({"role":"tool","name":name,"content":json.dumps(result)})
                # Boucle: renvoyer la sortie des tools au modèle pour la suite
                continue

            # Pas de tool call → on s'arrête avec le texte final
            return {"ok":True, "final": final_text or "(ok)"}
