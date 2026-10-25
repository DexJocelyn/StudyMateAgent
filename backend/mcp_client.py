"""A：每个聊天请求持有一条真实 MCP stdio 会话，避免跨异步任务关闭上下文。"""
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from backend.config import ROOT, data_dir
from backend.services.tools import error_result
from backend.services.search import configuration as search_configuration
from backend.services.weather import configuration as weather_configuration


@asynccontextmanager
async def open_tools():
    env = dict(os.environ)
    # 模型密钥仅保留在 HTTP 进程中；工具子进程无需读取。
    env['MODEL_API_KEY'] = ''  # 阻止子进程通过 load_dotenv 再次读取模型密钥。
    env['STUDYMATE_ACCESS_PASSWORD'] = ''  # 工具子进程不需要小组访问凭据。
    env['STUDYMATE_DATA_DIR'] = str(data_dir())
    params = StdioServerParameters(command=sys.executable, args=['-m', 'backend.mcp_server'], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            yield ToolClient(session, (await session.list_tools()).tools)


class ToolClient:
    def __init__(self, session, definitions):
        self.session = session
        self.definitions = {t.name: t for t in definitions}

    def model_tools(self):
        definitions = []
        for tool in self.definitions.values():
            if tool.name == 'get_weather' and not weather_configuration()['available']:
                continue
            if tool.name == 'web_search' and not search_configuration()['available']:
                continue
            schema = json.loads(json.dumps(tool.inputSchema))
            for key in ('project_id', 'mutation_id'):
                schema.get('properties', {}).pop(key, None)
            schema['required'] = [k for k in schema.get('required', []) if k not in ('project_id', 'mutation_id')]
            definitions.append({'type': 'function', 'function': {'name': tool.name, 'description': tool.description,
                                                                'parameters': schema}})
        return definitions

    async def call(self, name, args, project_id, mutation_id):
        if name not in self.definitions:
            return error_result('UNKNOWN_TOOL', '工具不存在')
        if not isinstance(args, dict) or set(args) & {'project_id', 'mutation_id'}:
            return error_result('INVALID_ARGUMENTS', '工具参数格式或执行范围不合法')
        injected = {**args, 'project_id': project_id}
        if 'mutation_id' in self.definitions[name].inputSchema.get('properties', {}):
            injected['mutation_id'] = mutation_id
        result = await self.session.call_tool(name, injected)
        if result.isError:
            return error_result('MCP_TOOL_ERROR', 'MCP 工具拒绝调用：' + ' '.join(c.text for c in result.content if c.type == 'text'))
        value = result.structuredContent
        if value is None:
            value = json.loads(next(c.text for c in result.content if c.type == 'text'))
        if not isinstance(value, dict) or 'ok' not in value:
            return error_result('MCP_INVALID_RESULT', '工具返回格式不正确')
        return value
