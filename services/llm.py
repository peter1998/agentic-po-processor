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

_MODEL = "claude-sonnet-5"  # verify current model string in docs.claude.com if this changes

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


def _extract_text(response) -> str:
    """Claude's response content can include a ThinkingBlock before the
    actual TextBlock (extended thinking). Find the text block explicitly
    instead of assuming content[0] is always text — a real API response
    surfaced this; no mock ever would have."""
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError(f"No text block found in response content: {response.content}")


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
    raw_text = _extract_text(response)
    return _parse_llm_json(raw_text)


_VALIDATION_INSTRUCTIONS = """You are checking whether an extracted purchase order item is valid, using retrieved reference data.

Extracted item: product_code={product_code}, quantity={quantity}, unit_price={unit_price}
Supplier on the order: {supplier_name}

Retrieved catalog entry (closest match by similarity, may or may not be correct): {catalog_doc}
Retrieved supplier entry (closest match by similarity, may or may not be correct): {supplier_doc}

Decide: does the extracted item actually match the retrieved reference data, or is there a discrepancy
(wrong supplier, price outside the approved range, product code that doesn't really match)?
A textually similar match is not automatically correct — check the actual numbers and names.

Return ONLY a JSON object: {{"is_valid": true or false, "reason": "one short sentence explaining why"}}
No markdown fences, no other text.
"""


def reason_about_item_validity(
    product_code: str,
    quantity: float | None,
    unit_price: float | None,
    supplier_name: str | None,
    catalog_doc: str | None,
    supplier_doc: str | None,
) -> tuple[bool, str]:
    """The Gate 2 reasoning step: retrieval alone isn't validation (see
    ADR / README) — this call decides whether the retrieved reference
    actually confirms the extracted data, not just whether it's textually
    close. Returns (is_valid, reason); defaults to invalid on any parse
    failure, since an unreadable verdict should never silently pass."""
    prompt = _VALIDATION_INSTRUCTIONS.format(
        product_code=product_code,
        quantity=quantity,
        unit_price=unit_price,
        supplier_name=supplier_name,
        catalog_doc=catalog_doc or "No match found in catalog.",
        supplier_doc=supplier_doc or "No match found in supplier list.",
    )
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = _extract_text(response)
    try:
        data = json.loads(raw_text)
        return bool(data["is_valid"]), str(data["reason"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, f"Could not parse validation response: {raw_text[:200]}"


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
    raw_text = _extract_text(response)
    return _parse_llm_json(raw_text)