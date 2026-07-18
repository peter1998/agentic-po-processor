"""Claude-based extraction: text and vision, both returning a PurchaseOrder
or a graceful failure reason (never raises on a bad LLM response — that's
Gate 1's job to handle, not this layer's)."""

import base64
import json

import anthropic
from pydantic import ValidationError

from models.schema import PurchaseOrder
from utils.config import settings

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_MODEL = "claude-sonnet-4-6"  # verify current model string in docs.claude.com before relying on this in production

_EXTRACTION_INSTRUCTIONS = """You are extracting structured data from a purchase order document.

Return ONLY a JSON object matching this exact structure, nothing else — no markdown fences, no commentary:

{
  "po_number": "string or null",
  "supplier": {"name": "string", "id": "string or null"},
  "items": [
    {"product_code": "string", "description": "string or null", "quantity": number, "unit_price": number or null, "total_price": number or null}
  ],
  "total_amount": number or null,
  "currency": "string, default EUR",
  "delivery_date": "YYYY-MM-DD or null"
}

Rules:
- If a required value (supplier name, product code, quantity, unit price) is genuinely absent, missing, or stated as "TBD"/"pending"/unclear, use null for that field. Do NOT guess or invent a number.
- Extract exactly what is on the document. Do not infer values that aren't stated.
"""


def _parse_llm_json(raw_text: str) -> tuple[PurchaseOrder | None, str | None]:
    """Shared parsing step for both extraction paths. Returns (order, None)
    on success, or (None, error_reason) on failure — never raises."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return None, f"LLM response was not valid JSON: {e}"

    try:
        order = PurchaseOrder(**data)
    except ValidationError as e:
        return None, f"LLM response did not match the PurchaseOrder schema: {e}"

    return order, None


def extract_from_text(text: str) -> tuple[PurchaseOrder | None, str | None]:
    """Extract a PurchaseOrder from plain text (PDF text or non-matching CSV)."""
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"{_EXTRACTION_INSTRUCTIONS}\n\nDocument text:\n{text}",
            }
        ],
    )
    raw_text = response.content[0].text
    return _parse_llm_json(raw_text)


def extract_from_image(image_path: str, media_type: str = "image/png") -> tuple[PurchaseOrder | None, str | None]:
    """Extract a PurchaseOrder from an image (scanned document or photo)."""
    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": _EXTRACTION_INSTRUCTIONS},
                ],
            }
        ],
    )
    raw_text = response.content[0].text
    return _parse_llm_json(raw_text)