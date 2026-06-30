from fastapi import APIRouter

router = APIRouter()

# health route
@router.get("/health")
def creat_health():
    return {"status": "ok"}