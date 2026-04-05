import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProductCatalog(Base):
    __tablename__ = "product_catalog"
    __table_args__ = (
        UniqueConstraint("company_id", "sku", name="uq_product_catalog_company_sku"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    sat_product_code = Column(String, nullable=False, default="78101800", server_default="78101800")
    unit_code = Column(String, nullable=False, default="E48", server_default="E48")
    unit_name = Column(String, nullable=True, default="Unidad de servicio")
    price = Column(Float, nullable=False, default=0.0, server_default="0")
    currency = Column(String, nullable=False, default="MXN", server_default="MXN")
    tax_rate = Column(Float, nullable=False, default=0.16, server_default="0.16")
    tax_object = Column(String, nullable=False, default="02", server_default="02")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("BillingDraftItem", back_populates="product")


class BillingDraft(Base):
    __tablename__ = "billing_draft"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="draft", server_default="draft", index=True)

    customer_name = Column(String, nullable=True)
    customer_rfc = Column(String, nullable=True)
    customer_zip = Column(String, nullable=True)
    customer_regimen = Column(String, nullable=True)
    customer_use_cfdi = Column(String, nullable=True)

    emitter_rfc = Column(String, nullable=False, default="IIA040805DZ4", server_default="IIA040805DZ4")
    emitter_name = Column(String, nullable=False, default="INDISTRIA ILUMINADORA DE ALMACENES", server_default="INDISTRIA ILUMINADORA DE ALMACENES")
    emitter_regimen = Column(String, nullable=False, default="626", server_default="626")
    place_of_issue = Column(String, nullable=False, default="32690", server_default="32690")

    currency = Column(String, nullable=False, default="MXN", server_default="MXN")
    payment_method = Column(String, nullable=False, default="PUE", server_default="PUE")
    payment_form = Column(String, nullable=False, default="01", server_default="01")
    series = Column(String, nullable=True, default="PF")
    folio = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    subtotal = Column(Float, nullable=False, default=0.0, server_default="0")
    taxes = Column(Float, nullable=False, default=0.0, server_default="0")
    total = Column(Float, nullable=False, default=0.0, server_default="0")

    stamped_at = Column(DateTime, nullable=True)
    stamp_status = Column(String, nullable=False, default="not_sent", server_default="not_sent")
    stamped_xml_base64 = Column(Text, nullable=True)
    stamped_response_json = Column(Text, nullable=True)

    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship(
        "BillingDraftItem",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="BillingDraftItem.created_at",
    )


class BillingDraftItem(Base):
    __tablename__ = "billing_draft_item"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("billing_draft.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(String, nullable=False, index=True)
    product_id = Column(String, ForeignKey("product_catalog.id"), nullable=True)

    sku = Column(String, nullable=True)
    description = Column(String, nullable=False)
    sat_product_code = Column(String, nullable=False, default="78101800", server_default="78101800")
    unit_code = Column(String, nullable=False, default="E48", server_default="E48")
    quantity = Column(Float, nullable=False, default=1.0, server_default="1")
    unit_price = Column(Float, nullable=False, default=0.0, server_default="0")
    tax_rate = Column(Float, nullable=False, default=0.16, server_default="0.16")
    tax_object = Column(String, nullable=False, default="02", server_default="02")

    line_subtotal = Column(Float, nullable=False, default=0.0, server_default="0")
    line_taxes = Column(Float, nullable=False, default=0.0, server_default="0")
    line_total = Column(Float, nullable=False, default=0.0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)

    draft = relationship("BillingDraft", back_populates="items")
    product = relationship("ProductCatalog", back_populates="items")
