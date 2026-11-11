"""
One-click launcher for the Lead-Lag Bidirectional Sniper Engine & Web Console.
Usage:
    python run.py
Opens FastAPI + WebSocket engine + Built Web Console at http://127.0.0.1:8000
"""
import uvicorn

if __name__ == "__main__":
    print("=" * 72)
    print("  Lead-Lag 双向脉冲狙击系统 v2.0 (审查修复全栈版)")
    print("  Web 可视化控制台地址: http://127.0.0.1:8000")
    print("  API 接口文档地址:     http://127.0.0.1:8000/docs")
    print("=" * 72)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, log_level="info")

