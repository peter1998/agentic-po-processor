"""Storage layer: two tables with deliberately different shapes.

`purchase_orders` + `order_items` are normalized — this is the "source of
truth" data, and it's meant to be queried (by supplier, by date, etc.).

`pending_reviews` stores the full extracted PurchaseOrder as a JSON blob
plus the reason it was flagged. It's not meant to be queried relationally —
its job is to preserve full context for a human to inspect, not efficiency.
On approval, the blob is parsed back into a PurchaseOrder and inserted
normally into purchase_orders/order_items."""

from datetime import datetime, date as date_type

from sqlalchemy import create_engine, ForeignKey, String, Float, Integer, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from models.schema import PurchaseOrder
from utils.config import settings


class Base(DeclarativeBase):
    pass


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_number: Mapped[str | None] = mapped_column(String, nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="EUR")
    delivery_date: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    items: Mapped[list["OrderItemRow"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItemRow(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    product_code: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    order: Mapped["PurchaseOrderRow"] = relationship(back_populates="items")


class PendingReviewRow(Base):
    __tablename__ = "pending_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String)
    review_reason: Mapped[str] = mapped_column(Text)
    extracted_data_json: Mapped[str] = mapped_column(Text)  # full PurchaseOrder, serialized
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | approved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = create_engine(f"sqlite:///{settings.database_path}")
_SessionLocal = sessionmaker(bind=_engine)


def init_db() -> None:
    Base.metadata.create_all(_engine)


def save_approved_order(order: PurchaseOrder, correlation_id: str) -> int:
    """Inserts a validated order, normalized across purchase_orders + order_items."""
    with _SessionLocal() as session:
        row = PurchaseOrderRow(
            po_number=order.po_number,
            supplier_name=order.supplier.name,
            supplier_id=order.supplier.id,
            total_amount=order.total_amount,
            currency=order.currency,
            delivery_date=order.delivery_date.isoformat() if isinstance(order.delivery_date, date_type) else order.delivery_date,
            correlation_id=correlation_id,
            items=[
                OrderItemRow(
                    product_code=item.product_code,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total_price=item.total_price,
                )
                for item in order.items
            ],
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def save_pending_review(order: PurchaseOrder, reason: str, correlation_id: str) -> int:
    """Stores the full order as JSON, not normalized — this is a snapshot
    for a human to inspect, not queryable business data yet."""
    with _SessionLocal() as session:
        row = PendingReviewRow(
            correlation_id=correlation_id,
            review_reason=reason,
            extracted_data_json=order.model_dump_json(),
            status="pending",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def get_pending_review(review_id: int) -> dict | None:
    with _SessionLocal() as session:
        row = session.get(PendingReviewRow, review_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "correlation_id": row.correlation_id,
            "review_reason": row.review_reason,
            "extracted_data": PurchaseOrder.model_validate_json(row.extracted_data_json),
            "status": row.status,
            "created_at": row.created_at,
        }


def approve_pending_review(review_id: int) -> int:
    """Moves a pending review into the normalized purchase_orders table.
    Returns the new purchase_orders.id. Raises ValueError if the review
    doesn't exist or was already approved."""
    with _SessionLocal() as session:
        row = session.get(PendingReviewRow, review_id)
        if row is None:
            raise ValueError(f"No pending review with id {review_id}")
        if row.status == "approved":
            raise ValueError(f"Review {review_id} was already approved")

        order = PurchaseOrder.model_validate_json(row.extracted_data_json)
        new_order_id = save_approved_order(order, correlation_id=row.correlation_id)

        row.status = "approved"
        session.add(row)
        session.commit()

        return new_order_id


def list_pending_reviews() -> list[dict]:
    with _SessionLocal() as session:
        rows = session.query(PendingReviewRow).filter_by(status="pending").all()
        return [
            {
                "id": r.id,
                "correlation_id": r.correlation_id,
                "review_reason": r.review_reason,
                "created_at": r.created_at,
            }
            for r in rows
        ]