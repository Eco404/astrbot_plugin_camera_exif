"""
相机EXIF分析插件 —— AstrBot 插件主入口

CameraExifPlugin 类负责：
- 消息监听与自动检测
- 指令路由（/exif 命令组、字段查询、完整元数据）
- 权限隔离、频率限制、黑白名单
- 图片下载、分析、回复、清理
- 等待模式（先输入指令后发图）

核心分析引擎见 libs.exif_analyzer.ExifAnalyzer
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api.message_components import (
    Node,
    Nodes,
    Plain as MsgPlain,
    Image as CompImage,
    Reply,
    File,
)
from astrbot.core.platform.message_type import MessageType

from .libs.constants import (
    RAW_EXTENSIONS,
    ALL_IMAGE_EXTS,
    FIELD_COMMAND_MAP,
    FIELD_CN_NAMES,
)
from .libs.utils import (
    split_text_chunks,
    make_preview_thumbnail,
)
from .libs.formatter import (
    format_display_text,
    format_full_exif_text,
    format_shutter_only,
)
from .libs.exif_analyzer import ExifAnalyzer

# ================================================================
# 依赖检测
# ================================================================
try:
    from PIL import Image as PILImage

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import exifread

    HAS_EXIFREAD = True
except ImportError:
    HAS_EXIFREAD = False

try:
    import rawpy

    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False


# ================================================================
# 自定义万能过滤器
# ================================================================
class _AlwaysPassFilter(filter.CustomFilter):
    """永远返回 True 的过滤器，确保文件消息也能触发。"""

    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        return True


def _strip_group_command_whitespace(command_group: Any) -> Any:
    """让 AstrBot 命令组的子指令匹配忽略 @ 后残留的前导空白。"""
    group_filter = command_group.parent_group
    original_startswith = group_filter.startswith

    def startswith(message: str) -> bool:
        return original_startswith(message.strip())

    group_filter.startswith = startswith
    return command_group


# ================================================================
# 插件主类
# ================================================================
class CameraExifPlugin(Star):
    """相机EXIF分析插件 —— 自动检测图片EXIF、RAW/DNG解析、快门次数提取"""

    # ── 安全常量 ──
    _ANALYSIS_TIMEOUT: int = 45  # 单次分析超时(秒)
    _RATE_LIMIT_WINDOW: float = 10.0  # 频率限制窗口(秒)
    _RATE_LIMIT_MAX: int = 3  # 窗口内最大自动检测次数
    _RATE_LIMIT_COMMAND_MAX: int = 5  # 窗口内最大指令次数

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config: AstrBotConfig = config
        self._last_results: dict[str, dict[str, Any]] = {}
        self._max_cache_size = 200
        self._rate_limits: dict[str, list[float]] = {}
        self._cmd_rate_limits: dict[str, list[float]] = {}
        # 等待模式: {user_id: (expiry_timestamp, field_key, cn_name)}
        self._waiting_for_image: dict[str, tuple[float, str, str]] = {}

    # ================================================================
    # 生命周期
    # ================================================================

    async def initialize(self) -> None:
        if not self.config.get("enabled", True):
            logger.info("[相机EXIF] 插件已禁用")
            return
        deps = []
        deps.append("Pillow \u2713" if HAS_PIL else "Pillow \u2717")
        deps.append("exifread \u2713" if HAS_EXIFREAD else "exifread \u2717")
        deps.append("rawpy \u2713" if HAS_RAWPY else "rawpy \u2717 (RAW深度解析不可用)")
        logger.info(f"[相机EXIF] 插件已激活 | 依赖: {', '.join(deps)}")
        logger.info(
            f"[相机EXIF] 自动检测: "
            f"{'开启' if self.config.get('auto_detect_enabled', True) else '关闭'}"
        )

    async def terminate(self) -> None:
        self._last_results.clear()
        self._rate_limits.clear()
        self._cmd_rate_limits.clear()
        logger.info("[相机EXIF] 插件已停止")

    # ================================================================
    # 频率限制
    # ================================================================

    def _check_rate_limit(self, user_id: str, is_command: bool = False) -> bool:
        """检查是否超过频率限制。"""
        now = time.time()
        bucket = self._cmd_rate_limits if is_command else self._rate_limits
        max_count = self._RATE_LIMIT_COMMAND_MAX if is_command else self._RATE_LIMIT_MAX

        if user_id not in bucket:
            bucket[user_id] = []
        bucket[user_id] = [
            t for t in bucket[user_id] if now - t < self._RATE_LIMIT_WINDOW
        ]
        if len(bucket[user_id]) >= max_count:
            logger.warning(
                f"[相机EXIF] 频率限制: {user_id} "
                f"({len(bucket[user_id])}/{max_count} in {self._RATE_LIMIT_WINDOW}s)"
            )
            return False
        bucket[user_id].append(now)
        return True

    # ================================================================
    # 权限隔离缓存
    # ================================================================

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        session = str(event.session)
        user_id = event.get_sender_id()
        return f"{session}::{user_id}"

    def _cache_result(self, event: AstrMessageEvent, result: dict[str, Any]) -> None:
        key = self._get_cache_key(event)
        self._last_results[key] = {"result": result, "timestamp": time.time()}
        if len(self._last_results) > self._max_cache_size:
            sorted_items = sorted(
                self._last_results.items(), key=lambda x: x[1]["timestamp"]
            )
            for k, _ in sorted_items[: len(sorted_items) - self._max_cache_size]:
                del self._last_results[k]

    def _get_cached_result(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        key = self._get_cache_key(event)
        cached = self._last_results.get(key)
        if cached and time.time() - cached["timestamp"] < 300:
            return cached["result"]
        if cached:
            del self._last_results[key]
        return None

    # ================================================================
    # 黑白名单
    # ================================================================

    def _check_access(self, event: AstrMessageEvent) -> tuple[bool, str]:
        if not self.config.get("enabled", True):
            return False, "插件已禁用"
        msg_type = event.get_message_type()
        if msg_type == MessageType.GROUP_MESSAGE:
            fc = self.config.get("group_chat_filter", {})
            mode = fc.get("mode", "all")
            gid = str(event.get_group_id())
            if mode == "whitelist" and gid not in [
                str(g) for g in fc.get("whitelist", [])
            ]:
                return False, "该群不在白名单中"
            if mode == "blacklist" and gid in [str(g) for g in fc.get("blacklist", [])]:
                return False, "该群在黑名单中"
            return True, ""
        else:
            fc = self.config.get("private_chat_filter", {})
            mode = fc.get("mode", "all")
            uid = event.get_sender_id()
            if mode == "whitelist" and uid not in [
                str(u) for u in fc.get("whitelist", [])
            ]:
                return False, "您不在白名单中"
            if mode == "blacklist" and uid in [str(u) for u in fc.get("blacklist", [])]:
                return False, "您在黑名单中"
            return True, ""

    # ================================================================
    # 回复格式化与发送
    # ================================================================

    async def _format_reply(
        self,
        event: AstrMessageEvent,
        text: str,
        thumb_path: str | None = None,
        force_plain: bool = False,
    ):
        """异步生成回复消息。文本模式自动分片+可配间隔。"""
        reply_mode = (
            "文本发送" if force_plain else self.config.get("reply_mode", "文本发送")
        )
        forward_name = self.config.get("forward_display_name", "相机EXIF分析")
        interval = self.config.get("text_chunk_interval", 1.0)

        if reply_mode == "转发发送":
            bot_uin = str(event.get_self_id() or "10000")
            nodes = [
                Node(uin=bot_uin, name=forward_name, content=[MsgPlain(text)]),
            ]
            if thumb_path and os.path.isfile(thumb_path):
                nodes.append(
                    Node(
                        uin=bot_uin,
                        name=forward_name,
                        content=[
                            MsgPlain("\U0001f5bc\ufe0f[\u56fe\u7247\u9884\u89c8]")
                        ],
                    )
                )
                nodes.append(
                    Node(
                        uin=bot_uin,
                        name=forward_name,
                        content=[CompImage.fromFileSystem(thumb_path)],
                    )
                )
            yield event.chain_result([Nodes(nodes)])
        else:
            chunks = split_text_chunks(text)
            total = len(chunks)
            logger.info(f"[相机EXIF] 文本分 {total} 片发送")
            for i, chunk in enumerate(chunks):
                preview = chunk[:80].replace("\n", "\\n")
                logger.info(f"[相机EXIF] 片{i + 1}/{total}: {len(chunk)}字, {preview}")
                yield event.plain_result(chunk)
                if i < total - 1 and interval > 0:
                    await asyncio.sleep(interval)

            if thumb_path and os.path.isfile(thumb_path):
                if interval > 0:
                    await asyncio.sleep(interval)
                yield event.plain_result("\U0001f5bc\ufe0f[\u56fe\u7247\u9884\u89c8]")
                if interval > 0:
                    await asyncio.sleep(interval)
                yield event.make_result().file_image(thumb_path)

    # ================================================================
    # 文件类型检测 & 路径获取
    # ================================================================

    @staticmethod
    def _is_processable_image(msg_component) -> bool:
        """判断消息组件是否是可处理的图片。"""
        if isinstance(msg_component, CompImage):
            return True
        if isinstance(msg_component, File):
            fname = getattr(msg_component, "name", "") or ""
            furl = getattr(msg_component, "url", "") or ""
            ffile = getattr(msg_component, "file_", "") or ""
            for candidate in (fname, furl, ffile):
                if candidate:
                    ext = os.path.splitext(candidate)[1].lower()
                    if ext in ALL_IMAGE_EXTS:
                        return True
            return False  # 非图片扩展名不处理
        return False

    @staticmethod
    async def _get_file_path(comp) -> str | None:
        """从 Image 或 File 组件获取本地文件路径。"""
        if isinstance(comp, CompImage):
            return await comp.convert_to_file_path()
        if isinstance(comp, File):
            return await comp.get_file()
        return None

    # ================================================================
    # 图片清理
    # ================================================================

    @staticmethod
    def _cleanup_temp_image(file_path: str) -> None:
        """安全删除临时图片文件，仅限 AstrBot temp 目录。"""
        try:
            if not file_path or not os.path.isfile(file_path):
                return
            from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

            abs_path = os.path.abspath(file_path)
            temp_dir = os.path.abspath(get_astrbot_temp_path())
            if not abs_path.startswith(temp_dir):
                logger.warning(f"[相机EXIF] 拒绝删除非临时目录文件: {abs_path}")
                return
            os.remove(file_path)
            logger.debug(f"[相机EXIF] 已清理: {os.path.basename(file_path)}")
        except Exception:
            pass

    # ================================================================
    # 图片分析核心
    # ================================================================

    async def _analyze_image(
        self, file_path: str, event: AstrMessageEvent | None = None
    ) -> dict[str, Any] | None:
        """分析图片 EXIF 元数据。"""
        if not file_path or not os.path.isfile(file_path):
            logger.warning(f"[相机EXIF] 无效文件路径: {file_path}")
            return None
        try:
            max_size = self.config.get("max_image_size_mb", 50)
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            if file_size > max_size:
                logger.info(f"[相机EXIF] 图片过大 ({file_size:.1f}MB)，跳过")
                return {
                    "file_info": {
                        "name": os.path.basename(file_path),
                        "size_mb": round(file_size, 2),
                        "is_raw": False,
                        "raw_format": "",
                    },
                    "is_camera_image": False,
                    "exif_data": {},
                    "shutter_count": None,
                    "gps": {},
                    "maker_note": {},
                    "xmp": {},
                    "errors": [f"图片过大 ({file_size:.1f}MB > {max_size}MB)"],
                }

            config = {
                "raw_format_config": self.config.get("raw_format_config", {}),
                "max_image_size_mb": max_size,
                "gps_map_provider": self.config.get("gps_map_provider", "高德地图"),
                "gps_custom_map_url": self.config.get("gps_custom_map_url", ""),
            }
            analyzer = ExifAnalyzer(file_path, config=config)
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, analyzer.analyze),
                timeout=self._ANALYSIS_TIMEOUT,
            )
            if event:
                self._cache_result(event, result)
            return result
        except asyncio.TimeoutError:
            logger.warning(
                f"[相机EXIF] 分析超时 ({self._ANALYSIS_TIMEOUT}s): "
                f"{os.path.basename(file_path)}"
            )
            return None
        except Exception as e:
            logger.error(f"[相机EXIF] 分析失败: {e}", exc_info=True)
            return None

    async def _analyze_and_reply_image(
        self,
        event: AstrMessageEvent,
        file_path: str,
        show_analyzing: bool = False,
        notify_no_exif: bool = False,
    ) -> AsyncGenerator[MessageChain, None]:
        """分析图片并生成回复。"""
        result = await self._analyze_image(file_path, event)
        if not result:
            self._cleanup_temp_image(file_path)
            if notify_no_exif:
                yield event.plain_result("\u26a0\ufe0f 图片分析失败，请重试")
            return

        if not result.get("is_camera_image"):
            self._cleanup_temp_image(file_path)
            if notify_no_exif:
                yield event.plain_result(
                    "\u26a0\ufe0f 该图片不包含 EXIF 信息（截图/网图等非相机拍摄图片）"
                )
            return

        if show_analyzing:
            yield event.plain_result("\U0001f50d 检测到相机图片，正在分析 EXIF 数据...")

        # 格式化结果
        fields = self.config.get("display_fields", {})
        show_detailed = self.config.get("show_detailed_exif_default", False)
        if show_detailed:
            text = format_full_exif_text(result)
        else:
            text = format_display_text(result, fields)

        # GPS 隐私提醒
        if result.get("gps") and fields.get("gps", False):
            text += "\n\n\U0001f512 GPS位置信息已显示，请注意隐私保护"

        # 缩略图
        thumb_path = None
        if self.config.get("send_preview_thumbnail", False):
            thumb_path = await make_preview_thumbnail(file_path)

        async for r in self._format_reply(event, text, thumb_path):
            yield r

        self._cleanup_temp_image(file_path)

    # ================================================================
    # 结果回复（字段查询/完整查询）
    # ================================================================

    async def _reply_result(
        self,
        event: AstrMessageEvent,
        file_path: str,
        mode: str,
        field_key: str = "",
        cn_name: str = "",
    ):
        """分析图片并回复。字段查询强制文本发送、不发预览图。"""
        result = await self._analyze_image(file_path, event)
        if not result:
            self._cleanup_temp_image(file_path)
            return

        is_field_query = mode != "full"
        thumb_path = None

        if not is_field_query and self.config.get("send_preview_thumbnail", False):
            thumb_path = await make_preview_thumbnail(file_path)

        self._cleanup_temp_image(file_path)

        # 消息文本
        if mode == "full":
            text = format_full_exif_text(result)
        else:
            exif = result.get("exif_data", {})
            if field_key == "shutter_count":
                text = (
                    f"\U0001f4f7 {cn_name}: {result.get('shutter_count') or '无法获取'}"
                )
            elif field_key == "image_size":
                text = (
                    f"\U0001f5bc\ufe0f {cn_name}: "
                    f"{exif.get('ImageWidth', '?')} \u00d7 "
                    f"{exif.get('ImageLength', '?')} px"
                )
            elif field_key == "gps":
                gps = result.get("gps", {})
                if gps:
                    lines = [f"\U0001f4cd {cn_name}:"]
                    for k, v in gps.items():
                        if k != "map_url" and v:
                            lines.append(f"  {k}: {v}")
                    if gps.get("map_url"):
                        lines.append(f"  \U0001f5fa\ufe0f {gps['map_url']}")
                    text = "\n".join(lines)
                else:
                    text = "\U0001f4cd 该图片无GPS信息"
            else:
                val = exif.get(field_key, "")
                text = (
                    f"\U0001f4f8 {cn_name}: {val}"
                    if val
                    else f"\u26a0\ufe0f 该图片未包含{cn_name}信息"
                )

        async for r in self._format_reply(
            event, text, thumb_path, force_plain=is_field_query
        ):
            yield r

    # ================================================================
    # 图片路径提取（权限隔离）
    # ================================================================

    def _is_blocked_reference(self, event: AstrMessageEvent) -> bool:
        """检查是否引用了别人的图片且被权限拦截。"""
        if self.config.get("allow_reference_query", False):
            return False
        my_uid = event.get_sender_id()
        for msg in event.get_messages():
            if isinstance(msg, Reply):
                ref_uid = str(getattr(msg, "sender_id", ""))
                if ref_uid and ref_uid != my_uid:
                    return True
        return False

    async def _get_image_path_from_event(self, event: AstrMessageEvent) -> str | None:
        """从事件中提取图片路径（优先引用图片 > 直接图片，含权限控制）。"""
        allow_ref = self.config.get("allow_reference_query", False)
        my_uid = event.get_sender_id()

        for msg in event.get_messages():
            if isinstance(msg, Reply):
                ref_uid = str(getattr(msg, "sender_id", ""))
                if allow_ref or ref_uid == my_uid:
                    ref_chain = (
                        getattr(msg, "chain", []) or getattr(msg, "message", []) or []
                    )
                    for ref_msg in ref_chain:
                        if self._is_processable_image(ref_msg):
                            try:
                                path = await self._get_file_path(ref_msg)
                                if path and os.path.isfile(path):
                                    return path
                            except Exception:
                                pass
        # 直接图片
        for msg in event.get_messages():
            if self._is_processable_image(msg):
                try:
                    path = await self._get_file_path(msg)
                    if path and os.path.isfile(path):
                        return path
                except Exception:
                    pass
        return None

    # ================================================================
    # 通用字段查询
    # ================================================================

    async def _query_field(self, event: AstrMessageEvent, field_key: str, cn_name: str):
        """通用字段查询处理。"""
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"\u26a0\ufe0f {reason}")
            return
        if not self._check_rate_limit(event.get_sender_id(), is_command=True):
            yield event.plain_result("\u26a0\ufe0f 查询过于频繁，请稍后再试")
            return

        file_path = await self._get_image_path_from_event(event)
        if file_path:
            async for r in self._reply_result(
                event, file_path, "field", field_key, cn_name
            ):
                yield r
            event.stop_event()
            return

        if self._is_blocked_reference(event):
            yield event.plain_result(
                "\U0001f512 这不是你发送的图片哦~\n"
                "主人没有开放别人查看其他人的 EXIF 信息呢"
            )
            return

        timeout = self.config.get("wait_timeout_seconds", 120)
        command_hint = f"/exif get {cn_name}"
        if timeout <= 0:
            yield event.plain_result(
                f"\u26a0\ufe0f 请发送图片或@引用图片后再使用 {command_hint}"
            )
        else:
            self._waiting_for_image[event.get_sender_id()] = (
                time.time() + timeout,
                field_key,
                cn_name,
            )
            yield event.plain_result(
                f"\u26a0\ufe0f 请发送图片或@引用图片后再使用 {command_hint}\n"
                f"\u23f3 请在 {timeout} 秒内发送图片，超时自动退出检测"
            )

    # ================================================================
    # 指令区 — /exif 命令组
    # ================================================================

    @_strip_group_command_whitespace
    @filter.command_group("exif")
    def exif(self):
        """相机 EXIF 查询命令组。"""
        pass

    @exif.command("help")
    async def exif_help(self, event: AstrMessageEvent):
        """显示帮助菜单。"""
        event.stop_event()
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"\u26a0\ufe0f {reason}")
            return
        cmds = "\n".join(
            f"  /exif get {field_name}"
            for field_name in FIELD_CN_NAMES.values()
        )
        separator = "\u2550" * 36
        help_text = "\n".join(
            [
                "\U0001f4f8 相机EXIF分析插件 — 使用帮助",
                separator,
                "",
                "\U0001f4cb 命令：",
                "  /exif help  \u2514 显示本帮助菜单",
                "  /exif full  \u2514 查询完整EXIF元数据",
                "  /exif get <字段>  \u2514 查询单个字段",
                "",
                "\U0001f4ca 字段单独查询：",
                cmds,
                "",
                "\U0001f4f8 支持格式：JPEG/TIFF/PNG/RAW(CR2/NEF/ARW等)/DNG",
                separator,
            ]
        )
        yield event.plain_result(help_text)

    @exif.command("full")
    async def query_exif(self, event: AstrMessageEvent):
        """查询完整 EXIF 元数据。"""
        event.stop_event()
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"\u26a0\ufe0f {reason}")
            return
        if not self._check_rate_limit(event.get_sender_id(), is_command=True):
            yield event.plain_result("\u26a0\ufe0f 查询过于频繁，请稍后再试")
            return

        file_path = await self._get_image_path_from_event(event)
        if file_path:
            async for r in self._reply_result(event, file_path, "full", ""):
                yield r
            event.stop_event()
            return

        if self._is_blocked_reference(event):
            yield event.plain_result(
                "\U0001f512 这不是你发送的图片哦~\n"
                "主人没有开放别人查看其他人的 EXIF 信息呢"
            )
            return

        timeout = self.config.get("wait_timeout_seconds", 120)
        if timeout <= 0:
            yield event.plain_result(
                "\u26a0\ufe0f 请发送图片或@引用图片后再使用 /exif full"
            )
            return
        self._waiting_for_image[event.get_sender_id()] = (
            time.time() + timeout,
            "",
            "完整EXIF",
        )
        yield event.plain_result(
            "\u26a0\ufe0f 请发送图片或@引用图片后再使用 /exif full\n"
            f"\u23f3 请在 {timeout} 秒内发送图片，超时自动退出检测"
        )

    @exif.command("get")
    async def query_exif_field(self, event: AstrMessageEvent, field: str):
        """查询指定 EXIF 字段。"""
        event.stop_event()
        normalized_field = field.strip().casefold()
        field_key = next(
            (
                key
                for name, key in FIELD_COMMAND_MAP.items()
                if name.casefold() == normalized_field
            ),
            None,
        )
        if not field_key:
            supported = "、".join(FIELD_CN_NAMES.values())
            yield event.plain_result(
                f"\u26a0\ufe0f 不支持的 EXIF 字段：{field}\n"
                f"可用字段：{supported}\n"
                "使用 /exif help 查看完整帮助"
            )
            return

        cn_name = FIELD_CN_NAMES.get(field_key, field)
        async for result in self._query_field(event, field_key, cn_name):
            yield result

    # ================================================================
    # 自动检测 — 监听所有消息（等待模式优先）
    # ================================================================

    @filter.custom_filter(_AlwaysPassFilter, False)
    async def auto_detect_images(self, event: AstrMessageEvent):
        """监听所有消息，自动检测图片/文件中的EXIF并回复。"""
        if not self.config.get("enabled", True):
            return
        if event.is_stopped():
            return

        uid = event.get_sender_id()
        in_waiting = uid in self._waiting_for_image
        wait_field_key = ""
        wait_cn_name = ""

        now = time.time()
        if in_waiting:
            expiry, wait_field_key, wait_cn_name = self._waiting_for_image[uid]
            if now > expiry:
                del self._waiting_for_image[uid]
                yield event.plain_result("\u23f0 超时未发送图片，已退出检测")
                return

        if not in_waiting:
            if not self.config.get("auto_detect_enabled", True):
                return
            if self.config.get("reply_mode", "文本发送") == "不发送":
                return

        comps = event.get_messages()
        found = False
        for comp in comps:
            if not self._is_processable_image(comp):
                continue
            found = True
            if in_waiting:
                self._waiting_for_image.pop(uid, None)
            try:
                file_path = await self._get_file_path(comp)
                if not file_path or not os.path.isfile(file_path):
                    logger.warning(
                        f"[相机EXIF] 文件下载失败: "
                        f"{getattr(comp, 'name', '') or comp.__class__.__name__}"
                    )
                    continue

                result = await self._analyze_image(file_path, event)
                if not result:
                    self._cleanup_temp_image(file_path)
                    if in_waiting:
                        yield event.plain_result("\u26a0\ufe0f 图片分析失败，请重试")
                    continue
                if not result.get("is_camera_image"):
                    self._cleanup_temp_image(file_path)
                    if in_waiting:
                        yield event.plain_result(
                            "\u26a0\ufe0f 该图片不包含 EXIF 信息"
                            "（截图/网图等非相机拍摄图片）"
                        )
                    continue

                # 等待模式 → 仅返回对应字段
                if in_waiting and wait_field_key:
                    self._cleanup_temp_image(file_path)
                    exif = result.get("exif_data", {})
                    if wait_field_key == "shutter_count":
                        sc = result.get("shutter_count") or "无法获取"
                        yield event.plain_result(f"\U0001f4f7 {wait_cn_name}: {sc}")
                    elif wait_field_key == "image_size":
                        yield event.plain_result(
                            f"\U0001f5bc\ufe0f {wait_cn_name}: "
                            f"{exif.get('ImageWidth', '?')} \u00d7 "
                            f"{exif.get('ImageLength', '?')} px"
                        )
                    elif wait_field_key == "gps":
                        gps = result.get("gps", {})
                        if gps:
                            lines = [f"\U0001f4cd {wait_cn_name}:"]
                            for k, v in gps.items():
                                if k != "map_url" and v:
                                    lines.append(f"  {k}: {v}")
                            if gps.get("map_url"):
                                lines.append(f"  \U0001f5fa\ufe0f {gps['map_url']}")
                            yield event.plain_result("\n".join(lines))
                        else:
                            yield event.plain_result("\U0001f4cd 该图片无GPS信息")
                    else:
                        val = exif.get(wait_field_key, "")
                        yield event.plain_result(
                            f"\U0001f4f8 {wait_cn_name}: {val}"
                            if val
                            else f"\u26a0\ufe0f 该图片未包含{wait_cn_name}信息"
                        )
                else:
                    # 完整回复（/exif full 或普通自动检测）
                    show_hint = self.config.get("show_analyzing_hint", True)
                    async for reply in self._analyze_and_reply_image(
                        event,
                        file_path,
                        show_analyzing=show_hint,
                        notify_no_exif=in_waiting,
                    ):
                        yield reply
            except Exception as e:
                logger.error(f"[相机EXIF] 自动检测错误: {e}", exc_info=True)

    # ================================================================
    # AstrBot 加载完成钩子
    # ================================================================

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        """AstrBot 框架加载完成时触发。"""
        logger.info("[相机EXIF] AstrBot已加载，相机EXIF分析插件就绪")
