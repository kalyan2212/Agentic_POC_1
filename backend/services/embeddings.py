import json
import math
import os
from typing import Iterable, List
from urllib import request as urlrequest

EMBED_DIM = 384


def embed_text(text: str, dim: int = EMBED_DIM) -> List[float]:
    vec = [0.0] * dim
    payload_text = (text or "").strip()
    if not payload_text:
        return vec

    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    body = {
        "model": model,
        "prompt": payload_text[:12000],
    }
    req = urlrequest.Request(
        f"{base}/api/embeddings",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlrequest.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    emb = data.get("embedding") or []
    if not isinstance(emb, list) or not emb:
        return vec

    emb = [float(x) for x in emb]
    if len(emb) >= dim:
        emb = emb[:dim]
    else:
        emb.extend([0.0] * (dim - len(emb)))

    norm = math.sqrt(sum(v * v for v in emb)) or 1.0
    return [v / norm for v in emb]


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    av = list(a)
    bv = list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av)) or 1.0
    nb = math.sqrt(sum(y * y for y in bv)) or 1.0
    return dot / (na * nb)
