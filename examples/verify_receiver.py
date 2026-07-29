import hmac
import hashlib
import time
import os

from fastapi import FastAPI, Request, HTTPException

app = FastAPI() # define a fastapi app

SECRET = os.environ["WEBHOOK_SECRET"]

@app.post("/webhook")
async def receive(request: Request):
    raw = await request.body() # bytes, NOT request.json()
    ts = request.headers.get("X-Webhook-Timestamp") # we get the timestamp in the headers parsed from the services event.py in the delivery attempt response body
    sig = request.headers.get("X-Webhook-Signature") # get the signature too from the headers parsed in the delivery attempt response body

    # headers present at all?
    if ts is None or sig is None:
        print("REJECTED: missing headers")
        raise HTTPException(status_code=401, detail="invalid signature")

    # timstamp parseable? (still UNTRUSTED data at this point)
    try:
        ts_int = int(ts)
    except ValueError:
        print("REJECTED: unparseable timestamp")
        raise HTTPException(status_code=401, detail="invalid signature")

    # fresh? reject anything more than 5 minutes off, in either direction
    if abs(time.time() - ts_int) > 300:
        print("REJECTED: stale timestamp")
        raise HTTPException(status_code=401, detail="invalid signature")

    # does the signature match to what we compute ourselves?

    expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256,).hexdigest() # what the signature should be 

    # compare the hmac strings computed and the ones in the db
    comparison = hmac.compare_digest(expected, sig)

    if not comparison:
        print("REJECTED: signature missmatch")
        raise HTTPException(status_code=401, detail="invalid signature")

    # verified signature

    print(f"VALID: {raw.decode()}")
    return {"received": True}

