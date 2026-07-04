from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, JSON, DateTime
from datetime import datetime, timezone
from app.db.database import Base

class Endpoint(Base):
    __tablename__ = "endpoints"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    event_types = Column(String, nullable=False) # CSV for now; migrate to JSON/ARRAY later
    is_active = Column(Boolean, default=True, nullable=False)  

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

