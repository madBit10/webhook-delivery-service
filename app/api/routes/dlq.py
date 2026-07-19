from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.dlq import replay_dead_letters
from app.api.schemas.dlq import ReplayResult

router = APIRouter()

@router.post("/dlq/replay", response_model=ReplayResult)
def replay(db: Session = Depends(get_db)):
    ids = replay_dead_letters(db)
    return {"replayed": ids, "count": len(ids)}