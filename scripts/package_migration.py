"""生成只转自己设备的迁移包；SQLite一致性快照，不复制Mac虚拟环境。"""
import argparse
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent


def build(source_zip, database, destination, root=ROOT, include_model_config=False):
    database = Path(database).resolve()
    if not database.is_file():
        raise FileNotFoundError('没有找到原数据库，未创建空数据库替代。')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='studymate-migrate-') as folder:
        snapshot = Path(folder) / 'studymate.sqlite3'
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('数据库快照完整性检查失败，未交付迁移包。')
                counts = {name: target.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                          for name in ('projects', 'members', 'tasks', 'sessions')}
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            with ZipFile(output, 'w', ZIP_DEFLATED) as archive, ZipFile(source_zip) as public:
                for item in public.infolist():
                    if item.filename.endswith('/.env') or '/data/' in item.filename or '/.venv/' in item.filename:
                        raise RuntimeError('源码包不应含真实配置或数据，请先重新生成源码包。')
                    archive.writestr(item, public.read(item))
                archive.write(snapshot, 'StudyMate/data/studymate.sqlite3')
                copied = []
                if include_model_config:
                    for name in ('.env', '.env.share'):
                        config = Path(root) / name
                        if config.is_file():
                            archive.write(config, 'StudyMate/' + name); copied.append(name)
                notice = ('这是私人设备迁移包，仅转到你自己的ROG，不要发到组群、博客或公共仓库。\n'
                          '包含程序、任务和聊天数据库；配置文件：' + ('、'.join(copied) or '未包含，需在新电脑自行配置') + '\n'
                          'Windows安装Python3.11以上版本，建议3.12。解压到全新的文件夹，双击“启动课伴-Windows.bat”。\n'
                          '确认本机数据正确后，双击“启动公网分享-Windows.bat”，把新生成的网址给组员。\n'
                          '详细说明：docs/ROG主机迁移说明.md。Mac和ROG数据不会自动同步，以ROG为后续唯一主机。\n')
                archive.writestr('StudyMate/迁移前先看.txt', notice)
    return {'path': str(destination), 'counts': counts, 'model_config_included': '.env' in copied}


def main():
    parser = argparse.ArgumentParser(description='导出自己的课伴数据到新主机；不用于组员分发。')
    parser.add_argument('--include-model-config', action='store_true', help='同时带上本机模型与分享配置，只转自己的设备')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from backend.config import data_dir
    from scripts.package import main as package
    package()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = data_dir() / 'migration' / f'ROG私人迁移包-{stamp}-{uuid4().hex[:6]}.zip'
    print(build(ROOT / 'dist/StudyMate-v0.1.zip', data_dir() / 'studymate.sqlite3', destination,
                include_model_config=args.include_model_config))


if __name__ == '__main__':
    main()
