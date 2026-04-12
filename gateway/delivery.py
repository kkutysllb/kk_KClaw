"""
Cron 作业输出和代理响应的传递路由。

根据以下内容将消息路由到适当的目的地：
- 显式目标（例如 "telegram:123456789"）
- 平台主页渠道（例如 "telegram" → 主页渠道）
- 来源（返回到创建作业的地方）
- 本地（始终保存到文件）
"""

import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union

from kclaw_cli.config import get_kclaw_home

logger = logging.getLogger(__name__)

MAX_PLATFORM_OUTPUT = 4000
TRUNCATED_VISIBLE = 3800

from .config import Platform, GatewayConfig
from .session import SessionSource


@dataclass
class DeliveryTarget:
    """
    单个传递目标。
    
    表示消息应该发送到哪里：
    - "origin" → 返回来源
    - "local" → 保存到本地文件
    - "telegram" → Telegram 主页渠道
    - "telegram:123456" → 特定 Telegram 聊天
    """
    platform: Platform
    chat_id: Optional[str] = None  # None 表示使用主页渠道
    thread_id: Optional[str] = None
    is_origin: bool = False
    is_explicit: bool = False  # True 表示 chat_id 是显式指定的
    
    @classmethod
    def parse(cls, target: str, origin: Optional[SessionSource] = None) -> "DeliveryTarget":
        """
        解析传递目标字符串。
        
        格式：
        - "origin" → 返回来源
        - "local" → 仅本地文件
        - "telegram" → Telegram 主页渠道
        - "telegram:123456" → 特定 Telegram 聊天
        """
        target = target.strip().lower()
        
        if target == "origin":
            if origin:
                return cls(
                    platform=origin.platform,
                    chat_id=origin.chat_id,
                    thread_id=origin.thread_id,
                    is_origin=True,
                )
            else:
                # Fallback to local if no origin
                return cls(platform=Platform.LOCAL, is_origin=True)
        
        if target == "local":
            return cls(platform=Platform.LOCAL)
        
        # Check for platform:chat_id or platform:chat_id:thread_id format
        if ":" in target:
            parts = target.split(":", 2)
            platform_str = parts[0]
            chat_id = parts[1] if len(parts) > 1 else None
            thread_id = parts[2] if len(parts) > 2 else None
            try:
                platform = Platform(platform_str)
                return cls(platform=platform, chat_id=chat_id, thread_id=thread_id, is_explicit=True)
            except ValueError:
                # Unknown platform, treat as local
                return cls(platform=Platform.LOCAL)
        
        # Just a platform name (use home channel)
        try:
            platform = Platform(target)
            return cls(platform=platform)
        except ValueError:
            # Unknown platform, treat as local
            return cls(platform=Platform.LOCAL)
    
    def to_string(self) -> str:
        """转换回字符串格式。"""
        if self.is_origin:
            return "origin"
        if self.platform == Platform.LOCAL:
            return "local"
        if self.chat_id and self.thread_id:
            return f"{self.platform.value}:{self.chat_id}:{self.thread_id}"
        if self.chat_id:
            return f"{self.platform.value}:{self.chat_id}"
        return self.platform.value


class DeliveryRouter:
    """
    将消息路由到适当的目的地。
    
    处理将传递目标解析并分派
    消息到正确平台适配器的逻辑。
    """
    
    def __init__(self, config: GatewayConfig, adapters: Dict[Platform, Any] = None):
        """
        初始化传递路由器。
        
        参数：
            config: 网关配置
            adapters: 将平台映射到其适配器实例的字典
        """
        self.config = config
        self.adapters = adapters or {}
        self.output_dir = get_kclaw_home() / "cron" / "output"
    
    def resolve_targets(
        self,
        deliver: Union[str, List[str]],
        origin: Optional[SessionSource] = None
    ) -> List[DeliveryTarget]:
        """
        将传递规范解析为具体目标。
        
        参数：
            deliver: 传递规范 - "origin", "telegram", ["local", "discord"] 等
            origin: 请求来源的来源（用于 "origin" 目标）
        
        返回：
            已解析的传递目标列表
        """
        if isinstance(deliver, str):
            deliver = [deliver]
        
        targets = []
        seen_platforms = set()
        
        for target_str in deliver:
            target = DeliveryTarget.parse(target_str, origin)
            
            # Resolve home channel if needed
            if target.chat_id is None and target.platform != Platform.LOCAL:
                home = self.config.get_home_channel(target.platform)
                if home:
                    target.chat_id = home.chat_id
                else:
                    # No home channel configured, skip this platform
                    continue
            
            # Deduplicate
            key = (target.platform, target.chat_id, target.thread_id)
            if key not in seen_platforms:
                seen_platforms.add(key)
                targets.append(target)
        
        # Always include local if configured
        if self.config.always_log_local:
            local_key = (Platform.LOCAL, None, None)
            if local_key not in seen_platforms:
                targets.append(DeliveryTarget(platform=Platform.LOCAL))
        
        return targets
    
    async def deliver(
        self,
        content: str,
        targets: List[DeliveryTarget],
        job_id: Optional[str] = None,
        job_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        将内容传递到所有指定目标。
        
        参数：
            content: 要传递的消息/输出
            targets: 传递目标列表
            job_id: 可选的作业 ID（用于 cron 作业）
            job_name: 可选的作业名称
            metadata: 要包含的其他元数据
        
        返回：
            每个目标的传递结果字典
        """
        results = {}
        
        for target in targets:
            try:
                if target.platform == Platform.LOCAL:
                    result = self._deliver_local(content, job_id, job_name, metadata)
                else:
                    result = await self._deliver_to_platform(target, content, metadata)
                
                results[target.to_string()] = {
                    "success": True,
                    "result": result
                }
            except Exception as e:
                results[target.to_string()] = {
                    "success": False,
                    "error": str(e)
                }
        
        return results
    
    def _deliver_local(
        self,
        content: str,
        job_id: Optional[str],
        job_name: Optional[str],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """将内容保存到本地文件。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if job_id:
            output_path = self.output_dir / job_id / f"{timestamp}.md"
        else:
            output_path = self.output_dir / "misc" / f"{timestamp}.md"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Build the output document
        lines = []
        if job_name:
            lines.append(f"# {job_name}")
        else:
            lines.append("# Delivery Output")
        
        lines.append("")
        lines.append(f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if job_id:
            lines.append(f"**Job ID:** {job_id}")
        
        if metadata:
            for key, value in metadata.items():
                lines.append(f"**{key}:** {value}")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(content)
        
        output_path.write_text("\n".join(lines))
        
        return {
            "path": str(output_path),
            "timestamp": timestamp
        }
    
    def _save_full_output(self, content: str, job_id: str) -> Path:
        """将完整的 cron 输出保存到磁盘并返回文件路径。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = get_kclaw_home() / "cron" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{job_id}_{timestamp}.txt"
        path.write_text(content)
        return path

    async def _deliver_to_platform(
        self,
        target: DeliveryTarget,
        content: str,
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """将内容传递到消息平台。"""
        adapter = self.adapters.get(target.platform)
        
        if not adapter:
            raise ValueError(f"No adapter configured for {target.platform.value}")
        
        if not target.chat_id:
            raise ValueError(f"No chat ID for {target.platform.value} delivery")
        
        # Guard: truncate oversized cron output to stay within platform limits
        if len(content) > MAX_PLATFORM_OUTPUT:
            job_id = (metadata or {}).get("job_id", "unknown")
            saved_path = self._save_full_output(content, job_id)
            logger.info("Cron output truncated (%d chars) — full output: %s", len(content), saved_path)
            content = (
                content[:TRUNCATED_VISIBLE]
                + f"\n\n... [truncated, full output saved to {saved_path}]"
            )
        
        send_metadata = dict(metadata or {})
        if target.thread_id and "thread_id" not in send_metadata:
            send_metadata["thread_id"] = target.thread_id
        return await adapter.send(target.chat_id, content, metadata=send_metadata or None)


def parse_deliver_spec(
    deliver: Optional[Union[str, List[str]]],
    origin: Optional[SessionSource] = None,
    default: str = "origin"
) -> Union[str, List[str]]:
    """
    规范化传递规范。
    
    如果为 None 或空，则返回默认值。
    """
    if not deliver:
        return default
    return deliver



