"""Explicit LAN/MCP smoke test. Creates a clearly named test project, never calls paid providers."""
import argparse
import asyncio
import base64
from datetime import timedelta
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts.lan_host import read_status
from scripts.share_public import share_credentials
from backend.config import now
from backend.mcp_client import open_tools


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:raise SystemExit('使用 --run 验证，将新建一个局域网验收项目，不调用付费模型。')
    status=read_status()
    if status.get('state')!='running':raise SystemExit('请先启动教室共享。')
    base=status['url'];auth_required=status.get('auth_required',True)
    credentials=share_credentials() if auth_required else None
    token=base64.b64encode(':'.join(credentials).encode()).decode() if credentials else None
    a=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    b=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,data=None,method=None,client=a,authorized=True):
        headers={'Content-Type':'application/json'}
        if authorized and token:headers['Authorization']='Basic '+token
        req=urllib.request.Request(base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None,method=method)
        try:
            with client.open(req,timeout=30) as response:return response.status,response.read()
        except urllib.error.HTTPError as exc:
            value=exc.read();exc.close();return exc.code,value
    def api(path,data=None,method=None,client=a):
        code,content=request(path,data,method,client)
        assert code==200,(path,code,content[:300])
        return json.loads(content)
    anonymous_status=request('/api/projects',authorized=False)[0]
    assert anonymous_status==(401 if auth_required else 200)
    p=api('/api/projects',{'name':'教室共享·双端验收','members':['验收同学甲','验收同学乙'],'request_id':uuid4().hex})['id']
    members=api(f'/api/projects/{p}/members')
    mid=members[0]['id']
    sid=api('/api/sessions',{'project_id':p,'member_id':mid})['session_id']
    async def create_task():
        async with open_tools() as tools:
            return await tools.call('create_tasks',{'tasks':[{'title':'等待另一位组员更新的测试任务','description':'仅用于教室共享验收，可用于观察自动更新。',
                'assignee_id':mid,'deadline':(now()+timedelta(days=5)).replace(second=0,microsecond=0).isoformat(),
                'estimated_minutes':30}]},p,'lan-test:'+uuid4().hex)
    created=asyncio.run(create_task());assert created['ok']
    task=created['data'][0]
    result=api('/api/chat',{'project_id':p,'member_id':mid,'session_id':sid,'request_id':uuid4().hex,
        'message':'更新验收任务','action':{'type':'update_task','task_id':task['id'],'patch':{'status':'doing'}}})
    assert result['ok']
    assert api(f'/api/projects/{p}/tasks',client=b)[0]['status']=='doing'
    api(f'/api/review/{p}/instructions',{'text':'这是一份教室共享验收用作业要求。'},'PUT')
    assert api(f'/api/review/{p}',client=b)['instructions']=='这是一份教室共享验收用作业要求。'
    workspace=api(f'/api/review/{p}/materials',{'name':'验收材料.txt','content':'用于测试删除操作的临时文字材料。'})
    material=workspace['materials'][0]['id']
    api(f'/api/review/{p}/materials/{material}',method='DELETE')
    assert not api(f'/api/review/{p}',client=b)['materials']
    assert request(f'/api/review/{p}/human',{'run_id':'not-created','index':0,'status':'needs_work','note':'验收'},'PATCH')[0]==400
    export=api('/api/chat',{'project_id':p,'member_id':mid,'session_id':sid,'request_id':uuid4().hex,
        'message':'导出计划','action':{'type':'export_plan'}})
    code,content=request(export['download']['url']+'?project_id='+p)
    assert code==200 and '## 计划概览' in content.decode()
    context={'project_id':p,'member_id':mid,'session_id':sid,'task_id':task['id'],'url':base}
    (ROOT/'data/lan_live_context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2))
    report={'date':now().date().isoformat(),'auth_required':auth_required,'unauthenticated_api_status':anonymous_status,'independent_clients_share_task_status':True,
        'put_instructions':True,'delete_material':True,'patch_reaches_validation':True,'download_plan':True,
        'model_or_search_called':False,'windows_install_verified':False,'cross_device_wifi_verified':False}
    (ROOT/'docs/LAN_VERIFICATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('验收项目：'+base+'/?project_id='+p)


if __name__=='__main__':main()
