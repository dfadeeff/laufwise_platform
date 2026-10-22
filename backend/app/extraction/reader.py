"""The model that reads documents into a form, behind one small protocol (ADR-0033).

`DocumentReader.read` gets the strict schema, the workflow's reading guide and the documents
themselves (a PDF goes to the model as the file, not as our text of it), and returns the filled
form. It does nothing else: no computing, no deciding. Today one implementation, OpenAI's
Responses API over the `httpx` already in use; another provider is another class.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings

SYSTEM = (
    "You read documents and fill in a form, nothing else.\n"
    "- Copy every amount, share and date exactly as the documents print it, converted to the "
    "form's notation: 1.234,56 € becomes 1234.56, 68 % becomes 0.68, 01.08.2025 becomes "
    "2025-08-01.\n"
    "- Never calculate, estimate or complete a value. If the documents do not state it, use null, "
    "an empty string or an empty list.\n"
    "- For a choice, pick the option that matches what the document says; if none clearly does, "
    "use null.\n"
)


class ReaderUnavailable(Exception):
    """No model is configured, or it did not answer usefully. Nothing was changed."""


@dataclass(frozen=True)
class Document:
    name: str
    media_type: str
    data: bytes


class DocumentReader(Protocol):
    model: str

    async def read(self, schema: dict, guide: str, documents: list[Document]) -> dict: ...


def _part(doc: Document) -> dict:
    encoded = base64.b64encode(doc.data).decode()
    if doc.media_type == "application/pdf":
        return {
            "type": "input_file",
            "filename": doc.name,
            "file_data": f"data:application/pdf;base64,{encoded}",
        }
    if doc.media_type.startswith("image/"):
        return {"type": "input_image", "image_url": f"data:{doc.media_type};base64,{encoded}"}
    return {"type": "input_text", "text": doc.data.decode("utf-8", errors="replace")}


class OpenAIReader:
    def __init__(
        self, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = api_key
        self.model = model
        self._transport = transport

    async def read(self, schema: dict, guide: str, documents: list[Document]) -> dict:
        content: list[dict] = []
        for i, doc in enumerate(documents, 1):
            content.append({"type": "input_text", "text": f"Document {i}: {doc.name}"})
            content.append(_part(doc))
        content.append({"type": "input_text", "text": "Fill in the form from these documents."})
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": SYSTEM + guide}]},
                {"role": "user", "content": content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "case_input",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=240, transport=self._transport) as client:
                res = await client.post(
                    "https://api.openai.com/v1/responses",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
        except httpx.HTTPError as exc:
            raise ReaderUnavailable(f"the model did not answer: {exc}") from exc
        if res.status_code != 200:
            raise ReaderUnavailable(f"the model refused: {res.status_code} {res.text[:300]}")
        for item in res.json().get("output", []):
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise ReaderUnavailable(f"the model declined: {part.get('refusal')}")
                if part.get("type") == "output_text":
                    try:
                        return json.loads(part["text"])
                    except json.JSONDecodeError as exc:
                        raise ReaderUnavailable("the model answered without valid JSON") from exc
        raise ReaderUnavailable("the model returned no form")


def document_reader() -> DocumentReader:
    if not settings.openai_api_key:
        raise ReaderUnavailable("reading documents needs OPENAI_API_KEY")
    return OpenAIReader(settings.openai_api_key, settings.extraction_model)
