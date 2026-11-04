"""The model the builder writes with, behind one small protocol (ADR-0039 D6, step 6).

A turn is the platform's own shape, `{"role": "user" | "assistant", "text": str, "documents":
[Document]}`; a provider turns it into its API's. The model answers with one JSON object (the
change, as `guide.md` describes it). Which model is configuration (`BUILDER_MODEL`); another
provider is another class here, not a change to the builder.
"""

from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.config import settings
from app.extraction.reader import Document, _part


class BuilderUnavailable(Exception):
    """No model is configured, or it did not answer usefully."""


class BuilderModel(Protocol):
    model: str

    async def answer(self, system: str, turns: list[dict]) -> dict: ...


class OpenAIBuilder:
    def __init__(self, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self.model = model
        self._transport = transport

    async def answer(self, system: str, turns: list[dict]) -> dict:
        messages = [{"role": "system", "content": [{"type": "input_text", "text": system}]}]
        for turn in turns:
            kind = "output_text" if turn["role"] == "assistant" else "input_text"
            parts = [{"type": kind, "text": turn["text"]}]
            for i, doc in enumerate(turn.get("documents") or [], 1):
                parts += [{"type": "input_text", "text": f"Document {i}: {doc.name}"}, _part(doc)]
            messages.append({"role": turn["role"], "content": parts})
        body = {"model": self.model, "input": messages, "text": {"format": {"type": "json_object"}}}
        try:
            async with httpx.AsyncClient(timeout=600, transport=self._transport) as client:
                res = await client.post(
                    "https://api.openai.com/v1/responses",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
        except httpx.HTTPError as exc:
            raise BuilderUnavailable(f"the model did not answer: {exc}") from exc
        if res.status_code != 200:
            raise BuilderUnavailable(f"the model refused: {res.status_code} {res.text[:300]}")
        for item in res.json().get("output", []):
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise BuilderUnavailable(f"the model declined: {part.get('refusal')}")
                if part.get("type") == "output_text":
                    try:
                        return json.loads(part["text"])
                    except json.JSONDecodeError as exc:
                        raise BuilderUnavailable("the model answered without valid JSON") from exc
        raise BuilderUnavailable("the model returned no answer")


def builder_model() -> BuilderModel:
    if not settings.openai_api_key:
        raise BuilderUnavailable("the builder needs OPENAI_API_KEY")
    return OpenAIBuilder(settings.openai_api_key, settings.builder_model)


__all__ = ["BuilderModel", "BuilderUnavailable", "Document", "OpenAIBuilder", "builder_model"]
