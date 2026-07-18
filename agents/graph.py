"""The LangGraph pipeline: parse -> extract -> Gate 1 (retry loop) ->
RAG validate -> Gate 2 -> store or human review.

Gate math lives in utils/gates.py (pure, unit-testable in isolation).
LLM calls live in services/llm.py. This file only wires them together
into the state machine and owns the routing decisions."""

import pdfplumber
from langgraph.graph import END, StateGraph

import services.llm as llm
import services.storage as storage
import services.vectorstore as vectorstore
from models.schema import GraphState, Item, ItemValidationResult, OrderStatus, PurchaseOrder, Supplier
from utils.config import settings
from utils.csv_parser import try_deterministic_csv_parse
from utils.gates import compute_gate1_fraction, compute_gate2_rate


def parse_file(state: GraphState) -> dict:
    if state.file_type == "csv":
        order = try_deterministic_csv_parse(state.file_path)
        if order is not None:
            return {"extracted_order": order, "skip_llm_extraction": True}
        with open(state.file_path) as f:
            return {"raw_text": f.read()}

    if state.file_type == "pdf":
        with pdfplumber.open(state.file_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return {"raw_text": text}

    if state.file_type == "image":
        return {}  # extract_to_json calls extract_from_image directly with file_path

    raise ValueError(f"Unsupported file_type: {state.file_type}")


def extract_to_json(state: GraphState) -> dict:
    if state.skip_llm_extraction:
        return {}

    if state.file_type == "image":
        order, error = llm.extract_from_image(state.file_path)
    else:
        order, error = llm.extract_from_text(state.raw_text or "")

    if order is None:
        # Extraction failed to produce anything usable at all — treat as a
        # fully empty order so Gate 1 scores it 0.0 and routes accordingly,
        # instead of crashing the whole graph run.
        order = PurchaseOrder(supplier=Supplier(name=None), items=[])

    return {"extracted_order": order}


def check_gate1(state: GraphState) -> dict:
    return {"gate1_fraction": compute_gate1_fraction(state.extracted_order)}


def increment_retry(state: GraphState) -> dict:
    return {"retry_count": state.retry_count + 1}


def gate1_router(state: GraphState) -> str:
    if state.gate1_fraction >= settings.gate1_completeness_threshold:
        return "continue"
    if state.retry_count < settings.gate1_max_retries:
        return "retry"
    return "review"


def rag_validate(state: GraphState) -> dict:
    order = state.extracted_order
    supplier_results = vectorstore.query_suppliers(order.supplier.name, n_results=1) if order.supplier.name else []
    supplier_doc = supplier_results[0]["document"] if supplier_results else None

    validations = []
    for item in order.items:
        query_text = item.product_code or item.description or ""
        catalog_results = vectorstore.query_catalog(query_text, n_results=1) if query_text else []
        catalog_doc = catalog_results[0]["document"] if catalog_results else None

        is_valid, reason = llm.reason_about_item_validity(
            product_code=item.product_code or "unknown",
            quantity=item.quantity,
            unit_price=item.unit_price,
            supplier_name=order.supplier.name,
            catalog_doc=catalog_doc,
            supplier_doc=supplier_doc,
        )
        validations.append(
            ItemValidationResult(
                product_code=item.product_code or "unknown",
                is_valid=is_valid,
                reason=reason,
                matched_document_id=catalog_results[0]["id"] if catalog_results else None,
            )
        )

    return {"item_validations": validations}


def check_gate2(state: GraphState) -> dict:
    return {"gate2_validation_rate": compute_gate2_rate(state.item_validations)}


def gate2_router(state: GraphState) -> str:
    if state.gate2_validation_rate >= settings.gate2_validation_rate_threshold:
        return "store"
    return "review"


def store_order(state: GraphState) -> dict:
    storage.save_approved_order(state.extracted_order, state.correlation_id)
    return {"final_status": OrderStatus.VALIDATED}


def human_review(state: GraphState) -> dict:
    if state.gate2_validation_rate is not None and state.gate2_validation_rate < settings.gate2_validation_rate_threshold:
        reason = (
            f"Gate 2 failed: {state.gate2_validation_rate:.0%} of items passed RAG validation "
            f"(threshold {settings.gate2_validation_rate_threshold:.0%})"
        )
    else:
        reason = (
            f"Gate 1 failed: {state.gate1_fraction:.0%} of required fields present after "
            f"{state.retry_count} retry attempt(s) (threshold {settings.gate1_completeness_threshold:.0%})"
        )
    storage.save_pending_review(state.extracted_order, reason, state.correlation_id)
    return {"final_status": OrderStatus.NEEDS_REVIEW, "review_reason": reason}


def build_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("parse_file", parse_file)
    workflow.add_node("extract_to_json", extract_to_json)
    workflow.add_node("check_gate1", check_gate1)
    workflow.add_node("increment_retry", increment_retry)
    workflow.add_node("rag_validate", rag_validate)
    workflow.add_node("check_gate2", check_gate2)
    workflow.add_node("store_order", store_order)
    workflow.add_node("human_review", human_review)

    workflow.set_entry_point("parse_file")
    workflow.add_edge("parse_file", "extract_to_json")
    workflow.add_edge("extract_to_json", "check_gate1")
    workflow.add_conditional_edges(
        "check_gate1",
        gate1_router,
        {"retry": "increment_retry", "continue": "rag_validate", "review": "human_review"},
    )
    workflow.add_edge("increment_retry", "extract_to_json")
    workflow.add_edge("rag_validate", "check_gate2")
    workflow.add_conditional_edges(
        "check_gate2",
        gate2_router,
        {"store": "store_order", "review": "human_review"},
    )
    workflow.add_edge("store_order", END)
    workflow.add_edge("human_review", END)

    return workflow.compile()