from sqlalchemy import Column, Integer, String, Boolean
from app.db.database import Base

class Endpoint(Base):
    __tablename__ = "endpoints"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    event_types = Column(String, nullable=False) # CSV for now; migrate to JSON/ARRAY later
    is_active = Column(Boolean, default=True, nullable=False)  