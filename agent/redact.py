"""基于正则表达式的密钥脱敏,用于日志和工具输出。

在 API 密钥、令牌和凭据到达日志文件、详细输出或 gateway
日志之前,应用模式匹配进行掩码。

短令牌(< 18 字符)完全掩码。较长令牌保留前 6 位和后 4 位
字符以便调试。
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# 导入时快照,因此运行时环境变化(例如 LLM 生成的
# `export KCLAW_REDACT_SECRETS=false`)无法在会话中间禁用脱敏。
_REDACT_ENABLED = os.getenv("KCLAW_REDACT_SECRETS", "").lower() not in ("0", "false", "no", "off")

# 已知的 API 密钥前缀 — 匹配前缀 + 连续的令牌字符
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",           # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth access token
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub user-to-server token
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub server-to-server token
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe secret key (live)
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe secret key (test)
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",          # SendGrid API key
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace token
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",            # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",           # AgentMail API key
    r"sk_[A-Za-z0-9_]{10,}",            # ElevenLabs TTS key (sk_ underscore, not sk- dash)
    r"tvly-[A-Za-z0-9]{10,}",           # Tavily search API key
    r"exa_[A-Za-z0-9]{10,}",            # Exa search API key
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API key
    r"syt_[A-Za-z0-9]{10,}",            # Matrix access token
    r"retaindb_[A-Za-z0-9]{10,}",       # RetainDB API key
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API key
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API key
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API key
]

# 环境变量赋值模式: KEY=value,其中 KEY 包含类似密钥的名称
_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
)

# JSON 字段模式: "apiKey": "value", "token": "value" 等
_JSON_KEY_NAMES = r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material)"
_JSON_FIELD_RE = re.compile(
    rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# 授权头
_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*Bearer\s+)(\S+)",
    re.IGNORECASE,
)

# Telegram 机器人令牌: bot<数字>:<令牌> 或 <数字>:<令牌>,
# 其中令牌部分限制为 [-A-Za-z0-9_] 且长度 >= 30
_TELEGRAM_RE = re.compile(
    r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})",
)

# 私钥块: -----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# 数据库连接字符串: protocol://user:PASSWORD@host
# 捕获 postgres、mysql、mongodb、redis、amqp URL 并脱敏密码
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)

# E.164 电话号码: +<国家><号码>, 7-15 位数字
# 负向前瞻防止匹配十六进制字符串或标识符
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# 将已知前缀模式编译为一个多选
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


def _mask_token(token: str) -> str:
    """掩码令牌,为长令牌保留前缀。"""
    if len(token) < 18:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def redact_sensitive_text(text: str) -> str:
    """将脱敏模式应用于文本块。

    可安全调用任意字符串 — 不匹配的文本原样通过。
    当 config.yaml 中 security.redact_secrets 为 false 时禁用。
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not _REDACT_ENABLED:
        return text

    # 已知前缀(sk-、ghp_ 等)
    text = _PREFIX_RE.sub(lambda m: _mask_token(m.group(1)), text)

    # 环境变量赋值: OPENAI_API_KEY=sk-abc...
    def _redact_env(m):
        name, quote, value = m.group(1), m.group(2), m.group(3)
        return f"{name}={quote}{_mask_token(value)}{quote}"
    text = _ENV_ASSIGN_RE.sub(_redact_env, text)

    # JSON 字段: "apiKey": "value"
    def _redact_json(m):
        key, value = m.group(1), m.group(2)
        return f'{key}: "{_mask_token(value)}"'
    text = _JSON_FIELD_RE.sub(_redact_json, text)

    # 授权头
    text = _AUTH_HEADER_RE.sub(
        lambda m: m.group(1) + _mask_token(m.group(2)),
        text,
    )

    # Telegram 机器人令牌
    def _redact_telegram(m):
        prefix = m.group(1) or ""
        digits = m.group(2)
        return f"{prefix}{digits}:***"
    text = _TELEGRAM_RE.sub(_redact_telegram, text)

    # 私钥块
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    # 数据库连接字符串密码
    text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

    # E.164 电话号码 (Signal, WhatsApp)
    def _redact_phone(m):
        phone = m.group(1)
        if len(phone) <= 8:
            return phone[:2] + "****" + phone[-2:]
        return phone[:4] + "****" + phone[-4:]
    text = _SIGNAL_PHONE_RE.sub(_redact_phone, text)

    return text


class RedactingFormatter(logging.Formatter):
    """日志格式化器,从所有日志消息中脱敏密钥。"""

    def __init__(self, fmt=None, datefmt=None, style='%', **kwargs):
        super().__init__(fmt, datefmt, style, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_text(original)
