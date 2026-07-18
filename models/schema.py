"""Pydantic models for the Agentic PO Processor — domain data (PurchaseOrder)
and the LangGraph pipeline state (GraphState)."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import ClassVar, Optional

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Supplier(BaseModel):
    name: str = Field(..., description="Required. Supplier name as it appears on the document.")
    id: Optional[str] = Field(None, description="Optional supplier ID, if present on the document.")


class Item(BaseModel):
    product_code: str = Field(..., description="Required. SKU or product code.")
    description: Optional[str] = Field(None)
    quantity: float = Field(..., description="Required.")
    unit_price: float = Field(..., description="Required.")
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

    # ClassVar (not a Pydantic field) so it doesn't leak into model_dump() output.
    REQUIRED_FIELD_PATHS: ClassVar[tuple[str, ...]] = (
        "supplier.name",
        "items[].product_code",
        "items[].quantity",
        "items[].unit_price",
    )


class ItemValidationResult(BaseModel):
    """Output of Gate 2's reasoning step, per item."""

    product_code: str
    is_valid: bool
    reason: str = Field(..., description="Short explanation from the LLM reasoning step.")
    matched_document_id: Optional[str] = Field(
        None, description="ID of the closest RAG document used for this verdict, for traceability."
    )


class GraphState(BaseModel):
    """Passed between LangGraph nodes. Carries the domain data plus pipeline
    metadata (retry count, gate results) that doesn't belong in the final
    stored record."""

    file_path: str
    file_type: str  # "pdf" | "image" | "csv"
    correlation_id: str

    raw_text: Optional[str] = Field(None, description="Set by parse_file for text-based inputs.")
    extracted_order: Optional[PurchaseOrder] = Field(None, description="Set by extract_to_json.")

    gate1_fraction: Optional[float] = Field(None)
    retry_count: int = Field(default=0)

    item_validations: list[ItemValidationResult] = Field(default_factory=list)
    gate2_validation_rate: Optional[float] = Field(None)

    final_status: Optional[OrderStatus] = Field(None)
    review_reason: Optional[str] = Field(
        None, description="Human-readable reason if routed to review — shown to the reviewer."
    )

    class Config:
        arbitrary_types_allowed = True