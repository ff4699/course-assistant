"""User-provided EAMS connection addresses; no institution defaults or secrets."""
import json
from pathlib import Path
from urllib.parse import urlparse
from pydantic import BaseModel, Field, model_validator


class ConnectionSettings(BaseModel):
    base_url: str = Field(default='', max_length=2048)
    login_url: str = Field(default='', max_length=2048)
    portal_url: str = Field(default='', max_length=2048)

    @model_validator(mode='after')
    def check_urls(self):
        values = [self.base_url, self.login_url, self.portal_url]
        if not any(values):
            return self
        if not all(values):
            raise ValueError('请完整填写教务地址、认证登录地址和门户地址')
        for value in values:
            parsed = urlparse(value)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.fragment or any(c.isspace() for c in value)):
                raise ValueError('连接地址必须是完整HTTPS地址，不能包含账号密码、空格或片段')
        base = urlparse(self.base_url)
        if base.path not in {'', '/'} or base.query:
            raise ValueError('教务地址只填写网站根地址，不包含路径或查询参数')
        self.base_url = self.base_url.rstrip('/')
        return self

    @property
    def hosts(self):
        return {urlparse(value).hostname for value in [self.base_url,self.login_url,self.portal_url] if value}


def load_connection(state_dir):
    try:
        return ConnectionSettings.model_validate_json((Path(state_dir)/'school_connection.json').read_text('utf-8'))
    except (OSError, ValueError):
        return ConnectionSettings()


def save_connection(state_dir, settings):
    path = Path(state_dir)/'school_connection.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(settings.model_dump_json(indent=2),'utf-8')
    temporary.replace(path)
