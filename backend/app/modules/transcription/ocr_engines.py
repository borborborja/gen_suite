"""Vision-OCR engines for manuscript transcription.

Absorbed verbatim from the (own) ``libros2pdf`` helper so the suite is self-contained — no vendored
editable package to install. Only what the transcription worker actually used is kept: the default
prompt and the two vision backends (Anthropic + OpenAI-compatible: OpenAI / OpenRouter / Ollama /
Google). Both lazily import their SDK so the import is cheap and optional.
"""
from __future__ import annotations

import base64
import os

DEFAULT_VISION_PROMPT = """\
Transcribe exactamente el texto visible en este documento histórico manuscrito.

- Copia el texto tal como aparece: respeta ortografía original, tildes y abreviaturas (Dn., Dña., Nro., fol., idem…)
- Si hay texto en latín, transcríbelo literalmente sin traducir
- Conserva la estructura: saltos de línea, párrafos y numeración de actas
- Marca palabras ilegibles como [ilegible] y palabras dudosas como [palabra?]
- Devuelve ÚNICAMENTE el texto transcrito, sin comentarios ni explicaciones\
"""


def _ocr_via_anthropic(img_data: bytes, *,
                       model: str = "claude-haiku-4-5-20251001",
                       api_key: str | None = None,
                       prompt: str | None = None) -> tuple[str, dict]:
    """Returns (text, usage) — usage is {prompt, completion} token counts for the spending control."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(img_data).decode()}},
                {"type": "text", "text": prompt or DEFAULT_VISION_PROMPT},
            ],
        }],
    )
    u = getattr(response, "usage", None)
    usage = {"prompt": getattr(u, "input_tokens", 0) or 0,
             "completion": getattr(u, "output_tokens", 0) or 0}
    return response.content[0].text, usage


def _ocr_via_openai_compat(img_data: bytes, *,
                           model: str,
                           api_key: str | None = None,
                           base_url: str | None = None,
                           prompt: str | None = None) -> tuple[str, dict]:
    """Returns (text, usage) — usage is {prompt, completion} token counts for the spending control."""
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", "none"),
        base_url=base_url,
    )
    img_b64 = base64.b64encode(img_data).decode()
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}",
                               "detail": "high"}},
                {"type": "text", "text": prompt or DEFAULT_VISION_PROMPT},
            ],
        }],
    )
    u = getattr(response, "usage", None)
    usage = {"prompt": getattr(u, "prompt_tokens", 0) or 0,
             "completion": getattr(u, "completion_tokens", 0) or 0}
    return response.choices[0].message.content, usage
