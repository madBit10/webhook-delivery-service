from fastapi import APIRouter

router = APIRouter()

# health route
@router.get("/health")
def health():
    return {"status": "ok"}