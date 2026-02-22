import json
import os
from urllib import error as urlerror
from urllib import request as urlrequest


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, str(default)) or "").strip()
    try:
        return int(raw)
    except Exception:
        return default


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_chat_model() -> str:
    return (os.getenv("OLLAMA_CHAT_MODEL", "llama3.1") or "llama3.1").strip()


def ollama_embed_model() -> str:
    return (os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text") or "nomic-embed-text").strip()


def ollama_list_models(base_url: str | None = None) -> list[str]:
    base = (base_url or ollama_base_url()).rstrip("/")
    req = urlrequest.Request(f"{base}/api/tags", method="GET")
    with urlrequest.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    out: list[str] = []
    for item in payload.get("models") or []:
        if isinstance(item, dict) and item.get("name"):
            out.append(str(item.get("name")))
    return out


def _extract_json(text: str):
    s = (text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        block = s[start:end + 1]
        try:
            return json.loads(block)
        except Exception:
            return None
    return None


def ollama_chat(
    messages: list[dict],
    *,
    preferred_model: str | None = None,
    num_predict: int | None = None,
    timeout_sec: int | None = None,
    temperature: float = 0.2,
) -> str:
    base = ollama_base_url()
    preferred = (preferred_model or ollama_chat_model()).strip() or "llama3.1"
    timeout = timeout_sec if timeout_sec is not None else _env_int("OLLAMA_CHAT_TIMEOUT_SEC", 240)
    predict = num_predict if num_predict is not None else _env_int("OLLAMA_CHAT_NUM_PREDICT", 450)

    installed_models: list[str] = []
    try:
        installed_models = ollama_list_models(base)
    except Exception:
        installed_models = []

    candidates: list[str] = []
    for model_name in [preferred, "llama3.1:8b", "llama3.1", "llama3.2", "qwen2.5", "mistral"]:
        if model_name and model_name not in candidates:
            candidates.append(model_name)
    for model_name in installed_models:
        if model_name not in candidates:
            candidates.append(model_name)

    last_error = None
    for model in candidates:
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": predict,
            },
        }
        req = urlrequest.Request(
            f"{base}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            msg = payload.get("message") or {}
            content = str(msg.get("content") or "").strip()
            if content:
                return content
            last_error = f"empty response model={model}"
        except urlerror.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8")[:500]
            except Exception:
                body_text = ""
            last_error = f"HTTP {e.code} model={model} body={body_text}"
        except Exception as e:
            last_error = f"model={model} error={e}"

    available = ", ".join(installed_models[:10]) if installed_models else "none"
    raise RuntimeError(f"Ollama chat failed. last_error={last_error}; installed_models={available}")


def ollama_chat_json(
    messages: list[dict],
    *,
    preferred_model: str | None = None,
    num_predict: int | None = None,
    timeout_sec: int | None = None,
) -> dict:
    text = ollama_chat(
        messages,
        preferred_model=preferred_model,
        num_predict=num_predict,
        timeout_sec=timeout_sec,
        temperature=0.1,
    )
    parsed = _extract_json(text)
    if isinstance(parsed, dict):
        return parsed
    raise RuntimeError("LLM did not return valid JSON payload")
