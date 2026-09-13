"""Vision-OCR engines for manuscript transcription.

Absorbed verbatim from the (own) ``libros2pdf`` helper so the suite is self-contained — no vendored
editable package to install. Only what the transcription worker actually used is kept: the default
prompt and the two vision backends (Anthropic + OpenAI-compatible: OpenAI / OpenRouter / Ollama /
Google). Both lazily import their SDK so the import is cheap and optional.
"""
from __future__ import annotations

import base64

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
                       prompt: str | None = None, max_output_tokens: int = 4096,
                       base_url: str | None = None) -> tuple[str, dict]:
    """Returns (text, usage) — usage is {prompt, completion} token counts for the spending control."""
    import anthropic
    from ..providers.admission import ReceivedResponseError, require_admission
    require_admission("claude")
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=0)
    response = client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
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
    usage = {"prompt": getattr(u, "input_tokens", None),
             "completion": getattr(u, "output_tokens", None), "request_id": getattr(response, "_request_id", None)}
    if usage["prompt"] is not None:
        usage["prompt"] += (getattr(u, "cache_creation_input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
    content = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    if response.stop_reason != "end_turn" or not content:
        raise ReceivedResponseError("incomplete_or_refused", content, usage)
    return content, usage


def _ocr_via_openai_compat(img_data: bytes, *,
                           model: str,
                           api_key: str | None = None,
                           base_url: str | None = None,
                           prompt: str | None = None, engine: str = "openai",
                           output_options: dict | None = None) -> tuple[str, dict]:
    """Returns (text, usage) — usage is {prompt, completion} token counts for the spending control."""
    from openai import OpenAI
    from ..providers.admission import ReceivedResponseError, require_admission
    require_admission(engine)
    client = OpenAI(
        api_key=api_key or "none", base_url=base_url, timeout=90.0, max_retries=0,
    )
    img_b64 = base64.b64encode(img_data).decode()
    response = client.chat.completions.create(
        model=model,
        n=1, **(output_options or {"max_completion_tokens": 4096}),
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
    usage = {"prompt": getattr(u, "prompt_tokens", None),
             "completion": getattr(u, "completion_tokens", None), "request_id": getattr(response, "_request_id", None)}
    content = response.choices[0].message.content if response.choices else None
    if not content or response.choices[0].finish_reason != "stop":
        raise ReceivedResponseError("incomplete_or_refused", content, usage)
    return content, usage
