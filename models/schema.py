"""
Pydantic models for the Agentic PO Processor.

Two groups of models here:
1. Domain models (Item, Supplier, PurchaseOrder) — the actual extracted data,
   matching the JSON schema documented in README.md.
2. GraphState — the state object passed between LangGraph nodes. This carries
   the domain data PLUS pipeline metadata (retry count, gate results, status)
   that only exists during processing, not in the final stored record.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Domain models — the actual purchase order data
# ---------------------------------------------------------------------------


class OrderStatus(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Supplier(BaseModel):
    name: Optional[str] = Field(None, description="Required for Gate 1, but Optional here so partial extraction doesn't raise.")
    id: Optional[str] = Field(None, description="Optional supplier ID, if present on the document.")


class Item(BaseModel):
    product_code: Optional[str] = Field(None, description="Required for Gate 1, but Optional here so partial extraction doesn't raise.")
    description: Optional[str] = Field(None)
    quantity: Optional[float] = Field(None, description="Required for Gate 1, but Optional here so partial extraction doesn't raise.")
    unit_price: Optional[float] = Field(None, description="Required for Gate 1, but Optional here so partial extraction doesn't raise.")
    total_price: Optional[float] = Field(None, description="Optional — quantity * unit_price if not stated.")


class PurchaseOrder(BaseModel):
    po_number: Optional[str] = Field(None)
    supplier: Supplier
    items: list[Item] = Field(default_factory=list)
    total_amount: Optional[float] = Field(
        None, description="Optional, nice-to-have. Does NOT participate in Gate 1."
    )
    currency: str = Field(default="EUR")
    delivery_date: Optional[date] = Field(None)
    status: OrderStatus = Field(default=OrderStatus.PENDING_VALIDATION)

    # Fields required for Gate 1 completeness scoring.
    # ClassVar, not a Pydantic field — this is metadata about the schema
    # itself, not data on any given instance, so it must NOT be serialized
    # into stored records or LLM-facing JSON.
    REQUIRED_FIELD_PATHS: ClassVar[tuple[str, ...]] = (
        "supplier.name",
        "items[].product_code",
        "items[].quantity",
        "items[].unit_price",
    )


# ---------------------------------------------------------------------------
# Per-item RAG validation result — output of Gate 2's reasoning step
# ---------------------------------------------------------------------------


class ItemValidationResult(BaseModel):
    product_code: str
    is_valid: bool
    reason: str = Field(..., description="Short explanation from the LLM reasoning step.")
    matched_document_id: Optional[str] = Field(
        None, description="ID of the closest RAG document used for this verdict, for traceability."
    )


# ---------------------------------------------------------------------------
# GraphState — passed between LangGraph nodes
# ---------------------------------------------------------------------------


class GraphState(BaseModel):
    # --- input ---
    file_path: str
    file_type: str  # "pdf" | "image" | "csv"
    correlation_id: str

    # --- working data, filled in as the graph progresses ---
    raw_text: Optional[str] = Field(None, description="Set by parse_file for text-based inputs.")
    extracted_order: Optional[PurchaseOrder] = Field(None, description="Set by extract_to_json.")
    skip_llm_extraction: bool = Field(
        default=False,
        description="True only when parse_file's deterministic CSV path already produced a "
        "complete extracted_order — tells extract_to_json not to call the LLM at all, "
        "including on any retry loop.",
    )

    # --- Gate 1: extraction completeness ---
    gate1_fraction: Optional[float] = Field(None)
    retry_count: int = Field(default=0)

    # --- Gate 2: RAG validation ---
    item_validations: list[ItemValidationResult] = Field(default_factory=list)
    gate2_validation_rate: Optional[float] = Field(None)

    # --- final outcome ---
    final_status: Optional[OrderStatus] = Field(None)
    review_reason: Optional[str] = Field(
        None, description="Human-readable reason if routed to review — shown to the reviewer."
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)