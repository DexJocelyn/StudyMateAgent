"""Local console configuration. Standard library only; never tests or prints API keys."""
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'


def read_values(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$', line)
        if match:
            try:
                parts = shlex.split(match[2], comments=True)
                result[match[1]] = ' '.join(parts)
            except ValueError:
                pass
    return result


def validate(values):
    for value in values.values():
        if any(ord(c) < 32 for c in value):
            raise ValueError('配置不能包含换行或控制字符。')
    if values['AGENT_MODE'] != 'llm':
        return
    url = urlsplit(values['MODEL_BASE_URL'])
    if (url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError('Base URL需为不含账号、密码、查询参数的HTTP(S)地址。')
    if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('远程模型请使用HTTPS；本机模型可使用localhost的HTTP地址。')
    if url.path.rstrip('/').endswith('/chat/completions'):
        raise ValueError('Base URL不包含/chat/completions，请去掉这个后缀。')
    if not values['MODEL_NAME'] or not values['MODEL_API_KEY']:
        raise ValueError('真实模型需要模型ID和API Key。')


def save_config(root, values):
    validate(values)
    target = Path(root) / '.env'
    original = target.read_text(encoding='utf-8-sig') if target.exists() else ''
    lines, seen = [], set()
    for line in original.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=', line)
        key = match[1] if match else None
        if key in values:
            if key not in seen:
                lines.append(key + '=' + json.dumps(values[key], ensure_ascii=False))
                seen.add(key)
        else:
            lines.append(line)
    lines.extend(key + '=' + json.dumps(value, ensure_ascii=False) for key, value in values.items() if key not in seen)
    fd, name = tempfile.mkstemp(prefix='.env.config-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(lines) + '\n')
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure(root=ROOT, prompt=input, secret=getpass.getpass):
    target = Path(root) / '.env'
    old = read_values(target.read_text(encoding='utf-8-sig')) if target.exists() else {}
    print('课伴：配置本机模型。密钥输入时不显示；不会发起模型调用。')
    print('1. 使用自己的OpenAI兼容模型（需工具调用与JSON模式）\n2. 先体验规则演示（不能自由聊天或检查作业）')
    choice = prompt('请选择 [1/2，默认1]：').strip() or '1'
    if choice not in ('1', '2'):
        raise ValueError('请选择1或2，配置未修改。')
    values = {'AGENT_MODE': 'llm' if choice == '1' else 'demo'}
    if choice == '1':
        base = old.get('MODEL_BASE_URL') or DEFAULT_BASE
        model = old.get('MODEL_NAME') or 'qwen-plus'
        values['MODEL_BASE_URL'] = (prompt('Base URL（按服务商控制台填写，回车保留默认值）\n['+base+']：').strip() or base).rstrip('/')
        values['MODEL_NAME'] = prompt('模型ID ['+model+']：').strip() or model
        values['MODEL_API_KEY'] = secret('API Key（不显示；已有密钥可回车保留）：').strip() or old.get('MODEL_API_KEY', '')
    search = secret('可选Tavily搜索密钥（不显示；回车跳过或保留已有值）：').strip()
    if search:
        values['TAVILY_API_KEY'] = search
    save_config(root, values)
    print('配置已保存到本机.env，未显示密钥。请启动或重启课伴；保存不代表模型已验证。')


if __name__ == '__main__':
    try:
        configure()
    except (ValueError, OSError):
        raise SystemExit('配置未完成：请检查输入的地址、模型ID及密钥，原有配置未被覆盖。')
    except (EOFError, KeyboardInterrupt):
        raise SystemExit('\n已取消配置。')
