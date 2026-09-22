from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.domain.order import CancellationNotAllowedError, Order
from app.repository import OrderRepository

app = FastAPI(title="AEM Order Service")
app.state.order_repository = OrderRepository()
app.state.audit_records = []
app.state.notifications = []


class OrderCreateRequest(BaseModel):
    customer_email: EmailStr
    amount: float = Field(gt=0)
    status: str = Field(min_length=1)


class OrderUpdateRequest(BaseModel):
    customer_email: EmailStr
    amount: float = Field(gt=0)
    status: str = Field(min_length=1)


class OrderResponse(BaseModel):
    id: int
    customer_email: EmailStr
    amount: float
    status: str
    created_at: str


class AuditRecordResponse(BaseModel):
    order_id: int
    action: str
    at: str


class NotificationResponse(BaseModel):
    order_id: int
    type: str
    recipient: EmailStr
    message: str
    at: str


def get_order_or_404(repository: OrderRepository, order_id: int) -> Order:
    try:
        return repository.get(order_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with id {order_id} not found",
        ) from exc


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreateRequest) -> Any:
    repository: OrderRepository = app.state.order_repository
    next_id = len(repository.list()) + 1
    normalized_status = payload.status.strip().lower()
    allowed_statuses = {"new", "pending", "confirmed", "paid", "shipped", "cancelled"}
    if normalized_status not in allowed_statuses:
        raise ValueError(
            "status must be one of: new, pending, confirmed, paid, shipped, cancelled"
        )
    order = Order(
        id=next_id,
        customer_email=payload.customer_email,
        amount=payload.amount,
        status=normalized_status,
    )
    order.touch()
    created = repository.create(order)
    return OrderResponse(
        id=created.id,
        customer_email=created.customer_email,
        amount=created.amount,
        status=created.status,
        created_at=created.created_at.isoformat(),
    )


@app.get("/orders", response_model=list[OrderResponse])
def list_orders(status: str | None = Query(default=None)) -> Any:
    repository: OrderRepository = app.state.order_repository
    orders = repository.list()
    if status is not None:
        normalized = status.strip().lower()
        orders = [order for order in orders if order.status.lower() == normalized]
    return [
        OrderResponse(
            id=order.id,
            customer_email=order.customer_email,
            amount=order.amount,
            status=order.status,
            created_at=order.created_at.isoformat(),
        )
        for order in orders
    ]


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int) -> Any:
    repository: OrderRepository = app.state.order_repository
    order = get_order_or_404(repository, order_id)
    return OrderResponse(
        id=order.id,
        customer_email=order.customer_email,
        amount=order.amount,
        status=order.status,
        created_at=order.created_at.isoformat(),
    )


@app.put("/orders/{order_id}", response_model=OrderResponse)
def update_order(order_id: int, payload: OrderUpdateRequest) -> Any:
    repository: OrderRepository = app.state.order_repository
    order = get_order_or_404(repository, order_id)

    try:
        order.validate_email_change(payload.customer_email)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    normalized_status = payload.status.strip().lower()
    allowed_statuses = {"new", "pending", "confirmed", "paid", "shipped", "cancelled"}
    if normalized_status not in allowed_statuses:
        raise ValueError(
            "status must be one of: new, pending, confirmed, paid, shipped, cancelled"
        )

    updated = Order(
        id=order.id,
        customer_email=payload.customer_email,
        amount=payload.amount,
        status=normalized_status,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
    updated.touch()
    repository.update(order_id, updated)
    return OrderResponse(
        id=updated.id,
        customer_email=updated.customer_email,
        amount=updated.amount,
        status=updated.status,
        created_at=updated.created_at.isoformat(),
    )


@app.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(order_id: int) -> Any:
    repository: OrderRepository = app.state.order_repository
    order = get_order_or_404(repository, order_id)

    try:
        order.cancel()
    except CancellationNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order can only be cancelled from NEW, PENDING, or CONFIRMED status",
        ) from exc

    order.touch()
    repository.update(order_id, order)
    occurred_at = datetime.now(timezone.utc).isoformat()
    app.state.audit_records.append(
        {"order_id": order.id, "action": "order_cancelled", "at": occurred_at}
    )
    app.state.notifications.append(
        {
            "order_id": order.id,
            "type": "order_cancelled",
            "recipient": str(order.customer_email),
            "message": f"Order {order.id} was cancelled",
            "at": occurred_at,
        }
    )
    return OrderResponse(
        id=order.id,
        customer_email=order.customer_email,
        amount=order.amount,
        status=order.status,
        created_at=order.created_at.isoformat(),
    )


@app.get("/orders/{order_id}/audit-records", response_model=list[AuditRecordResponse])
def list_audit_records(order_id: int) -> list[AuditRecordResponse]:
    repository: OrderRepository = app.state.order_repository
    get_order_or_404(repository, order_id)
    return [
        AuditRecordResponse(**record)
        for record in app.state.audit_records
        if record["order_id"] == order_id
    ]


@app.get("/orders/{order_id}/notifications", response_model=list[NotificationResponse])
def list_notifications(order_id: int) -> list[NotificationResponse]:
    repository: OrderRepository = app.state.order_repository
    get_order_or_404(repository, order_id)
    return [
        NotificationResponse(**notification)
        for notification in app.state.notifications
        if notification["order_id"] == order_id
    ]
