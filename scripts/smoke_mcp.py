"""在临时库进行真实 MCP create/list/update/export，退出时自动清理临时目录。"""
import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def verify():
    from backend.mcp_client import open_tools
    async with open_tools() as client:
        assert {'manage_member', 'create_tasks', 'list_tasks', 'update_task', 'export_plan'} <= set(client.definitions)
        task = {'title': 'MCP 验收任务', 'assignee_id': 'member_a',
                'deadline': '2030-10-25T22:00:00+08:00', 'estimated_minutes': 30}
        created = await client.call('create_tasks', {'tasks': [task]}, 'project_demo', 'create')
        assert created['ok'], created
        listed = await client.call('list_tasks', {}, 'project_demo', 'list')
        assert len(listed['data']) == 1
        updated = await client.call('update_task', {'task_id': created['data'][0]['id'], 'patch': {'status': 'done'}}, 'project_demo', 'done')
        assert updated['data']['status'] == 'done'
        exported = await client.call('export_plan', {'format': 'markdown'}, 'project_demo', 'export')
        assert exported['ok'] and exported['data']['file_id']
        print(f'真实 MCP stdio 验收通过：{len(client.definitions)} 个工具发现；创建 → 查询 → 完成 → 导出。使用临时库，未更改项目数据。')


if __name__ == '__main__':
    with TemporaryDirectory(prefix='studymate-smoke-') as tmp:
        os.environ['STUDYMATE_DATA_DIR'] = tmp
        asyncio.run(verify())
