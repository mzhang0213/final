import itertools
import json
import os
import urllib3

from google import genai

from server.resources.riot import (
    get_active_player,
    get_player_scores,
    get_game_stats,
)
from server.resources.prompts.schemas import SCHEMAS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "coach_memory.json")

# Model options — swap to change quality / cost:
#   gemini-2.0-flash        fast, cheap, good for live polling  ← default
#   gemini-2.0-flash-lite   cheapest, weaker reasoning
#   gemini-1.5-pro          strong reasoning, slower, higher cost
#   gemini-2.5-pro-preview  best available, use for post_game only
MODEL = "gemini-2.0-flash"


def _load_system_prompt() -> str:
    with open(os.path.join(_PROMPTS_DIR, "context.txt")) as f:
        return f.read().strip()


# ── State compression ─────────────────────────────────────────────────────────

def compress_game_state() -> dict:
    """
    Pull live data from the Riot API and strip it to coaching-relevant fields.
    Keeps per-turn token cost to ~150 bytes vs ~4 KB raw.
    """
    active  = get_active_player()
    game    = get_game_stats()
    riot_id = active.get("riotId", "")
    scores  = get_player_scores(riot_id) if riot_id else {}
    cs      = active["championStats"]

    return {
        "t":    round(game.get("gameTime", 0)),
        "mode": game.get("gameMode", ""),
        "hp":   round(cs.get("currentHealth", 0)),
        "mhp":  round(cs.get("maxHealth", 1)),
        "mp":   round(cs.get("resourceValue", 0)),
        "mmp":  round(cs.get("resourceMax", 1)),
        "g":    round(active.get("currentGold", 0)),
        "lvl":  active.get("level", 1),
        "k":    scores.get("kills", 0),
        "d":    scores.get("deaths", 0),
        "a":    scores.get("assists", 0),
        "cs":   scores.get("creepScore", 0),
        "ad":   round(cs.get("attackDamage", 0)),
        "ap":   round(cs.get("abilityPower", 0)),
    }


# ── Session memory ────────────────────────────────────────────────────────────

def load_memory() -> tuple[list[dict], dict]:
    """
    Returns (prior_context_messages, last_game_summary).
    Injects last-game facts as a single 2-message exchange rather than
    replaying full history — keeps session startup token cost near zero.
    """
    if not os.path.exists(_MEMORY_FILE):
        return [], {}

    with open(_MEMORY_FILE) as f:
        data = json.load(f)

    last_game = data.get("last_game", {})
    if not last_game:
        return [], {}

    prior_context = [
        {"role": "user",  "parts": [f"Summary of my last game: {json.dumps(last_game)}"]},
        {"role": "model", "parts": ["Got it. I'll keep that in mind for this session."]},
    ]
    return prior_context, last_game


def save_memory(game_summary: dict):
    """Persist a compact game summary for the next session."""
    with open(_MEMORY_FILE, "w") as f:
        json.dump({"last_game": game_summary}, f, indent=2)


# ── Coach ─────────────────────────────────────────────────────────────────────

class LoLCoach:
    def __init__(self, api_keys: list[str] | str, model: str = MODEL):
        if isinstance(api_keys, str):
            api_keys = [api_keys]

        self._keys       = itertools.cycle(api_keys)
        self._model_name = model
        self._system     = _load_system_prompt()

        prior_context, self.last_game = load_memory()
        self._prior_context = prior_context
        self._init_client()

    def _init_client(self):
        """Configure SDK with the next key and open a fresh chat session."""
        genai.configure(api_key=next(self._keys))
        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=self._system,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=300,
                temperature=0.4,
            ),
        )
        self._chat = model.start_chat(history=self._prior_context)

    def _call(self, question: str | None, schema_key: str) -> dict:
        """Build prompt, send to Gemini, parse and return JSON."""
        schema = SCHEMAS[schema_key]
        state  = compress_game_state()

        prompt = (
            f"schema:{json.dumps(schema, separators=(',',':'))} "
            f"state:{json.dumps(state, separators=(',',':'))}"
        )
        if question:
            prompt += f" question:{question}"

        response = self._chat.send_message(prompt)
        return json.loads(response.text)

    def get_advice(self, question: str = None, schema_key: str = "live") -> dict:
        """
        Fetch live state and return coaching JSON.

        Args:
            question:   optional free-text player question → uses "question" schema
            schema_key: "live" | "question" | "event" | "post_game"
        """
        if question and schema_key == "live":
            schema_key = "question"

        try:
            return self._call(question, schema_key)
        except Exception as e:
            err = str(e)
            # Rotate to next key on quota / rate-limit errors
            if "429" in err or "quota" in err.lower():
                self._init_client()
                try:
                    return self._call(question, schema_key)
                except Exception as e2:
                    err = str(e2)
            # Game client not reachable
            if "connect" in err.lower() or "api unavailable" in err.lower():
                return {"priority": "API unavailable",
                        "action": "Game client not running?",
                        "warning": err}
            return {"priority": "error", "action": err, "warning": None}

    def end_session(self, result: str, champion: str, kda: str, cs: int):
        """
        Call at game end. Saves a summary for the next session and
        optionally returns post-game advice.

        Args:
            result:   "win" or "loss"
            champion: champion name
            kda:      e.g. "8/2/10"
            cs:       total creep score
        """
        summary = {"result": result, "champion": champion, "kda": kda, "cs": cs}
        save_memory(summary)
        return self.get_advice(schema_key="post_game")


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Collect all env vars starting with GEMINI_API_KEY (e.g. GEMINI_API_KEY_1, _2 ...)
    keys = [v for k, v in os.environ.items() if k.startswith("GEMINI_API_KEY")]
    if not keys and len(sys.argv) > 1:
        keys = sys.argv[1:]
    if not keys:
        print("Usage: GEMINI_API_KEY_1=... GEMINI_API_KEY_2=... python gemini.py")
        sys.exit(1)

    coach = LoLCoach(api_keys=keys)

    print("=== live advice ===")
    print(json.dumps(coach.get_advice(), indent=2))

    print("\n=== question ===")
    print(json.dumps(coach.get_advice("should I take dragon or push mid?"), indent=2))
