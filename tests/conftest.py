import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db.database import Base
from app.db import model
from app.core.config import settings
from app.main import app
from app.db.database import get_db


# test engine - derives the URL from the existing settings rather than duplicating the credentials

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or settings.database_url + "_test"

# creating the test engine 
test_engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind = test_engine) # bind the testing session with the test engine created

@pytest.fixture(scope="session", autouse=True)
def create_schema():
    """Build the schema once for the whole run; drop it at the end"""

    Base.metadata.create_all(bind = test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)

@pytest.fixture
def db():
    """A fresh session per test, with every table emptied afterwards."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        with test_engine.begin() as conn:
            conn.execute(text("TRUNCATE delivery_attempts, events, endpoints RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()