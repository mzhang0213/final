import itertools
import json
import os

import numpy as np
import cv2 as cv

from google import genai
from google.genai import types

from resources.prompts.schemas import SCHEMAS

from dotenv import load_dotenv

load_dotenv()

#test: python resources/gemini.py ss1.png


# ── Config ────────────────────────────────────────────────────────────────────

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")

MODEL = "gemini-2.5-flash"


def _load_system_prompt() -> str:
    with open(os.path.join(_PROMPTS_DIR, "context.txt")) as f:
        return f.read().strip()


# ── Transaction Extractor ────────────────────────────────────────────────────

class TransactionExtractor:
    def __init__(self, api_keys: list[str] | str, model: str = MODEL):
        if isinstance(api_keys, str):
            api_keys = [api_keys]

        self._keys = itertools.cycle(api_keys)
        self._model_name = model
        self._system = _load_system_prompt()
        self._init_client()

    def _init_client(self):
        """Configure client with the next key."""
        self._client = genai.Client(api_key=next(self._keys))

    def _generate(self, prompt: str, image_part: types.Part) -> dict:
        """Send prompt + image to Gemini and return parsed JSON."""
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                system_instruction=self._system,
                response_mime_type="application/json",
                max_output_tokens=4096,
                temperature=0.1,
            ),
        )
        return json.loads(response.text)

    def _build_prompt(self) -> str:
        schema = SCHEMAS["extract"]
        return (
            f"Extract all visible transactions from this banking app screenshot. "
            f"Respond with JSON matching this schema: {json.dumps(schema, separators=(',', ':'))} "
            f"Set any undeterminable fields to null."
        )

    def extract(self, image_path: str) -> dict:
        """Extract transactions from a banking app screenshot file.

        Args:
            image_path: path to the screenshot image file

        Returns:
            dict with "transactions" list matching the extract schema
        """
        with open(image_path, "rb") as f:
            image_data = f.read()

        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        image_part = types.Part.from_bytes(data=image_data, mime_type=mime)
        prompt = self._build_prompt()

        try:
            return self._generate(prompt, image_part)
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                self._init_client()
                try:
                    return self._generate(prompt, image_part)
                except Exception as e2:
                    return {"transactions": [], "error": str(e2)}
            return {"transactions": [], "error": err}

    def extract_from_frame(self, frame: np.ndarray) -> dict:
        """Extract transactions from a numpy image array (e.g. from screen capture).

        Args:
            frame: BGR numpy array (as returned by OpenCV / mss)

        Returns:
            dict with "transactions" list matching the extract schema
        """
        _, buf = cv.imencode(".png", frame)
        image_part = types.Part.from_bytes(data=buf.tobytes(), mime_type="image/png")
        prompt = self._build_prompt()

        try:
            return self._generate(prompt, image_part)
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                self._init_client()
                try:
                    return self._generate(prompt, image_part)
                except Exception as e2:
                    return {"transactions": [], "error": str(e2)}
            return {"transactions": [], "error": err}


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        image = sys.argv[1]
    else:
        print("Usage: python gemini.py <screenshot.png>")
        sys.exit(1)

    extractor = TransactionExtractor(api_keys=[os.getenv("GEMINI_API_KEY")])
    result = extractor.extract(image)
    print(json.dumps(result, indent=2))
