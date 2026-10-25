"""生成可公开提交的源码与组员上传包；不包含本机数据、凭据或个人申请材料。"""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib
import json
import re
import shutil
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parent.parent
DOCS=['使用说明.md','组员快速上手.md','ARCHITECTURE.md','DEMO_GUIDE.md',
      '天气接口实现与报告说明.md','回复排版更新说明.md','公开提交与组员上传说明.md',
      '课伴StudyMate_课程作业报告.md']
PICTURES=['001','002','003','004-2-1','004-2-2','004-3-1','004-3-2','005-1','005-2',
          '006-1-1','006-2-1','007-1','007-2','008-1','008-2']
MEMBERS={
 '陈建志':('模型集成与联调',['backend/agent/runner.py','backend/mcp_client.py','backend/services/weather.py'],
             '验证模型、MCP、搜索与天气接口的实际执行；记录输入、工具事件和网页结果。'),
 '陈信儒':('工具、数据库与日程规则',['backend/mcp_server.py','backend/services/tools.py','backend/storage/db.py'],
             '复测任务创建、暂停继续、持久化、日程冲突和导出一致性。'),
 '肖琳瀚':('前端交互与排版',['frontend/app.js','frontend/styles.css','frontend/markdown.js'],
             '复测项目和成员切换、任务负责人提示、回复排版及来源链接。'),
 '陈康泓':('多轮记忆与语音',['backend/memory/store.py','frontend/speech.js'],
             '复测任务指代、刷新恢复、语音转写和录音控制；如实记录朗读手动停止的问题。'),
 '陈憬乐':('回归测试与文档',['tests/','scripts/verify_weather.py','docs/课伴StudyMate_课程作业报告.md'],
             '执行自动测试与演示步骤，整理真实结果、报告与仓库交付检查。')}


def source_files():
    files=[]
    for folder in ('backend','frontend','scripts','tests'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':files.append(p)
    for name in ('README.md','.gitignore','.env.example','requirements.txt','requirements.lock',
                 '启动课伴.command','启动公网分享.command','启动课伴-Windows.bat','启动公网分享-Windows.bat'):
        files.append(ROOT/name)
    files.extend(ROOT/'docs'/name for name in DOCS)
    files.extend((ROOT/'docs').glob('*VERIFICATION*.json'))
    # 公开源码仅保留报告中的在线图片地址，不自动发布本机截图。
    return sorted(set(files))


def main():
    destination=ROOT/'dist/github-upload/StudyMateAgent'
    destination.mkdir(parents=True,exist_ok=True)
    if any(destination.iterdir()):raise SystemExit('目标目录已有文件，未覆盖。请换用空目录或核对后保留旧版。')
    env=dotenv_values(ROOT/'.env') if (ROOT/'.env').exists() else {}
    share=dotenv_values(ROOT/'.env.share') if (ROOT/'.env.share').exists() else {}
    secrets=[env.get('MODEL_API_KEY'),env.get('TAVILY_API_KEY'),share.get('STUDYMATE_ACCESS_PASSWORD')]
    entries=[]
    for source in source_files():
        relative=source.relative_to(ROOT).as_posix()
        content=source.read_bytes()
        if relative=='docs/课伴StudyMate_课程作业报告.md':
            # GitHub Markdown不使用博客园专用的图片尺寸扩展。
            content=re.sub(r'\s+=\d*x\d*(?=\))','',content.decode('utf8')).encode('utf8')
        if source.suffix=='.json' and source.parent==ROOT/'docs':
            # 公开验收记录保留数据，去掉仅本机可用的数据库和工作目录路径。
            value=content.decode('utf8')
            value=re.sub(r'/Users/[^"\n]+', '[本机路径已省略]',value)
            value=re.sub(r'/private/(?:var|tmp)/[^"\n]+', '[临时路径已省略]',value)
            json.loads(value)
            content=value.encode('utf8')
        for secret in secrets:
            if secret and secret.encode() in content:raise SystemExit('停止：文件包含真实凭据：'+relative)
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content):
            raise SystemExit('停止：文件疑似包含私钥：'+relative)
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        mode='100755' if source.suffix=='.command' else '100644'
        if mode=='100755':target.chmod(0o755)
        entries.append({'path':relative,'sha256':hashlib.sha256(content).hexdigest(),'mode':mode,'size':len(content),
                        'git_blob_sha':hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()})
    manifest={'files':entries,'excluded':['.env','.env.share','data/','.venv/','.tools/','dist/','jpg/','个人背景调查材料','本机截图（报告保留在线图链）'],
              'credentials_scanned':True,'real_provider_called':False}
    manifest_path=destination/'发布文件校验.json'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    def archive(path,extra=None):
        with ZipFile(path,'w',ZIP_DEFLATED) as z:
            for item in sorted(destination.rglob('*')):
                if item.is_file():z.write(item,item.relative_to(destination).as_posix())
            if extra:z.writestr('docs/本成员交接说明.md',extra)
        with ZipFile(path) as z:
            assert z.testzip() is None
            for entry in entries:
                assert hashlib.sha256(z.read(entry['path'])).hexdigest()==entry['sha256']
            assert not any(n.startswith(('.env/','data/','.venv/','.tools/')) or n in ('.env','.env.share') for n in z.namelist())
    common=ROOT/'dist/StudyMateAgent-组员上传源码.zip';archive(common)
    member_dir=ROOT/'dist/组员上传包';member_dir.mkdir(exist_ok=True)
    for name,(role,paths,task) in MEMBERS.items():
        instructions=f'''# {name}：{role}\n\n此包包含完整课伴源码，可以单独安装运行。本人重点方向：{task}\n\n主要文件：\n'''+''.join('- `'+p+'`\n' for p in paths)+'''\n先看根目录README和docs/公开提交与组员上传说明.md。只在本机填写.env；不上传数据库、密钥、私人迁移包或虚拟环境。\n\n请填写自己实际完成的修改或测试结果，并使用本人GitHub账号提交；本交接说明本身不表示已完成所有模块开发。若上传到个人仓库，保留团队项目来源。\n'''
        archive(member_dir/(name+'-StudyMateAgent上传包.zip'),instructions)
    print(json.dumps({'directory':str(destination),'files':len(entries)+1,'bytes':sum(e['size'] for e in entries),
                      'source_zip':str(common),'member_zips':len(MEMBERS),'audit':'passed'},ensure_ascii=False))


if __name__=='__main__':main()
