"""Datasheet RAG: download a part's datasheet PDF, convert it to text, chunk it into Chroma,
and hand the model the passages that matter (supply range, dropout, output capacitor...).

Only the few candidate parts a module designer is shown are fetched, never the whole library.
Datasheet text is UNTRUSTED third-party content: it is passed to the model labelled as data.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .memory import DesignMemory
from .models import PartDef

DEFAULT_QUERY = ("input voltage range, output voltage, dropout voltage, absolute maximum ratings, "
                 "recommended operating conditions, output capacitor requirements, supply current")
MAX_PAGES = 12  # specs live at the front of a datasheet


def chunk_text(text: str, size: int = 900, max_chunks: int = 80) -> list[str]:
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    chunks, cur = [], ""
    for para in paras:
        if not para:
            continue
        if cur and len(cur) + len(para) > size:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{para}".strip()
        while len(cur) > size * 1.5:  # very long paragraph (tables)
            chunks.append(cur[:size])
            cur = cur[size:]
    if cur:
        chunks.append(cur)
    return chunks[:max_chunks]


class DatasheetStore:
    def __init__(self, memory: DesignMemory, cache_dir: str | Path, manual_dir: str | Path | None = None):
        self.memory = memory
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.manual = Path(manual_dir) if manual_dir else None
        if self.manual:
            self.manual.mkdir(parents=True, exist_ok=True)

    def manual_path(self, part_id: str) -> Path | None:
        """Where a human can drop a datasheet PDF for a part whose vendor blocks scripted downloads."""
        return self.manual / (part_id.replace(":", "__") + ".pdf") if self.manual else None

    def has(self, part_id: str) -> bool:
        return bool(self.memory.datasheets.get(where={"part_id": part_id}, limit=1)["ids"])

    def add_text(self, part_id: str, text: str) -> int:
        chunks = chunk_text(text)
        if chunks:
            self.memory.datasheets.upsert(
                ids=[f"{part_id}#{i}" for i in range(len(chunks))], documents=chunks,
                embeddings=self.memory.embedder.embed(chunks),
                metadatas=[{"part_id": part_id, "chunk": i} for i in range(len(chunks))])
        return len(chunks)

    def _download(self, url: str) -> Path | None:
        if urlparse(url).scheme not in ("http", "https"):
            return None
        dest = self.cache / (hashlib.sha1(url.encode()).hexdigest() + ".pdf")
        if not dest.exists():
            try:
                r = httpx.get(url, follow_redirects=True, timeout=40,
                              headers={"User-Agent": "Mozilla/5.0 (pcbcreator datasheet fetch)"})
            except httpx.HTTPError:
                return None
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                return None
            dest.write_bytes(r.content)
        return dest

    def ensure(self, part: PartDef) -> bool:
        """Make sure the part's datasheet is in the store. Returns False if it cannot be obtained."""
        if self.has(part.id):
            return True
        manual = self.manual_path(part.id)
        if manual is not None and manual.exists():
            pdf = manual
        elif not part.datasheet or part.datasheet == "~":
            return False
        else:
            pdf = self._download(part.datasheet)
        if pdf is None:
            return False
        out = subprocess.run(["pdftotext", "-layout", "-l", str(MAX_PAGES), str(pdf), "-"],
                             capture_output=True, text=True, timeout=60, check=False)
        return self.add_text(part.id, out.stdout) > 0

    def excerpts(self, part_id: str, query: str = DEFAULT_QUERY, n: int = 3, max_chars: int = 1500) -> str:
        res = self.memory.datasheets.query(query_embeddings=self.memory.embedder.embed([query]),
                                           n_results=n, where={"part_id": part_id})
        out, used = [], 0
        for doc in res["documents"][0]:
            doc = doc[: max_chars - used]
            out.append(doc)
            used += len(doc)
            if used >= max_chars:
                break
        return "\n---\n".join(out)
