from fastapi import FastAPI, Response

app = FastAPI()

@app.post("/status/{code}")
def echo_status(code: int):
    return Response(status_code=code)