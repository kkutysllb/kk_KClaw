"""
网关配置管理。

处理以下配置加载和验证：
- 已连接的平台（Telegram、Discord、WhatsApp）
- 每个平台的主页渠道
- 会话重置策略
- 传递偏好设置
"""

import logging
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from kclaw_cli.config import get_kclaw_home
from utils import is_truthy_value

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """强制转换布尔风格配置值，保留调用者提供的默认值。"""
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        return default
    return is_truthy_value(value, default=default)


def _normalize_unauthorized_dm_behavior(value: Any, default: str = "pair") -> str:
    """将未授权 DM 行为规范化为支持的值。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pair", "ignore"}:
            return normalized
    return default


class Platform(Enum):
    """支持的消息平台。"""
    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    SLACK = "slack"
    SIGNAL = "signal"
    MATTERMOST = "mattermost"
    MATRIX = "matrix"
    HOMEASSISTANT = "homeassistant"
    EMAIL = "email"
    SMS = "sms"
    DINGTALK = "dingtalk"
    API_SERVER = "api_server"
    WEBHOOK = "webhook"
    FEISHU = "feishu"
    WECOM = "wecom"
    BLUEBUBBLES = "bluebubbles"


@dataclass
class HomeChannel:
    """
    平台的消息默认目的地。
    
    当 cron 作业指定 deliver="telegram" 但没有特定聊天 ID 时，
    消息会发送到此主页渠道。
    """
    platform: Platform
    chat_id: str
    name: str  # 用于显示的人类可读名称
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "name": self.name,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HomeChannel":
        return cls(
            platform=Platform(data["platform"]),
            chat_id=str(data["chat_id"]),
            name=data.get("name", "Home"),
        )


@dataclass
class SessionResetPolicy:
    """
    控制会话何时重置（失去上下文）。
    
    模式：
    - "daily": 每天特定小时重置
    - "idle": 空闲 N 分钟后重置
    - "both": 无论哪个先触发（每日边界或空闲超时）
    - "none": 从不自动重置（仅通过压缩管理上下文）
    """
    mode: str = "both"  # "daily", "idle", "both", 或 "none"
    at_hour: int = 4  # 每日重置的小时（0-23，本地时间）
    idle_minutes: int = 1440  # 重置前的空闲分钟数（24小时）
    notify: bool = True  # 自动重置发生时是否向用户发送通知
    notify_exclude_platforms: tuple = ("api_server", "webhook")  # 不发送重置通知的平台
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "at_hour": self.at_hour,
            "idle_minutes": self.idle_minutes,
            "notify": self.notify,
            "notify_exclude_platforms": list(self.notify_exclude_platforms),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionResetPolicy":
        # 同时处理缺失的键和显式的 null 值（YAML null → None）
        mode = data.get("mode")
        at_hour = data.get("at_hour")
        idle_minutes = data.get("idle_minutes")
        notify = data.get("notify")
        exclude = data.get("notify_exclude_platforms")
        return cls(
            mode=mode if mode is not None else "both",
            at_hour=at_hour if at_hour is not None else 4,
            idle_minutes=idle_minutes if idle_minutes is not None else 1440,
            notify=notify if notify is not None else True,
            notify_exclude_platforms=tuple(exclude) if exclude is not None else ("api_server", "webhook"),
        )


@dataclass
class PlatformConfig:
    """单个消息平台的配置。"""
    enabled: bool = False
    token: Optional[str] = None  # 机器人令牌（Telegram、Discord）
    api_key: Optional[str] = None  # 如果与令牌不同则使用 API 密钥
    home_channel: Optional[HomeChannel] = None
    
    # 回复线程模式（Telegram/Slack）
    # - "off": 从不将回复线程化到原始消息
    # - "first": 仅第一个分块线程化到用户消息（默认）
    # - "all": 多部分回复中的所有分块都线程化到用户消息
    reply_to_mode: str = "first"
    
    # 平台特定设置
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "enabled": self.enabled,
            "extra": self.extra,
            "reply_to_mode": self.reply_to_mode,
        }
        if self.token:
            result["token"] = self.token
        if self.api_key:
            result["api_key"] = self.api_key
        if self.home_channel:
            result["home_channel"] = self.home_channel.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformConfig":
        home_channel = None
        if "home_channel" in data:
            home_channel = HomeChannel.from_dict(data["home_channel"])
        
        return cls(
            enabled=data.get("enabled", False),
            token=data.get("token"),
            api_key=data.get("api_key"),
            home_channel=home_channel,
            reply_to_mode=data.get("reply_to_mode", "first"),
            extra=data.get("extra", {}),
        )


@dataclass
class StreamingConfig:
    """实时令牌流式传输到消息平台的配置。"""
    enabled: bool = False
    transport: str = "edit"       # "edit" (渐进式 editMessageText) 或 "off"
    edit_interval: float = 0.3    # 消息编辑之间的秒数
    buffer_threshold: int = 40    # 强制编辑前的字符数
    cursor: str = " ▉"           # 流式传输期间显示的光标

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "transport": self.transport,
            "edit_interval": self.edit_interval,
            "buffer_threshold": self.buffer_threshold,
            "cursor": self.cursor,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamingConfig":
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            transport=data.get("transport", "edit"),
            edit_interval=float(data.get("edit_interval", 0.3)),
            buffer_threshold=int(data.get("buffer_threshold", 40)),
            cursor=data.get("cursor", " ▉"),
        )


@dataclass
class GatewayConfig:
    """
    主网关配置。
    
    管理所有平台连接、会话策略和传递设置。
    """
    # 平台配置
    platforms: Dict[Platform, PlatformConfig] = field(default_factory=dict)
    
    # 按类型分类的会话重置策略
    default_reset_policy: SessionResetPolicy = field(default_factory=SessionResetPolicy)
    reset_by_type: Dict[str, SessionResetPolicy] = field(default_factory=dict)
    reset_by_platform: Dict[Platform, SessionResetPolicy] = field(default_factory=dict)
    
    # 重置触发命令
    reset_triggers: List[str] = field(default_factory=lambda: ["/new", "/reset"])

    # 用户定义的快速命令（绕过代理循环的斜杠命令）
    quick_commands: Dict[str, Any] = field(default_factory=dict)
    
    # 存储路径
    sessions_dir: Path = field(default_factory=lambda: get_kclaw_home() / "sessions")
    
    # 传递设置
    always_log_local: bool = True  # 始终将 cron 输出保存到本地文件

    # STT 设置
    stt_enabled: bool = True  # 是否自动转录入站语音消息

    # 共享聊天中的会话隔离
    group_sessions_per_user: bool = True  # 当用户 ID 可用时，按参与者隔离群组/频道会话
    thread_sessions_per_user: bool = False  # 为 False（默认）时，线程在所有参与者之间共享

    # 未授权 DM 策略
    unauthorized_dm_behavior: str = "pair"  # "pair" 或 "ignore"

    # 流式传输配置
    streaming: StreamingConfig = field(default_factory=StreamingConfig)

    def get_connected_platforms(self) -> List[Platform]:
        """返回已启用且已配置的平台列表。"""
        connected = []
        for platform, config in self.platforms.items():
            if not config.enabled:
                continue
            # 使用令牌/API 密钥认证的平台
            if config.token or config.api_key:
                connected.append(platform)
            # WhatsApp 使用仅启用标志（桥处理认证）
            elif platform == Platform.WHATSAPP:
                connected.append(platform)
            # Signal 使用 extra 字典进行配置（http_url + account）
            elif platform == Platform.SIGNAL and config.extra.get("http_url"):
                connected.append(platform)
            # Email 使用 extra 字典进行配置（address + imap_host + smtp_host）
            elif platform == Platform.EMAIL and config.extra.get("address"):
                connected.append(platform)
            # SMS 使用 api_key（Twilio 认证令牌）——SID 通过环境变量检查
            elif platform == Platform.SMS and os.getenv("TWILIO_ACCOUNT_SID"):
                connected.append(platform)
            # API Server 使用仅启用标志（无需令牌）
            elif platform == Platform.API_SERVER:
                connected.append(platform)
            # Webhook 使用仅启用标志（密钥通过路由配置）
            elif platform == Platform.WEBHOOK:
                connected.append(platform)
            # Feishu 使用 extra 字典进行应用凭证配置
            elif platform == Platform.FEISHU and config.extra.get("app_id"):
                connected.append(platform)
            # WeCom 使用 extra 字典进行机器人凭证配置
            elif platform == Platform.WECOM and config.extra.get("bot_id"):
                connected.append(platform)
            # BlueBubbles 使用 extra 字典进行本地服务器配置
            elif platform == Platform.BLUEBUBBLES and config.extra.get("server_url") and config.extra.get("password"):
                connected.append(platform)
        return connected
    
    def get_home_channel(self, platform: Platform) -> Optional[HomeChannel]:
        """获取平台的主页渠道。"""
        config = self.platforms.get(platform)
        if config:
            return config.home_channel
        return None
    
    def get_reset_policy(
        self, 
        platform: Optional[Platform] = None,
        session_type: Optional[str] = None
    ) -> SessionResetPolicy:
        """
        获取会话的适当重置策略。
        
        优先级：平台覆盖 > 类型覆盖 > 默认
        """
        # 平台特定覆盖优先
        if platform and platform in self.reset_by_platform:
            return self.reset_by_platform[platform]
        
        # 类型特定覆盖（dm, group, thread）
        if session_type and session_type in self.reset_by_type:
            return self.reset_by_type[session_type]
        
        return self.default_reset_policy
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platforms": {
                p.value: c.to_dict() for p, c in self.platforms.items()
            },
            "default_reset_policy": self.default_reset_policy.to_dict(),
            "reset_by_type": {
                k: v.to_dict() for k, v in self.reset_by_type.items()
            },
            "reset_by_platform": {
                p.value: v.to_dict() for p, v in self.reset_by_platform.items()
            },
            "reset_triggers": self.reset_triggers,
            "quick_commands": self.quick_commands,
            "sessions_dir": str(self.sessions_dir),
            "always_log_local": self.always_log_local,
            "stt_enabled": self.stt_enabled,
            "group_sessions_per_user": self.group_sessions_per_user,
            "thread_sessions_per_user": self.thread_sessions_per_user,
            "unauthorized_dm_behavior": self.unauthorized_dm_behavior,
            "streaming": self.streaming.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GatewayConfig":
        platforms = {}
        for platform_name, platform_data in data.get("platforms", {}).items():
            try:
                platform = Platform(platform_name)
                platforms[platform] = PlatformConfig.from_dict(platform_data)
            except ValueError:
                pass  # 跳过未知平台
        
        reset_by_type = {}
        for type_name, policy_data in data.get("reset_by_type", {}).items():
            reset_by_type[type_name] = SessionResetPolicy.from_dict(policy_data)  # 类型特定重置策略
        
        reset_by_platform = {}
        for platform_name, policy_data in data.get("reset_by_platform", {}).items():
            try:
                platform = Platform(platform_name)
                reset_by_platform[platform] = SessionResetPolicy.from_dict(policy_data)  # 平台特定重置策略
            except ValueError:
                pass
        
        default_policy = SessionResetPolicy()  # 默认重置策略
        if "default_reset_policy" in data:
            default_policy = SessionResetPolicy.from_dict(data["default_reset_policy"])  # 默认重置策略 
        
        sessions_dir = get_kclaw_home() / "sessions"
        if "sessions_dir" in data:
            sessions_dir = Path(data["sessions_dir"])
        
        quick_commands = data.get("quick_commands", {})
        if not isinstance(quick_commands, dict):
            quick_commands = {}

        stt_enabled = data.get("stt_enabled")
        if stt_enabled is None:
            stt_enabled = data.get("stt", {}).get("enabled") if isinstance(data.get("stt"), dict) else None

        group_sessions_per_user = data.get("group_sessions_per_user")
        thread_sessions_per_user = data.get("thread_sessions_per_user")
        unauthorized_dm_behavior = _normalize_unauthorized_dm_behavior(
            data.get("unauthorized_dm_behavior"),
            "pair",
        )

        return cls(
            platforms=platforms,
            default_reset_policy=default_policy,
            reset_by_type=reset_by_type,
            reset_by_platform=reset_by_platform,
            reset_triggers=data.get("reset_triggers", ["/new", "/reset"]),
            quick_commands=quick_commands,
            sessions_dir=sessions_dir,
            always_log_local=data.get("always_log_local", True),
            stt_enabled=_coerce_bool(stt_enabled, True),
            group_sessions_per_user=_coerce_bool(group_sessions_per_user, True),
            thread_sessions_per_user=_coerce_bool(thread_sessions_per_user, False),
            unauthorized_dm_behavior=unauthorized_dm_behavior,
            streaming=StreamingConfig.from_dict(data.get("streaming", {})),
        )

    def get_unauthorized_dm_behavior(self, platform: Optional[Platform] = None) -> str:
        """返回平台的有效未授权-DM 行为。"""
        if platform:
            platform_cfg = self.platforms.get(platform)
            if platform_cfg and "unauthorized_dm_behavior" in platform_cfg.extra:
                return _normalize_unauthorized_dm_behavior(
                    platform_cfg.extra.get("unauthorized_dm_behavior"),
                    self.unauthorized_dm_behavior,
                )
        return self.unauthorized_dm_behavior


def load_gateway_config() -> GatewayConfig:
    """
    从多个来源加载网关配置。

    优先级（从高到低）：
    1. 环境变量
    2. ~/.kclaw/config.yaml（主要面向用户的配置）
    3. ~/.kclaw/gateway.json（传统 — 在 config.yaml 下提供默认值）
    4. 内置默认值
    """
    _home = get_kclaw_home()
    gw_data: dict = {}

    # 传统回退：gateway.json 提供基础层。
    # 当两者指定相同设置时，config.yaml 键总是优先。
    gateway_json_path = _home / "gateway.json"
    if gateway_json_path.exists():
        try:
            with open(gateway_json_path, "r", encoding="utf-8") as f:
                gw_data = json.load(f) or {}
            logger.info(
                "已加载传统 %s — 考虑将设置迁移到 config.yaml",
                gateway_json_path,
            )
        except Exception as e:
            logger.warning("加载 %s 失败: %s", gateway_json_path, e)

    # 主要来源：config.yaml
    try:
        import yaml
        config_yaml_path = _home / "config.yaml"
        if config_yaml_path.exists():
            with open(config_yaml_path, encoding="utf-8") as f:
                yaml_cfg = yaml.safe_load(f) or {}

            # 将 config.yaml 键 → GatewayConfig.from_dict() 模式映射
            # 每个键覆盖 gateway.json 可能设置的任何内容。
            sr = yaml_cfg.get("session_reset")
            if sr and isinstance(sr, dict):
                gw_data["default_reset_policy"] = sr  # 会话重置策略

            qc = yaml_cfg.get("quick_commands")
            if qc is not None:
                if isinstance(qc, dict):
                    gw_data["quick_commands"] = qc  # 快速命令
                else:
                    logger.warning(
                        "Ignoring invalid quick_commands in config.yaml "
                        "(expected mapping, got %s)",
                        type(qc).__name__,
                    )

            stt_cfg = yaml_cfg.get("stt")
            if isinstance(stt_cfg, dict):
                gw_data["stt"] = stt_cfg  # STT 配置

            if "group_sessions_per_user" in yaml_cfg:
                gw_data["group_sessions_per_user"] = yaml_cfg["group_sessions_per_user"]  # 每用户群会话数

            if "thread_sessions_per_user" in yaml_cfg:
                gw_data["thread_sessions_per_user"] = yaml_cfg["thread_sessions_per_user"]  # 每用户线程会话数

            streaming_cfg = yaml_cfg.get("streaming")
            if isinstance(streaming_cfg, dict):
                gw_data["streaming"] = streaming_cfg  # 流式配置

            if "reset_triggers" in yaml_cfg:
                gw_data["reset_triggers"] = yaml_cfg["reset_triggers"]  # 重置触发电脑

            if "always_log_local" in yaml_cfg:
                gw_data["always_log_local"] = yaml_cfg["always_log_local"]  # 始终本地日志

            if "unauthorized_dm_behavior" in yaml_cfg:
                gw_data["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                    yaml_cfg.get("unauthorized_dm_behavior"),
                    "pair",  # 默认行为 
                    yaml_cfg.get("unauthorized_dm_behavior"),
                    "pair",
                )

            # 将 config.yaml 中的 platforms 部分合并到 gw_data 中，以便
            # 嵌套键（如 platforms.webhook.extra.routes）会被加载。
            yaml_platforms = yaml_cfg.get("platforms")
            platforms_data = gw_data.setdefault("platforms", {})
            if not isinstance(platforms_data, dict):
                platforms_data = {}
                gw_data["platforms"] = platforms_data
            if isinstance(yaml_platforms, dict):
                for plat_name, plat_block in yaml_platforms.items():
                    if not isinstance(plat_block, dict):
                        continue
                    existing = platforms_data.get(plat_name, {})
                    if not isinstance(existing, dict):
                        existing = {}
                    # 深合并 extra 字典，以便 gateway.json 默认值 survive
                    merged_extra = {**existing.get("extra", {}), **plat_block.get("extra", {})}
                    merged = {**existing, **plat_block}
                    if merged_extra:
                        merged["extra"] = merged_extra
                    platforms_data[plat_name] = merged
                gw_data["platforms"] = platforms_data
            for plat in Platform:
                if plat == Platform.LOCAL:
                    continue
                platform_cfg = yaml_cfg.get(plat.value)
                if not isinstance(platform_cfg, dict):
                    continue
                # 收集此平台部分中的桥接键
                bridged = {}  # 桥接的键值对
                if "unauthorized_dm_behavior" in platform_cfg:
                    bridged["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(  # 未授权-DM 行为
                        platform_cfg.get("unauthorized_dm_behavior"),
                        gw_data.get("unauthorized_dm_behavior", "pair"),
                    )
                if "reply_prefix" in platform_cfg:
                    bridged["reply_prefix"] = platform_cfg["reply_prefix"]  # 回复前缀
                if "require_mention" in platform_cfg:
                    bridged["require_mention"] = platform_cfg["require_mention"]  # 需要提及
                if "mention_patterns" in platform_cfg:
                    bridged["mention_patterns"] = platform_cfg["mention_patterns"]  # 著名模式
                if not bridged:
                    continue
                plat_data = platforms_data.setdefault(plat.value, {})
                if not isinstance(plat_data, dict):
                    plat_data = {}
                    platforms_data[plat.value] = plat_data
                extra = plat_data.setdefault("extra", {})
                if not isinstance(extra, dict):
                    extra = {}
                    plat_data["extra"] = extra
                extra.update(bridged)

            # Discord 设置 → 环境变量（环境变量优先）
            discord_cfg = yaml_cfg.get("discord", {})
            if isinstance(discord_cfg, dict):
                if "require_mention" in discord_cfg and not os.getenv("DISCORD_REQUIRE_MENTION"):
                    os.environ["DISCORD_REQUIRE_MENTION"] = str(discord_cfg["require_mention"]).lower()
                frc = discord_cfg.get("free_response_channels")
                if frc is not None and not os.getenv("DISCORD_FREE_RESPONSE_CHANNELS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["DISCORD_FREE_RESPONSE_CHANNELS"] = str(frc)
                if "auto_thread" in discord_cfg and not os.getenv("DISCORD_AUTO_THREAD"):
                    os.environ["DISCORD_AUTO_THREAD"] = str(discord_cfg["auto_thread"]).lower()
                if "reactions" in discord_cfg and not os.getenv("DISCORD_REACTIONS"):
                    os.environ["DISCORD_REACTIONS"] = str(discord_cfg["reactions"]).lower()
                # ignored_channels: channels where bot never responds (even when mentioned)  # 忽略的频道
                ic = discord_cfg.get("ignored_channels")
                if ic is not None and not os.getenv("DISCORD_IGNORED_CHANNELS"):
                    if isinstance(ic, list):
                        ic = ",".join(str(v) for v in ic)
                    os.environ["DISCORD_IGNORED_CHANNELS"] = str(ic)
                # no_thread_channels: channels where bot responds directly without creating thread  # 不创建线程的频道
                ntc = discord_cfg.get("no_thread_channels")
                if ntc is not None and not os.getenv("DISCORD_NO_THREAD_CHANNELS"):
                    if isinstance(ntc, list):
                        ntc = ",".join(str(v) for v in ntc)
                    os.environ["DISCORD_NO_THREAD_CHANNELS"] = str(ntc)

            # Telegram 设置 → 环境变量（环境变量优先）
            telegram_cfg = yaml_cfg.get("telegram", {})
            if isinstance(telegram_cfg, dict):
                if "require_mention" in telegram_cfg and not os.getenv("TELEGRAM_REQUIRE_MENTION"):
                    os.environ["TELEGRAM_REQUIRE_MENTION"] = str(telegram_cfg["require_mention"]).lower()
                if "mention_patterns" in telegram_cfg and not os.getenv("TELEGRAM_MENTION_PATTERNS"):
                    import json as _json
                    os.environ["TELEGRAM_MENTION_PATTERNS"] = _json.dumps(telegram_cfg["mention_patterns"])
                frc = telegram_cfg.get("free_response_chats")
                if frc is not None and not os.getenv("TELEGRAM_FREE_RESPONSE_CHATS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["TELEGRAM_FREE_RESPONSE_CHATS"] = str(frc)
                if "reactions" in telegram_cfg and not os.getenv("TELEGRAM_REACTIONS"):
                    os.environ["TELEGRAM_REACTIONS"] = str(telegram_cfg["reactions"]).lower()

            whatsapp_cfg = yaml_cfg.get("whatsapp", {})
            if isinstance(whatsapp_cfg, dict):
                if "require_mention" in whatsapp_cfg and not os.getenv("WHATSAPP_REQUIRE_MENTION"):
                    os.environ["WHATSAPP_REQUIRE_MENTION"] = str(whatsapp_cfg["require_mention"]).lower()
                if "mention_patterns" in whatsapp_cfg and not os.getenv("WHATSAPP_MENTION_PATTERNS"):
                    os.environ["WHATSAPP_MENTION_PATTERNS"] = json.dumps(whatsapp_cfg["mention_patterns"])
                frc = whatsapp_cfg.get("free_response_chats")
                if frc is not None and not os.getenv("WHATSAPP_FREE_RESPONSE_CHATS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["WHATSAPP_FREE_RESPONSE_CHATS"] = str(frc)

            # Matrix 设置 → 环境变量（环境变量优先）
            matrix_cfg = yaml_cfg.get("matrix", {})
            if isinstance(matrix_cfg, dict):
                if "require_mention" in matrix_cfg and not os.getenv("MATRIX_REQUIRE_MENTION"):
                    os.environ["MATRIX_REQUIRE_MENTION"] = str(matrix_cfg["require_mention"]).lower()
                frc = matrix_cfg.get("free_response_rooms")
                if frc is not None and not os.getenv("MATRIX_FREE_RESPONSE_ROOMS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["MATRIX_FREE_RESPONSE_ROOMS"] = str(frc)
                if "auto_thread" in matrix_cfg and not os.getenv("MATRIX_AUTO_THREAD"):
                    os.environ["MATRIX_AUTO_THREAD"] = str(matrix_cfg["auto_thread"]).lower()

    except Exception as e:
        logger.warning(
            "Failed to process config.yaml — falling back to .env / gateway.json values. "
            "Check %s for syntax errors. Error: %s",
            _home / "config.yaml",
            e,
        )

    config = GatewayConfig.from_dict(gw_data)

    # 用环境变量覆盖
    _apply_env_overrides(config)
    
    # --- 验证加载的值 ---
    policy = config.default_reset_policy
    # 验证 at_hour 值
    if not (0 <= policy.at_hour <= 23):
        logger.warning(
            "Invalid at_hour=%s (must be 0-23). Using default 4.", policy.at_hour
        )
        policy.at_hour = 4

    # 验证 idle_minutes 值
    if policy.idle_minutes <= 0:
        logger.warning(
            "Invalid idle_minutes=%s (must be positive). Using default 1440.",
            policy.idle_minutes,
        )
        policy.idle_minutes = 1440

    # 警告关于空的机器人令牌 —— 加载空字符串的平台
    # won't connect and the cause can be confusing without a log line.  # 不会连接，原因可能令人困惑，没有日志行。  
    _token_env_names = {
        Platform.TELEGRAM: "TELEGRAM_BOT_TOKEN",
        Platform.DISCORD: "DISCORD_BOT_TOKEN",
        Platform.SLACK: "SLACK_BOT_TOKEN",
        Platform.MATTERMOST: "MATTERMOST_TOKEN",
        Platform.MATRIX: "MATRIX_ACCESS_TOKEN",
    }
    for platform, pconfig in config.platforms.items():
        if not pconfig.enabled:
            continue
        env_name = _token_env_names.get(platform)
        if env_name and pconfig.token is not None and not pconfig.token.strip():
            logger.warning(
                "%s is enabled but %s is empty. "
                "The adapter will likely fail to connect.",
                platform.value, env_name,
            )

    return config


def _apply_env_overrides(config: GatewayConfig) -> None:
    """应用环境变量覆盖到配置。"""
    
    # Telegram 机器人令牌
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].enabled = True
        config.platforms[Platform.TELEGRAM].token = telegram_token
    
    # Telegram 回复线程模式（off/first/all）
    telegram_reply_mode = os.getenv("TELEGRAM_REPLY_TO_MODE", "").lower()
    if telegram_reply_mode in ("off", "first", "all"):
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].reply_to_mode = telegram_reply_mode
    
    # Telegram 回退 IP 地址
    if telegram_fallback_ips:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].extra["fallback_ips"] = [
            ip.strip() for ip in telegram_fallback_ips.split(",") if ip.strip()
        ]

    telegram_home = os.getenv("TELEGRAM_HOME_CHANNEL")
    if telegram_home and Platform.TELEGRAM in config.platforms:
        config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
            platform=Platform.TELEGRAM,
            chat_id=telegram_home,
            name=os.getenv("TELEGRAM_HOME_CHANNEL_NAME", "Home"),
        )
    
    # Discord 机器人令牌
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    if discord_token:
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].enabled = True
        config.platforms[Platform.DISCORD].token = discord_token
    
    discord_home = os.getenv("DISCORD_HOME_CHANNEL")
    if discord_home and Platform.DISCORD in config.platforms:
        config.platforms[Platform.DISCORD].home_channel = HomeChannel(
            platform=Platform.DISCORD,
            chat_id=discord_home,
            name=os.getenv("DISCORD_HOME_CHANNEL_NAME", "Home"),
        )
    
    # Discord 回复线程模式（off/first/all）
    discord_reply_mode = os.getenv("DISCORD_REPLY_TO_MODE", "").lower()
    if discord_reply_mode in ("off", "first", "all"):
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].reply_to_mode = discord_reply_mode
    
    # WhatsApp (typically uses different auth mechanism)  # 通常使用不同的认证机制
    whatsapp_enabled = os.getenv("WHATSAPP_ENABLED", "").lower() in ("true", "1", "yes")
    if whatsapp_enabled:
        if Platform.WHATSAPP not in config.platforms:
            config.platforms[Platform.WHATSAPP] = PlatformConfig()
        config.platforms[Platform.WHATSAPP].enabled = True
    
    # Slack 机器人令牌
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if slack_token:
        if Platform.SLACK not in config.platforms:
            config.platforms[Platform.SLACK] = PlatformConfig()
        config.platforms[Platform.SLACK].enabled = True
        config.platforms[Platform.SLACK].token = slack_token
    slack_home = os.getenv("SLACK_HOME_CHANNEL")
    if slack_home and Platform.SLACK in config.platforms:
        config.platforms[Platform.SLACK].home_channel = HomeChannel(
            platform=Platform.SLACK,
            chat_id=slack_home,
            name=os.getenv("SLACK_HOME_CHANNEL_NAME", ""),
        )
    
    # Signal 机器人令牌
    signal_url = os.getenv("SIGNAL_HTTP_URL")
    signal_account = os.getenv("SIGNAL_ACCOUNT")
    if signal_url and signal_account:
        if Platform.SIGNAL not in config.platforms:
            config.platforms[Platform.SIGNAL] = PlatformConfig()
        config.platforms[Platform.SIGNAL].enabled = True
        config.platforms[Platform.SIGNAL].extra.update({
            "http_url": signal_url,
            "account": signal_account,
            "ignore_stories": os.getenv("SIGNAL_IGNORE_STORIES", "true").lower() in ("true", "1", "yes"),
        })
    signal_home = os.getenv("SIGNAL_HOME_CHANNEL")
    if signal_home and Platform.SIGNAL in config.platforms:
        config.platforms[Platform.SIGNAL].home_channel = HomeChannel(
            platform=Platform.SIGNAL,
            chat_id=signal_home,
            name=os.getenv("SIGNAL_HOME_CHANNEL_NAME", "Home"),
        )

    # Mattermost 机器人令牌
    mattermost_token = os.getenv("MATTERMOST_TOKEN")
    if mattermost_token:
        mattermost_url = os.getenv("MATTERMOST_URL", "")
        if not mattermost_url:
            logger.warning("MATTERMOST_TOKEN set but MATTERMOST_URL is missing")
        if Platform.MATTERMOST not in config.platforms:
            config.platforms[Platform.MATTERMOST] = PlatformConfig()
        config.platforms[Platform.MATTERMOST].enabled = True
        config.platforms[Platform.MATTERMOST].token = mattermost_token
        config.platforms[Platform.MATTERMOST].extra["url"] = mattermost_url
    mattermost_home = os.getenv("MATTERMOST_HOME_CHANNEL")
    if mattermost_home and Platform.MATTERMOST in config.platforms:
        config.platforms[Platform.MATTERMOST].home_channel = HomeChannel(
            platform=Platform.MATTERMOST,
            chat_id=mattermost_home,
            name=os.getenv("MATTERMOST_HOME_CHANNEL_NAME", "Home"),
        )

    # Matrix 访问令牌
    matrix_token = os.getenv("MATRIX_ACCESS_TOKEN")
    matrix_homeserver = os.getenv("MATRIX_HOMESERVER", "")
    if matrix_token or os.getenv("MATRIX_PASSWORD"):
        if not matrix_homeserver:
            logger.warning("MATRIX_ACCESS_TOKEN/MATRIX_PASSWORD set but MATRIX_HOMESERVER is missing")
        if Platform.MATRIX not in config.platforms:
            config.platforms[Platform.MATRIX] = PlatformConfig()
        config.platforms[Platform.MATRIX].enabled = True
        if matrix_token:
            config.platforms[Platform.MATRIX].token = matrix_token
        config.platforms[Platform.MATRIX].extra["homeserver"] = matrix_homeserver
        matrix_user = os.getenv("MATRIX_USER_ID", "")
        if matrix_user:
            config.platforms[Platform.MATRIX].extra["user_id"] = matrix_user
        matrix_password = os.getenv("MATRIX_PASSWORD", "")
        if matrix_password:
            config.platforms[Platform.MATRIX].extra["password"] = matrix_password
        matrix_e2ee = os.getenv("MATRIX_ENCRYPTION", "").lower() in ("true", "1", "yes")
        config.platforms[Platform.MATRIX].extra["encryption"] = matrix_e2ee
        matrix_device_id = os.getenv("MATRIX_DEVICE_ID", "")
        if matrix_device_id:
            config.platforms[Platform.MATRIX].extra["device_id"] = matrix_device_id
    matrix_home = os.getenv("MATRIX_HOME_ROOM")
    if matrix_home and Platform.MATRIX in config.platforms:
        config.platforms[Platform.MATRIX].home_channel = HomeChannel(
            platform=Platform.MATRIX,
            chat_id=matrix_home,
            name=os.getenv("MATRIX_HOME_ROOM_NAME", "Home"),
        )

    # Home Assistant 令牌
    hass_token = os.getenv("HASS_TOKEN")
    if hass_token:
        if Platform.HOMEASSISTANT not in config.platforms:
            config.platforms[Platform.HOMEASSISTANT] = PlatformConfig()
        config.platforms[Platform.HOMEASSISTANT].enabled = True
        config.platforms[Platform.HOMEASSISTANT].token = hass_token
        hass_url = os.getenv("HASS_URL")
        if hass_url:
            config.platforms[Platform.HOMEASSISTANT].extra["url"] = hass_url

    # Email 邮箱
    email_addr = os.getenv("EMAIL_ADDRESS")
    email_pwd = os.getenv("EMAIL_PASSWORD")
    email_imap = os.getenv("EMAIL_IMAP_HOST")
    email_smtp = os.getenv("EMAIL_SMTP_HOST")
    if all([email_addr, email_pwd, email_imap, email_smtp]):
        if Platform.EMAIL not in config.platforms:
            config.platforms[Platform.EMAIL] = PlatformConfig()
        config.platforms[Platform.EMAIL].enabled = True
        config.platforms[Platform.EMAIL].extra.update({
            "address": email_addr,
            "imap_host": email_imap,
            "smtp_host": email_smtp,
        })
    email_home = os.getenv("EMAIL_HOME_ADDRESS")
    if email_home and Platform.EMAIL in config.platforms:
        config.platforms[Platform.EMAIL].home_channel = HomeChannel(
            platform=Platform.EMAIL,
            chat_id=email_home,
            name=os.getenv("EMAIL_HOME_ADDRESS_NAME", "Home"),
        )

    # SMS (Twilio) 短信
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    if twilio_sid:
        if Platform.SMS not in config.platforms:
            config.platforms[Platform.SMS] = PlatformConfig()
        config.platforms[Platform.SMS].enabled = True
        config.platforms[Platform.SMS].api_key = os.getenv("TWILIO_AUTH_TOKEN", "")
    sms_home = os.getenv("SMS_HOME_CHANNEL")
    if sms_home and Platform.SMS in config.platforms:
        config.platforms[Platform.SMS].home_channel = HomeChannel(
            platform=Platform.SMS,
            chat_id=sms_home,
            name=os.getenv("SMS_HOME_CHANNEL_NAME", "Home"),
        )

    # API Server API 服务器
    api_server_enabled = os.getenv("API_SERVER_ENABLED", "").lower() in ("true", "1", "yes")
    api_server_key = os.getenv("API_SERVER_KEY", "")
    api_server_cors_origins = os.getenv("API_SERVER_CORS_ORIGINS", "")
    api_server_port = os.getenv("API_SERVER_PORT")
    api_server_host = os.getenv("API_SERVER_HOST")
    if api_server_enabled or api_server_key:
        if Platform.API_SERVER not in config.platforms:
            config.platforms[Platform.API_SERVER] = PlatformConfig()
        config.platforms[Platform.API_SERVER].enabled = True
        if api_server_key:
            config.platforms[Platform.API_SERVER].extra["key"] = api_server_key
        if api_server_cors_origins:
            origins = [origin.strip() for origin in api_server_cors_origins.split(",") if origin.strip()]
            if origins:
                config.platforms[Platform.API_SERVER].extra["cors_origins"] = origins
        if api_server_port:
            try:
                config.platforms[Platform.API_SERVER].extra["port"] = int(api_server_port)
            except ValueError:
                pass
        if api_server_host:
            config.platforms[Platform.API_SERVER].extra["host"] = api_server_host

    # Webhook platform Webhook 平台
    webhook_enabled = os.getenv("WEBHOOK_ENABLED", "").lower() in ("true", "1", "yes")
    webhook_port = os.getenv("WEBHOOK_PORT")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if webhook_enabled:
        if Platform.WEBHOOK not in config.platforms:
            config.platforms[Platform.WEBHOOK] = PlatformConfig()
        config.platforms[Platform.WEBHOOK].enabled = True
        if webhook_port:
            try:
                config.platforms[Platform.WEBHOOK].extra["port"] = int(webhook_port)
            except ValueError:
                pass
        if webhook_secret:
            config.platforms[Platform.WEBHOOK].extra["secret"] = webhook_secret

    # Feishu / Lark 飞书 / 鹿客
    feishu_app_id = os.getenv("FEISHU_APP_ID")
    feishu_app_secret = os.getenv("FEISHU_APP_SECRET")
    if feishu_app_id and feishu_app_secret:
        if Platform.FEISHU not in config.platforms:
            config.platforms[Platform.FEISHU] = PlatformConfig()
        config.platforms[Platform.FEISHU].enabled = True
        config.platforms[Platform.FEISHU].extra.update({
            "app_id": feishu_app_id,
            "app_secret": feishu_app_secret,
            "domain": os.getenv("FEISHU_DOMAIN", "feishu"),
            "connection_mode": os.getenv("FEISHU_CONNECTION_MODE", "websocket"),
        })
        feishu_encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
        if feishu_encrypt_key:
            config.platforms[Platform.FEISHU].extra["encrypt_key"] = feishu_encrypt_key
        feishu_verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        if feishu_verification_token:
            config.platforms[Platform.FEISHU].extra["verification_token"] = feishu_verification_token
        feishu_home = os.getenv("FEISHU_HOME_CHANNEL")
        if feishu_home:
            config.platforms[Platform.FEISHU].home_channel = HomeChannel(
                platform=Platform.FEISHU,
                chat_id=feishu_home,
                name=os.getenv("FEISHU_HOME_CHANNEL_NAME", "Home"),
            )

    # WeCom (Enterprise WeChat) 企业微信
    wecom_bot_id = os.getenv("WECOM_BOT_ID")
    wecom_secret = os.getenv("WECOM_SECRET")
    if wecom_bot_id and wecom_secret:
        if Platform.WECOM not in config.platforms:
            config.platforms[Platform.WECOM] = PlatformConfig()
        config.platforms[Platform.WECOM].enabled = True
        config.platforms[Platform.WECOM].extra.update({
            "bot_id": wecom_bot_id,
            "secret": wecom_secret,
        })
        wecom_ws_url = os.getenv("WECOM_WEBSOCKET_URL", "")
        if wecom_ws_url:
            config.platforms[Platform.WECOM].extra["websocket_url"] = wecom_ws_url
        wecom_home = os.getenv("WECOM_HOME_CHANNEL")
        if wecom_home:
            config.platforms[Platform.WECOM].home_channel = HomeChannel(
                platform=Platform.WECOM,
                chat_id=wecom_home,
                name=os.getenv("WECOM_HOME_CHANNEL_NAME", "Home"),
            )

    # BlueBubbles (iMessage) 蓝色气泡（iMessage）
    bluebubbles_server_url = os.getenv("BLUEBUBBLES_SERVER_URL")
    bluebubbles_password = os.getenv("BLUEBUBBLES_PASSWORD")
    if bluebubbles_server_url and bluebubbles_password:
        if Platform.BLUEBUBBLES not in config.platforms:
            config.platforms[Platform.BLUEBUBBLES] = PlatformConfig()
        config.platforms[Platform.BLUEBUBBLES].enabled = True
        config.platforms[Platform.BLUEBUBBLES].extra.update({
            "server_url": bluebubbles_server_url.rstrip("/"),
            "password": bluebubbles_password,
            "webhook_host": os.getenv("BLUEBUBBLES_WEBHOOK_HOST", "127.0.0.1"),
            "webhook_port": int(os.getenv("BLUEBUBBLES_WEBHOOK_PORT", "8645")),
            "webhook_path": os.getenv("BLUEBUBBLES_WEBHOOK_PATH", "/bluebubbles-webhook"),
            "send_read_receipts": os.getenv("BLUEBUBBLES_SEND_READ_RECEIPTS", "true").lower() in ("true", "1", "yes"),
        })
    bluebubbles_home = os.getenv("BLUEBUBBLES_HOME_CHANNEL")
    if bluebubbles_home and Platform.BLUEBUBBLES in config.platforms:
        config.platforms[Platform.BLUEBUBBLES].home_channel = HomeChannel(
            platform=Platform.BLUEBUBBLES,
            chat_id=bluebubbles_home,
            name=os.getenv("BLUEBUBBLES_HOME_CHANNEL_NAME", "Home"),
        )

    # 会话设置
    idle_minutes = os.getenv("SESSION_IDLE_MINUTES")
    if idle_minutes:
        try:
            config.default_reset_policy.idle_minutes = int(idle_minutes)
        except ValueError:
            pass
    
    reset_hour = os.getenv("SESSION_RESET_HOUR")
    if reset_hour:
        try:
            config.default_reset_policy.at_hour = int(reset_hour)
        except ValueError:
            pass
