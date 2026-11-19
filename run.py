"""
One-click launcher for the Lead-Lag Bidirectional Sniper Engine & Web Console.
Usage:
    python run.py
Opens FastAPI + WebSocket engine + Built Web Console at http://127.0.0.1:8000
"""
import os
import uvicorn


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        os.environ.setdefault(k, v)
        except Exception:
            pass


_load_dotenv()

if __name__ == "__main__":
    print("=" * 72)
    print("  Lead-Lag 双向脉冲狙击系统 v2.0 (多租户隔离安全版)")
    print("  Web 可视化控制台地址: http://127.0.0.1:8000")
    print("  API 接口文档地址:     http://127.0.0.1:8000/docs")
    print("=" * 72)
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
        ws="websockets-sansio",
        ws_per_message_deflate=False,
    )

