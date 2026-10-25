import os
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
TZ = ZoneInfo('Asia/Shanghai')
PROJECT_ID = 'project_demo'


def now():
    return datetime.now(TZ)


def data_dir():
    path = Path(os.getenv('STUDYMATE_DATA_DIR', str(ROOT / 'data'))).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
