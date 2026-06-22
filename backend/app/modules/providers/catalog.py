"""Static catalog of AI engines (per-engine defaults: base_url, default model, capabilities).

Credentials reference a ``provider_key`` validated against this catalog. Capabilities gate which
task types an engine may serve (transcription→vision, embedding→embedding, inference→text).
"""
from __future__ import annotations

PROVIDER_CATALOG: dict[str, dict] = {
    "tesseract": {
        "display_name": "Tesseract (OCR local)",
        "capabilities": ["ocr_local"],
        "default_base_url": None,
        "default_model": None,
        "requires_key": False,
    },
    "kraken": {
        # Local handwritten-text recognition via the kraken-htr microservice (plan §6). Zero
        # cost/page and trainable per scribe; base_url points at the homelab stack.
        "display_name": "Kraken (HTR local)",
        "capabilities": ["ocr_local"],
        "default_base_url": "http://kraken-htr:8000",
        "default_model": None,
        "requires_key": False,
    },
    "ollama": {
        "display_name": "Ollama (local)",
        "capabilities": ["vision", "text", "embedding"],
        "default_base_url": "http://localhost:11434/v1",
        "default_model": "llava:13b",
        "requires_key": False,
    },
    "ollama_cloud": {
        # Ollama Cloud (hosted) via its OpenAI-compatible endpoint. Auth = Bearer API key from
        # ollama.com/settings/keys. Catalog of cloud models at ollama.com/search?c=cloud. NOTE: the
        # DeepSeek models are TEXT-ONLY (no image input) so they can't transcribe scans — for
        # transcription pick a vision model (gemma3:27b, gemma4:31b, gemini-3-flash-preview); those work
        # but the shared cloud can be slow (~150s/page). For text→records (inference) DeepSeek reads
        # well but doesn't honour our strict JSON schema reliably; gemini-2.5-pro stays the best.
        "display_name": "Ollama Cloud",
        "capabilities": ["vision", "text"],
        "default_base_url": "https://ollama.com/v1",
        "default_model": "gemma3:27b",
        "requires_key": True,
    },
    "claude": {
        "display_name": "Anthropic Claude",
        "capabilities": ["vision", "text"],
        "default_base_url": None,
        "default_model": "claude-haiku-4-5-20251001",
        "requires_key": True,
    },
    "openai": {
        "display_name": "OpenAI",
        "capabilities": ["vision", "text", "embedding"],
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "requires_key": True,
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "capabilities": ["vision", "text"],
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.5-flash-lite",
        "requires_key": True,
    },
    "google": {
        # Gemini via its OpenAI-compatible endpoint — direct billing/quota (no OpenRouter middleman),
        # higher rate limits and very cheap, so it's the recommended engine for bulk transcription +
        # extraction of whole books. Models: gemini-2.5-flash (fast/cheap), -pro (best HTR recall).
        "display_name": "Google Gemini",
        "capabilities": ["vision", "text"],
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "requires_key": True,
        "batch": True,  # supports the Batch API modality (async, ~50% cheaper)
    },
    "jina": {
        # Cloud embeddings (OpenAI-compatible). jina-embeddings-v3 is multilingual + 1024 dims.
        "display_name": "Jina AI (embeddings)",
        "capabilities": ["embedding"],
        "default_base_url": "https://api.jina.ai/v1",
        "default_model": "jina-embeddings-v3",
        "requires_key": True,
    },
}

# Which capability each task type needs.
TASK_CAPABILITY = {
    "transcription": "vision",
    "embedding": "embedding",
    "inference": "text",
}
