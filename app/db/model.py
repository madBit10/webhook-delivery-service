from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, JSON, DateTime, CheckConstraint, func, text
from datetime import datetime, timezone
from app.db.database import Base

class Endpoint(Base):
    __tablename__ = "endpoints"
    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    event_types = Column(String, nullable=False) # CSV for now; migrate to JSON/ARRAY later
    is_active = Column(Boolean, default=True, nullable=False, server_default=text("true"))  
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, server_default=func.now())
    # new table arguments to check the status added are the from the default set in the db
    __table_args__ = (CheckConstraint("status IN ('pending', 'delivered', 'dead')", name = "ck_events_status"),)

class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    attempted_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())
    success = Column(Boolean, nullable=False)
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(String, nullable=True)
    attempt_number = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=True) 
    # add table args to check if the attempt_number is non-negative or greater than 0
    __table_args__ = (CheckConstraint("attempt_number > 0", name="ck_delivery_attempts_attempt_number"),)

