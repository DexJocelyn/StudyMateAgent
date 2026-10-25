"""云端小组访问门禁；与成员视角分开，不冒充独立成员账号。"""
import base64
import binascii
import os
import secrets
from dataclasses import dataclass, field
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class AccessConfig:
    enabled: bool
    username: str
    password: str = field(repr=False)

    def accepts(self, authorization):
        if not self.enabled:
            return True
        try:
            scheme, token = (authorization or '').split(' ', 1)
            if scheme.lower() != 'basic' or len(token) > 4096:
                return False
            username, password = base64.b64decode(token, validate=True).decode('utf-8').split(':', 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        # 不用短路比较，避免用户名比较结果影响密码比较是否执行。
        name_ok = secrets.compare_digest(username.encode(), self.username.encode())
        password_ok = secrets.compare_digest(password.encode(), self.password.encode())
        return name_ok & password_ok


def load_access_config():
    public = os.getenv('STUDYMATE_PUBLIC', '0')
    if public not in ('0', '1'):
        raise RuntimeError('STUDYMATE_PUBLIC 必须是 0 或 1。')
    username = os.getenv('STUDYMATE_ACCESS_USER', 'group').strip()
    password = os.getenv('STUDYMATE_ACCESS_PASSWORD', '')
    enabled = public == '1' or bool(password)
    if enabled and (not 1 <= len(username) <= 64 or ':' in username or any(ord(c) < 32 for c in username)):
        raise RuntimeError('请设置有效的小组访问用户名。')
    if enabled and (len(password) < 16 or password.strip() != password or any(ord(c) < 32 for c in password)
                    or password in ('change-this-password', 'replace-with-random-password')):
        raise RuntimeError('公网运行必须设置至少16位的小组访问密码，请在云端配置，不要写入代码。')
    return AccessConfig(enabled, username, password)


def unauthorized_response():
    return JSONResponse(status_code=401,
        content={'ok': False, 'error': {'code': 'AUTH_REQUIRED', 'message': '请使用组长提供的小组访问账号和密码登录。'}},
        headers={'WWW-Authenticate': 'Basic realm="StudyMate", charset="UTF-8"', 'Cache-Control': 'no-store'})
