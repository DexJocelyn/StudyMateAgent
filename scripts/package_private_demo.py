"""Explicit opt-in private demo packaging: share the model key, not the host database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from dotenv import dotenv_values
from scripts.package_members import ROOT, RELEASE_VERSION, RELEASE_TAG, build as build_public, batch, mac

GUIDE = '''# 课伴 · 小组演示私密版

模型已配置好，共用陈建志的模型API额度，不用填写Key，也不需要他的Mac或ROG开机。

本版更新于2026年11月6日，已包含团队分工修正、个人时间安排、作业检查与导出，以及网站书页勾选Logo。

## Windows怎么用

1. 完整解压本包，再打开StudyMate文件夹；不要在ZIP里面直接运行。
2. 双击“启动课伴-Windows.bat”。首次自动创建运行环境并联网安装依赖，等待安装完成。
3. 浏览器打开 http://127.0.0.1:8000/ 。保留启动窗口，按Ctrl+C停止；以后仍然双击同一个启动文件。

仍需本机有Python 3.11或以上版本，现有3.13可以先尝试；若没有Python，启动窗口会提示。它是解压运行包，不是自带Python的EXE。Windows实机仍需组员验证。

Mac双击“启动课伴-Mac.command”；若无法直接运行，在解压后的目录用终端执行：
`python3 scripts/windows_start.py`

## 演示什么

- 新建“体验·自己的姓名”项目，填入本人姓名并选择聊天成员。
- 帮小组分工：输入要求，检查并采用任务草案。
- 安排我的时间：保存自己的未来空闲时间，生成日程并确认。
- 检查作业：导入examples中的老师要求和报告初稿，检查缺项，人工复核后生成补充任务。
- 支持普通问答和天气工具；本包只共享模型Key，未附带Tavily搜索Key，因此通用联网搜索未启用。

日期需选未来时段，保存后可以刷新验证。每次AI问答和作业检查都会使用共享账号额度。

## 几点说明

本包含真实模型Key，仅私发本组演示使用，不要上传GitHub、博客、公共网盘或再转发。程序中的Key能被拿到文件的人读取；演示结束后可由Key持有人在平台停用或更换。不要把.env截图发出去。

每台电脑的数据独立保存于本包data文件夹，不会自动同步成同一个项目；停止或重启后，已有数据仍保留。包里没有陈建志原来的项目、聊天和数据库。

如果打不开页面，先看启动窗口是否报错、是否关闭；端口8000若已被旧课伴占用，先停止旧版本，避免误用旧网页。若模型报401/403/额度不足，把脱敏错误反馈给Key持有人，不需要自己重新配置模型。

语音依赖浏览器权限；部分设备的停止朗读仍待复测。PDF、OCR和自动评分尚未支持。
'''


def build(destination, root=ROOT, include_model_key=False):
    if not include_model_key:
        raise ValueError('私密演示包必须明确选择包含模型Key。')
    root=Path(root);destination=Path(destination)
    env=dotenv_values(root/'.env',interpolate=False)
    key=(env.get('MODEL_API_KEY') or '').strip()
    if not key:
        raise ValueError('本机未配置模型Key，未生成空配置演示包。')
    # Public packaging performs credential scanning first; secrets enter only the final .env.
    with tempfile.TemporaryDirectory(prefix='studymate-clean-source-') as folder:
        public=build_public(Path(folder)/'public.zip',root=root)
        with ZipFile(public) as archive:
            files={name.removeprefix('StudyMate/'):archive.read(name) for name in archive.namelist()}
    files.pop('文件校验.json',None)
    files.pop('README-先看这里.md',None)
    for name in list(files):
        if name.startswith(('1-配置自己的模型','2-启动课伴')):
            files.pop(name)
    config={'AGENT_MODE':'llm','MODEL_BASE_URL':env.get('MODEL_BASE_URL') or 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'MODEL_NAME':env.get('MODEL_NAME') or 'qwen-plus','MODEL_API_KEY':key,
            'MODEL_TIMEOUT':env.get('MODEL_TIMEOUT') or '45','AGENT_MAX_ROUNDS':env.get('AGENT_MAX_ROUNDS') or '8',
            'TAVILY_API_KEY':'','WEB_SEARCH_ENABLED':'0','WEATHER_ENABLED':'1'}
    files['.env']=('\n'.join(k+'='+json.dumps(v,ensure_ascii=False) for k,v in config.items())+'\n').encode()
    files['README-演示版先看.md']=GUIDE.encode()
    files['启动课伴-Windows.bat']=batch().encode()
    files['启动课伴-Mac.command']=mac().encode()
    manifest={'version':RELEASE_VERSION,'private_demo':True,'contains_model_api_key':True,'contains_search_api_key':False,
              'contains_host_database':False,'windows_runtime_verified':False,
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
    files['文件校验.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream,ZipFile(stream,'w',ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            info=ZipInfo('StudyMate/'+name);info.create_system=3;info.compress_type=ZIP_DEFLATED
            info.external_attr=(0o100600 if name=='.env' else 0o100755 if name.endswith('.command') else 0o100644)<<16
            archive.writestr(info,data)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('StudyMate/'+name)).hexdigest()==digest
        assert sum(key.encode() in archive.read(n) for n in archive.namelist())==1
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='生成含真实模型Key的小组私密演示包，不包含原数据库。')
    parser.add_argument('--include-model-key',action='store_true')
    args=parser.parse_args()
    path=build(ROOT/f'dist/private/课伴-小组演示私密版-{RELEASE_TAG}.zip',include_model_key=args.include_model_key)
    print('私密演示包已生成，模型Key未显示。文件：'+str(path))
