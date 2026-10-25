# 课伴 StudyMate

面向课程小组作业的智能体：通过自然语言生成任务草案，确认后保存任务，再根据成员可用时间安排日程。支持多项目、成员视角、多轮任务修改、Markdown导出、网页搜索和天气查询。

技术栈：Python 3.11+、FastAPI、qwen-plus、官方 MCP Python SDK、SQLite、原生 HTML/CSS/JavaScript。前端无需 Node 构建，Markdown库随源码提供。

## 功能

- 任务：创建、查询、修改，支持待开始、进行中、已暂停、已完成及明确转交。
- 日程：读取实际任务和可用时段，检查冲突、截止与工时覆盖，确认草案后保存。
- 记忆：按项目和聊天成员保存历史、当前任务焦点及待确认草案。
- 工具：13个独立 MCP 工具，通过 stdio 执行数据库操作和外部查询。
- 搜索：可选 Tavily 基础搜索，展示实际来源并随对话保存。
- 天气：Open-Meteo 城市查询、当前天气模型估计及今明两日预报，无额外天气密钥。
- 网页：任务看板、日程、成员与项目切换、实际工具进度和计划下载。
- 语音：浏览器识别、可编辑转写、暂停/继续/停止输入与回复朗读。

## 安装与启动

先安装 Python 3.11 或以上版本，在自己的电脑创建虚拟环境；不要复制他人电脑的 `.venv`。

### Windows

解压或克隆源码后，双击 `启动课伴-Windows.bat`。启动器会选择可运行的 Python，创建本机环境并安装锁定依赖。首次运行需要联网下载依赖，终端不能关闭。

也可在 PowerShell 手动运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe scripts\check_env.py
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/check_env.py
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

macOS也可双击 `启动课伴.command`。启动后打开 [本机网页](http://127.0.0.1:8000/)，按 Ctrl+C 停止服务。单进程运行，不启用多 worker。

## 演示模式与真实模型

默认 `AGENT_MODE=demo`，无需模型密钥，仅支持有限规则指令。MCP和数据库操作是真实的，但不能把规则演示当作大模型能力证明。

新建项目后，选择自己的聊天成员，可先试“拆解演示作业”，检查并采用草案，再使用任务卡片和导出按钮。

自由对话需复制 `.env.example` 为 `.env`，在本机配置：

```dotenv
AGENT_MODE=llm
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
MODEL_API_KEY=在本机填写
```

使用百炼业务空间密钥时，Base URL应填写该业务空间提供的OpenAI兼容地址，以 `/compatible-mode/v1` 结尾，地域和密钥需对应。密钥只在服务器端使用，修改后重启。

搜索在同一 `.env` 增加 `TAVILY_API_KEY`；应用默认每月限制100次搜索尝试，失败也计数，短期缓存不重复调用。天气默认启用，可设置 `WEATHER_ENABLED=0` 关闭。天气免费接口适用于非商业教学场景，模型回复仍消耗供应商额度。

## 简单体验

1. 新建“练习·自己的姓名”项目，填写本人和可选组员。
2. 描述作业要求、未来的明确截止时间和分工需求，检查草案后采用。
3. 给成员保存可用时间，生成日程草案，检查后采用。
4. 对自己的任务依次开始、暂停、继续；查看工具记录和任务卡片。
5. 导出计划，下载文件核对任务状态与日程。
6. 在模型模式可问“今天广州天气如何”，或要求搜索公开资料。

日期示例需要替换为使用当天之后的时间。未知城市、负责人或不明确指代应先澄清，不能根据模型一句“成功了”判断操作完成。

## 测试

在安装好依赖的虚拟环境中运行：

```bash
python -m pytest -q
python scripts/smoke_mcp.py
```

自动测试使用独立临时库和受控供应商响应，不消耗真实模型额度；MCP冒烟执行真实协议及数据库操作。2026-10-24的项目记录为138项Python测试、8项Markdown渲染测试和7项语音事件测试通过。

前端语音测试可使用 `node --test tests/test_speech.mjs`。Markdown渲染测试还需要开发环境安装 `jsdom`，通过 `STUDYMATE_TEST_JSDOM` 指定其绝对模块目录；运行网页本身不需要Node或jsdom。

真实供应商验证必须显式加 `--run`，会使用模型/搜索额度：

```bash
python scripts/verify_weather.py --run
python scripts/verify_web_search.py --run
```

## 范围与已知问题

- 成员视角是手动选择，不是个人账号登录或细粒度权限。
- 每个项目独立排程，尚不检查跨项目冲突和任务依赖。
- 天气是模型估计，需核对数据时间；网页搜索摘要可能滞后。
- 语音受浏览器、设备和网络影响；已有实测显示手动停止朗读未正常生效。
- 模型有时会遗漏调用或给出错误参数，需结合工具记录和实际数据核对。
- 数据默认在本机 `data/`，源码不含任何真实任务或密钥；不同电脑不会自动同步数据。
- 可用带密码网关的临时分享启动器，需主机保持运行。GitHub托管源码，不会自动把Python网站部署上线。

## 文档与协作

- [使用说明](docs/使用说明.md)
- [组员快速上手](docs/组员快速上手.md)
- [系统结构](docs/ARCHITECTURE.md)
- [天气接口实现](docs/天气接口实现与报告说明.md)
- [公开提交与组员上传说明](docs/公开提交与组员上传说明.md)
- [课程项目报告](docs/课伴StudyMate_课程作业报告.md)

开发使用了 Codex 辅助，成员按实际的设计、配置联调、优化与测试工作记录贡献。第三方前端库的版本、完整性记录和许可证保留在 `frontend/vendor/`。

`.env`、`.env.share`、数据库、私人迁移包、运行日志及虚拟环境不提交公共仓库。发布源码使用 `python scripts/package_github.py` 的白名单打包流程。
