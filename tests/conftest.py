"""Keep upstream configuration scenarios independent of the user's settings."""
import shutil
from pathlib import Path

import pytest
import yaml

from wechat_agent import config as cfg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    target = tmp_path / 'config'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'config', target)
    settings_path = target / 'settings.yaml'
    settings = yaml.safe_load(settings_path.read_text('utf-8'))
    settings['wechat'].update(master_chat='浅华', group_chats=['相亲相爱一寝室'], self_nick='qianhua')
    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), 'utf-8')
    path = target / 'personas.yaml'
    personas = yaml.safe_load(path.read_text('utf-8'))
    personas['masks'] = {'女友': {'persona': '测试面具，温和交流。', 'greeting': '你好'}}
    path.write_text(yaml.safe_dump(personas, allow_unicode=True), 'utf-8')
    monkeypatch.setattr(cfg, 'CONFIG_DIR', target)
    monkeypatch.setattr(cfg, 'BASE_DIR', tmp_path)
