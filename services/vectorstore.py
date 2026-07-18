"""RAG corpus loading and retrieval. Three separate ChromaDB collections
(suppliers, catalog, policies) so a query against one type of knowledge
doesn't surface irrelevant results from another.

Raw JSON records are converted to natural-language description text before
embedding — vector similarity works on meaning, and a flat key-value dump
doesn't carry meaning the way a sentence does."""

import json

import chromadb
import voyageai

from utils.config import settings

_EMBED_MODEL = "voyage-4-lite"  # current (Jan 2026) generation; older voyage-3.x no longer gets free-tier tokens

_chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_path)
_voyage_client = voyageai.Client(api_key=settings.voyage_api_key)


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    """input_type is 'document' when indexing, 'query' when searching —
    Voyage embeds these differently, so mixing them up quietly degrades
    retrieval quality without raising any error."""
    result = _voyage_client.embed(texts=texts, model=_EMBED_MODEL, input_type=input_type)
    return result.embeddings


def _supplier_to_text(s: dict) -> str:
    return f"Approved supplier: {s['name']} (ID: {s['id']}), category: {s['category']}."


def _catalog_item_to_text(item: dict, supplier_name: str) -> str:
    return (
        f"Product {item['product_code']}: {item['description']}. "
        f"Supplied by {supplier_name}. "
        f"Approved unit price range: {item['unit_price_min']}-{item['unit_price_max']} {item['currency']}."
    )


def _policy_to_text(p: dict) -> str:
    return p["rule"]


def build_corpus(corpus_dir: str = "data/rag_corpus") -> None:
    """Load the JSON corpus files and (re)index them into ChromaDB.
    Safe to call more than once — deterministic IDs mean re-indexing
    overwrites existing entries instead of duplicating them."""

    with open(f"{corpus_dir}/suppliers.json") as f:
        suppliers = json.load(f)
    with open(f"{corpus_dir}/catalog.json") as f:
        catalog = json.load(f)
    with open(f"{corpus_dir}/policies.json") as f:
        policies = json.load(f)

    supplier_by_id = {s["id"]: s for s in suppliers}

    # --- suppliers collection ---
    supplier_texts = [_supplier_to_text(s) for s in suppliers]
    supplier_ids = [s["id"] for s in suppliers]
    supplier_embeddings = _embed(supplier_texts, input_type="document")

    suppliers_collection = _chroma_client.get_or_create_collection("suppliers")
    suppliers_collection.upsert(
        ids=supplier_ids,
        documents=supplier_texts,
        embeddings=supplier_embeddings,
        metadatas=[{"name": s["name"], "category": s["category"]} for s in suppliers],
    )

    # --- catalog collection ---
    catalog_texts = [
        _catalog_item_to_text(item, supplier_by_id[item["supplier_id"]]["name"]) for item in catalog
    ]
    catalog_ids = [item["product_code"] for item in catalog]
    catalog_embeddings = _embed(catalog_texts, input_type="document")

    catalog_collection = _chroma_client.get_or_create_collection("catalog")
    catalog_collection.upsert(
        ids=catalog_ids,
        documents=catalog_texts,
        embeddings=catalog_embeddings,
        metadatas=[
            {
                "supplier_id": item["supplier_id"],
                "unit_price_min": item["unit_price_min"],
                "unit_price_max": item["unit_price_max"],
                "currency": item["currency"],
            }
            for item in catalog
        ],
    )

    # --- policies collection ---
    policy_texts = [_policy_to_text(p) for p in policies]
    policy_ids = [p["id"] for p in policies]
    policy_embeddings = _embed(policy_texts, input_type="document")

    policies_collection = _chroma_client.get_or_create_collection("policies")
    policies_collection.upsert(
        ids=policy_ids,
        documents=policy_texts,
        embeddings=policy_embeddings,
    )


def query_suppliers(supplier_name: str, n_results: int = 1) -> list[dict]:
    return _query_collection("suppliers", supplier_name, n_results)


def query_catalog(product_code_or_description: str, n_results: int = 1) -> list[dict]:
    return _query_collection("catalog", product_code_or_description, n_results)


def _query_collection(collection_name: str, query_text: str, n_results: int) -> list[dict]:
    collection = _chroma_client.get_collection(collection_name)
    query_embedding = _embed([query_text], input_type="query")[0]
    results = collection.query(query_embeddings=[query_embedding], n_results=n_results)

    output = []
    for i in range(len(results["ids"][0])):
        output.append(
            {
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            }
        )
    return output