"""E：检查配置，不输出密钥，不发送任何付费模型请求。"""
import importlib.metadata
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import data_dir


def main():
    print(f'Python: {sys.version.split()[0]}')
    if sys.version_info < (3, 11):
        raise SystemExit('需要 Python 3.11 或以上版本。')
    for package in ('fastapi', 'mcp', 'uvicorn', 'httpx', 'python-dotenv'):
        print(f'{package}: {importlib.metadata.version(package)}')
    mode = os.getenv('AGENT_MODE', 'demo')
    print(f'模式: {mode}')
    print(f'模型密钥: {"已设置（不显示）" if os.getenv("MODEL_API_KEY", "").strip() else "未设置"}')
    print(f'数据目录可写: {os.access(data_dir(), os.W_OK)}')
    from backend.services.search import configuration
    search = configuration()
    print(f'联网搜索: {"已配置" if search["available"] else "未启用"}；本应用月限额: {search["monthly_limit"]}')
    if mode == 'llm' and not os.getenv('MODEL_API_KEY', '').strip():
        raise SystemExit('真实模式需要在 .env 填写 MODEL_API_KEY。')
    print('本地依赖检查通过。此检查未验证模型/语音服务，也未发起付费调用。')


if __name__ == '__main__':
    main()
