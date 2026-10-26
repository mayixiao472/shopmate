import pytest
from pydantic import ValidationError

from app.config import Settings


_REQUIRED = {
    "CHAT_MODEL": "deepseek-ai/DeepSeek-V4-Flash",
    "CHAT_BASE_URL": "https://api.siliconflow.cn/v1",
    "CHAT_API_KEY": "sk-test",
    "EMBED_API_KEY": "sk-test",
    "RERANK_API_KEY": "sk-test",
}

# 有代码默认值的变量:测试默认值时先清掉,防 .env 被 dotenv 加载进 os.environ 污染
# (实测 langsmith 插件会 load_dotenv,pytest 全量跑时 EMBED_BASE_URL 等会凭空出现)
_DEFAULTED = ["EMBED_BASE_URL", "RERANK_BASE_URL", "EMBED_MODEL", "RERANK_MODEL", "TOKEN_BUDGET"]


def _fill(monkeypatch, skip=None):
    for k in _DEFAULTED:
        monkeypatch.delenv(k, raising=False)
    for k, v in _REQUIRED.items():
        if k == skip:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


@pytest.mark.parametrize("missing", sorted(_REQUIRED))
def test_账号相关的配置都必填(monkeypatch, missing):
    # 这几项都跟着账号走,给默认值等于替学员猜:猜错不在启动时报错,而是第一次调上游时
    # 抛一个看不懂的 401。缺了就在启动报 Field required,直接指向 .env
    _fill(monkeypatch, skip=missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_嵌入与重排的地址有默认值_只要填_key(monkeypatch):
    # 这两个上游地址是固定的(硅基流动),不该逼学员填
    _fill(monkeypatch)
    s = Settings(_env_file=None)
    assert s.embed_base_url == "https://api.siliconflow.cn/v1"
    assert s.rerank_base_url == "https://api.siliconflow.cn/v1"


def test_settings_defaults(monkeypatch):
    # 隔离 .env 与其他测试可能泄漏进 os.environ 的值,测的是代码默认值
    _fill(monkeypatch)
    s = Settings(_env_file=None)
    assert s.token_budget == 2000
    assert s.rerank_model == "rerank"   # 上游真实名(ch03 起模型已切换,以代码默认值为准)


def test_settings_env_override(monkeypatch):
    _fill(monkeypatch)
    monkeypatch.setenv("TOKEN_BUDGET", "500")
    assert Settings(_env_file=None).token_budget == 500
