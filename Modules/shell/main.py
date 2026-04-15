from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="viral-shell", version="0.1.0")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="shell")
