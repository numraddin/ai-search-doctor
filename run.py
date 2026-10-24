"""Start the app:  python run.py   then open http://127.0.0.1:8000"""
import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=config.APP_PORT)
