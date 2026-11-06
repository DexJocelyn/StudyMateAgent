"""Build a clean, independently runnable team ZIP using an explicit runtime whitelist."""
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED
from scripts.configure_model import read_values

ROOT = Path(__file__).resolve().parent.parent
RELEASE_VERSION = '2026-11-06'
RELEASE_TAG = RELEASE_VERSION.replace('-', '')
GUIDE = '''# 课伴 · 组员独立体验版

这是完整本机运行包，不连接陈建志的Mac或ROG。Windows和Mac均可运行；它不是免Python的EXE安装器。

## 第一次使用（Windows）

1. 安装Python 3.11或以上版本；现有3.13可先尝试。本包未在Windows实机完成验收。没有Python时，启动窗口会明确提示。Python官方入口：https://www.python.org/downloads/windows/
2. 先完整解压到普通文件夹（例如桌面StudyMate），不要在压缩包内直接双击。
3. 双击“1-配置自己的模型-Windows.bat”。选择1，填写服务商提供的OpenAI兼容Base URL、模型ID和自己的API Key。密钥输入时不显示。千问业务空间密钥需使用对应的业务空间地址；Base URL不带/chat/completions。
4. 双击“2-启动课伴-Windows.bat”。首次自动创建.venv并联网安装requirements.lock中的依赖，可能需要几分钟。
5. 保留启动窗口，浏览器打开 http://127.0.0.1:8000/ 。以后直接双击启动文件；按Ctrl+C停止。

没有模型密钥时，配置时选2可体验有限规则演示，但不能自由聊天，也不能执行作业自查。模型会员账号不等于API Key。首次安装依赖和真实AI调用均需要网络；API费用由各自模型账号承担。

## Mac

双击“1-配置自己的模型-Mac.command”，再双击“2-启动课伴-Mac.command”。同样需要Python 3.11+、首次联网安装依赖。若系统不允许直接运行，可在终端进入解压后的目录执行：

```sh
python3 scripts/configure_model.py
python3 scripts/windows_start.py
```

## 功能与模型要求

三个入口：帮小组分工、安排我的时间、检查作业。支持项目管理、任务暂停继续、对话记忆、计划和自查报告下载。支持TXT、MD、DOCX正文，不支持PDF、OCR或自动评分。

模型服务需要兼容Chat Completions、多轮工具调用，以及JSON输出模式。千问已做过真实验证；其他模型需自行验证，普通问答有回复不代表全部Agent功能兼容。详细说明见docs/组员接入自己的模型.md。

天气使用独立天气接口，默认无需额外密钥；一般联网搜索另需Tavily密钥，配置时可跳过。接入模型不代表模型自己能联网。关闭网络或供应商不可用时，对应功能可能失败，程序不会伪造实时结果。

## 自己操作一次

1. 新建“体验·自己的姓名”项目，填写本人姓名并切换聊天成员。
2. 普通对话：“用两句话解释数据库索引”。
3. 创建任务：“给我创建整理需求任务，预计30分钟，截止北京时间2030年11月8日20:00。” 日期仅为测试示例，实际请改成未来的日期。核对任务卡，再说“暂停刚才的任务”。
4. 安排时间：为自己填写未来的空闲时段，生成我的日程草案，确认后保存。
5. 作业检查：导入examples中的“老师要求.txt”和“报告初稿.md”，开始检查。预期能找到目标用户与技术方案，指出分工表缺失。人工复核后创建补充任务。
6. 上传“成员分工补充.md”后重新检查；材料变化会令旧结果过期。调用模型消耗供应商额度，请核对原文再确认。

## 数据与协作

每个人电脑上的项目、任务和聊天分别保存在本包data目录里，刷新或重启可恢复。大家都填写相同项目名，也不会自动同步。不要删除data来更新版本；新版代码替换前先关闭程序并备份自己的data和.env。

包里没有原主机的密钥、真实数据库或对话。每个人自己配置.env，不要把它提交GitHub或发到群里。换模型时重新运行配置入口，保存后重启；一台后端共用一套模型配置，切换成员不会切换模型。

## 常见问题

- Python未找到：修复本机Python安装或PATH；不一定是VPN问题。
- 首次安装依赖失败：查看窗口错误，确认网络能访问Python包源后重试；启动器不会静默替换依赖版本。
- AI接口401/403：核对自己的密钥、地域、业务空间和权限；404核对Base URL及模型ID；400核对工具和JSON参数支持。不要截图密钥。
- 没有搜索结果：先确认Tavily配置；天气接口不等于一般搜索。
- 页面打不开：确认启动窗口仍开着，端口8000没有被其他程序占用。若旧课伴已运行，先停止旧版本，避免误打开旧服务。
- 语音依赖浏览器和麦克风权限。已知部分设备的“停止朗读”仍需复测，不作为已修复功能承诺。

本包是2026年11月6日的功能快照，含个人排程、项目管理、简洁作业检查、中文时间导出、团队分工修正与书页勾选Logo。正式上线和多人共享是另外的部署方式。
'''


def batch(configure=False):
    args=' --configure' if configure else ''
    return ('@echo off\nchcp 65001 >nul\nsetlocal\ncd /d "%~dp0"\n'
            'call "%~dp0scripts\\windows_bootstrap.bat"'+args+'\n'
            'if errorlevel 1 echo 操作未完成，请查看上方错误。\npause\n').replace('\n','\r\n')


def mac(configure=False):
    args=' --configure' if configure else ''
    return '''#!/bin/zsh
cd "$(dirname "$0")"
runtime=""
for candidate in python3.12 python3.13 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,11))'; then
    runtime="$candidate"
    break
  fi
done
if [[ -z "$runtime" ]]; then
  echo '请先安装Python3.11或以上版本。'
else
  "$runtime" scripts/windows_start.py'''+args+'''
fi
read 'reply?按回车关闭窗口…'
'''


def contents(root):
    root=Path(root); files={}
    for folder in ('backend','frontend'):
        for p in (root/folder).rglob('*'):
            relative=p.relative_to(root)
            if p.is_file() and not any(part.startswith('.') or part=='__pycache__' for part in relative.parts) and p.suffix!='.pyc':
                files[relative.as_posix()]=p.read_bytes()
    for relative in ('.gitignore','requirements.txt','requirements.lock','.env.example','scripts/windows_start.py',
                     'scripts/windows_bootstrap.bat','scripts/check_env.py','scripts/configure_model.py',
                     'docs/组员接入自己的模型.md','docs/个人任务与时间安排.md','docs/作业自查_使用与验收说明.md'):
        files[relative]=(root/relative).read_bytes()
    files['README-先看这里.md']=GUIDE.encode()
    for label,configure in [('1-配置自己的模型',True),('2-启动课伴',False)]:
        files[label+'-Windows.bat']=batch(configure).encode()
        files[label+'-Mac.command']=mac(configure).encode()
    examples=root/'examples/review'
    for source,target in [('老师要求.txt','老师要求.txt'),('报告初稿.md','报告初稿.md'),('成员分工补充.md','成员分工补充.md')]:
        files['examples/'+target]=(examples/source).read_bytes()
    return files


def build(destination=None,root=ROOT):
    root=Path(root); destination=Path(destination or root/f'dist/课伴-组员独立运行版-{RELEASE_TAG}.zip')
    files=contents(root)
    secrets=[]
    for name in ('.env','.env.share'):
        path=root/name
        if path.exists():
            config=read_values(path.read_text(encoding='utf-8-sig'))
            secrets.extend(v for k,v in config.items() if v and any(word in k for word in ('KEY','PASSWORD','TOKEN','SECRET')))
    for name,content in files.items():
        if any(secret.encode() in content for secret in secrets) or re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content):
            raise RuntimeError('文件疑似包含真实凭据，未打包：'+name)
    manifest={'version':RELEASE_VERSION,'kind':'local-source-runtime','python_required':True,'secrets_scanned':True,
              'shared_database':False,'windows_runtime_verified':False,
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
    files['文件校验.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as out, ZipFile(out,'w',ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            from zipfile import ZipInfo
            info=ZipInfo('StudyMate/'+name);info.create_system=3
            info.external_attr=(0o100755 if name.endswith('.command') else 0o100644)<<16
            info.compress_type=ZIP_DEFLATED
            archive.writestr(info,data)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('StudyMate/'+name)).hexdigest()==digest
    return destination


if __name__=='__main__':
    print(build())
