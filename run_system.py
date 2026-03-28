from pathlib import Path

from dotenv import load_dotenv
from config import AppConfig
from database.db import init_db


def main():
    load_dotenv()
    cfg = AppConfig.from_env()
    cfg.validate(require_real_executor=cfg.executor_mode == "real")
    init_db(cfg)

    Path("logs").mkdir(exist_ok=True)

    print("Database initialized.")
    print(f"DB path: {cfg.db_path}")
    print(f"Executor mode: {cfg.executor_mode}")
    print("")
    print("Run the following commands in separate terminals:")
    print("1) python -m server.webhook_server")
    print(f"2) ngrok http {cfg.server_port}")
    print("3) streamlit run dashboard/dashboard.py")
    print("")
    print(f"Health check URL: http://localhost:{cfg.server_port}/health")
    print("Webhook auth header: X-Webhook-Token: <WEBHOOK_SHARED_TOKEN>")
    print("Webhook path: /webhook")


if __name__ == "__main__":
    main()

