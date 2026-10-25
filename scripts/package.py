"""生成供组员接手的源码包；显式白名单，不打包密钥、数据库或虚拟环境。"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ['backend', 'frontend', 'scripts', 'tests', 'docs', 'README.md', 'requirements.txt',
           'requirements.lock', '.env.example', '.gitignore', '启动课伴.command', '启动公网分享.command',
           '启动课伴-Windows.bat', '启动公网分享-Windows.bat']


def main():
    destination = ROOT / 'dist' / 'StudyMate-v0.1.zip'
    destination.parent.mkdir(exist_ok=True)
    files = []
    for name in INCLUDE:
        path = ROOT / name
        for file in path.rglob('*') if path.is_dir() else [path]:
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                files.append(file)
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, 'StudyMate/' + file.relative_to(ROOT).as_posix())
    print(f'已生成：{destination}（{len(files)} 个文件，无 .env、data/ 或 .venv/）')


if __name__ == '__main__':
    main()
