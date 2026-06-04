"""
相机EXIF分析插件 —— AstrBot 插件
自动检测图片来源，提取相机EXIF数据（快门次数、光圈、ISO等），
支持主流相机RAW格式解析，自动回传元数据至原始聊天渠道。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import struct
import sys
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api.message_components import Node, Plain as MsgPlain, Image as CompImage, Reply, File
from astrbot.core.platform.message_type import MessageType

# 自定义万能过滤器：永远返回 True，确保文件消息也能触发
class _AlwaysPassFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        return True

# ============================================================
# 依赖检测
# ============================================================
try:
    from PIL import Image as PILImage
    from PIL.ExifTags import TAGS as EXIF_TAGS, GPSTAGS

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import exifread

    HAS_EXIFREAD = True
except ImportError:
    HAS_EXIFREAD = False

# rawpy 可选依赖
try:
    import rawpy

    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False


# ============================================================
# RAW 文件扩展名映射
# ============================================================
RAW_EXTENSIONS: dict[str, str] = {
    # Canon
    ".cr2": "Canon CR2",
    ".cr3": "Canon CR3",
    ".crw": "Canon CRW",
    # Nikon
    ".nef": "Nikon NEF",
    ".nrw": "Nikon NRW",
    # Sony
    ".arw": "Sony ARW",
    ".srf": "Sony SRF",
    ".sr2": "Sony SR2",
    # Fujifilm
    ".raf": "Fujifilm RAF",
    # Olympus
    ".orf": "Olympus ORF",
    # Panasonic
    ".rw2": "Panasonic RW2",
    # Pentax
    ".pef": "Pentax PEF",
    ".dng": "Pentax DNG",
    # Leica
    ".raw": "Leica RAW",
    ".rwl": "Leica RWL",
    # Hasselblad
    ".3fr": "Hasselblad 3FR",
    ".fff": "Hasselblad FFF",
    # Phase One
    ".iiq": "Phase One IIQ",
    # Samsung
    ".srw": "Samsung SRW",
    # Minolta
    ".mrw": "Minolta MRW",
    # Sigma
    ".x3f": "Sigma X3F",
    # Epson
    ".erf": "Epson ERF",
    # General
    ".dng": "Adobe DNG",
}

# 所有支持的图片/RAW文件扩展名（用于 File 组件检测）
ALL_IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif", ".bmp"} | set(RAW_EXTENSIONS.keys())


# ============================================================
# 快门次数 MakerNote 标签映射 (各厂商)
# ============================================================
SHUTTER_COUNT_TAGS: dict[str, list[int | str]] = {
    "Canon": [0x0093, 0x0095, 0x0096, 0x0099, "ImageCount", "TotalShutterCount"],
    "NIKON": [0x00A7, 0x00A8, "ShutterCount", "TotalShutterReleases"],
    "SONY": [
        0x9400,
        0x9401,
        0x9402,
        0x9403,
        0x940E,
        "ShutterCount",
        "ImageCount",
    ],
    "FUJIFILM": [0x0010, 0x1431, "ImageCount", "ShutterCount"],
    "PENTAX": [0x003E, 0x004D, "ShutterCount"],
    "OLYMPUS": [0x0207, "ShutterCount", "ImageCount"],
    "Panasonic": [0x0032, "ShutterCount"],
    "LEICA": [0x0010, "ShutterCount"],
    "Minolta": [0x0020, "ShutterCount"],
}


# ============================================================
# EXIF 解析工具函数
# ============================================================


def _convert_to_degrees(values) -> float:
    """将 GPS 坐标元组转换为十进制度数"""
    try:
        if isinstance(values, (list, tuple)):
            d = float(values[0])
            m = float(values[1])
            s = float(values[2])
            return d + (m / 60.0) + (s / 3600.0)
        return float(values)
    except (ValueError, TypeError, IndexError):
        return 0.0


def _clean_tag_value(value: Any) -> str:
    """清理 EXIF 标签值，转为可读字符串"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:
            return str(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return str(value).strip().rstrip("\x00")


def _format_fraction(val) -> str:
    """格式化分数值"""
    if val is None:
        return ""
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            if val.denominator == 1:
                return str(val.numerator)
            return f"{val.numerator}/{val.denominator}"
        return str(val)
    except Exception:
        return str(val)


def _rational_to_float(val) -> float | None:
    """将 Rational 值转换为浮点数"""
    if val is None:
        return None
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            if val.denominator != 0:
                return float(val.numerator) / float(val.denominator)
        return float(val)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


# ============================================================
# EXIF 分析引擎
# ============================================================


class ExifAnalyzer:
    """EXIF 数据解析引擎，支持 JPEG/TIFF/RAW 等多种格式"""

    # 常见 EXIF 标签中文名映射
    TAG_NAMES_CN: dict[str, str] = {
        "Make": "相机品牌",
        "Model": "相机型号",
        "Software": "处理软件",
        "DateTime": "拍摄时间",
        "DateTimeOriginal": "原始拍摄时间",
        "DateTimeDigitized": "数字化时间",
        "ExposureTime": "快门速度",
        "FNumber": "光圈值",
        "ExposureProgram": "曝光模式",
        "ISOSpeedRatings": "ISO感光度",
        "FocalLength": "焦距",
        "FocalLengthIn35mmFilm": "35mm等效焦距",
        "LensModel": "镜头型号",
        "LensMake": "镜头品牌",
        "Flash": "闪光灯",
        "WhiteBalance": "白平衡",
        "MeteringMode": "测光模式",
        "ExposureBiasValue": "曝光补偿",
        "ExposureMode": "曝光模式(Exif)",
        "ColorSpace": "色彩空间",
        "SceneCaptureType": "场景类型",
        "Contrast": "对比度",
        "Saturation": "饱和度",
        "Sharpness": "锐度",
        "ImageWidth": "图片宽度",
        "ImageLength": "图片高度",
        "Orientation": "方向",
        "Artist": "作者",
        "Copyright": "版权",
        "BodySerialNumber": "机身序列号",
        "SerialNumber": "序列号",
        "GPSLatitude": "GPS纬度",
        "GPSLongitude": "GPS经度",
        "GPSAltitude": "GPS海拔",
        "GPSInfo": "GPS信息",
        "ShutterCount": "快门次数",
    }

    # 曝光模式映射
    EXPOSURE_PROGRAMS: dict[int, str] = {
        0: "未定义",
        1: "手动",
        2: "程序自动",
        3: "光圈优先",
        4: "快门优先",
        5: "创意程序",
        6: "运动模式",
        7: "人像模式",
        8: "风景模式",
    }

    # 测光模式映射
    METERING_MODES: dict[int, str] = {
        0: "未知",
        1: "平均测光",
        2: "中央重点测光",
        3: "点测光",
        4: "多点测光",
        5: "多区测光",
        6: "局部测光",
        255: "其他",
    }

    # 闪光灯状态映射
    FLASH_STATUS: dict[int, str] = {
        0x0: "未闪光",
        0x1: "已闪光",
        0x5: "闪光(未检测到返回光)",
        0x7: "闪光(检测到返回光)",
        0x8: "关闭",
        0x9: "强制闪光",
        0xD: "强制闪光(未检测到返回光)",
        0xF: "强制闪光(检测到返回光)",
        0x10: "未闪光(强制)",
        0x18: "自动",
        0x19: "自动(闪光)",
        0x1D: "自动(闪光, 未检测到返回光)",
        0x1F: "自动(闪光, 检测到返回光)",
        0x20: "无闪光功能",
        0x41: "防红眼",
        0x45: "防红眼(未检测到返回光)",
        0x47: "防红眼(检测到返回光)",
        0x49: "防红眼(强制闪光)",
        0x4D: "防红眼(强制闪光, 未检测到返回光)",
        0x4F: "防红眼(强制闪光, 检测到返回光)",
        0x59: "防红眼(自动闪光)",
        0x5D: "防红眼(自动闪光, 未检测到返回光)",
        0x5F: "防红眼(自动闪光, 检测到返回光)",
    }

    def __init__(self, file_path: str = "", config: dict[str, Any] | None = None) -> None:
        self.file_path = file_path
        self.config = config or {}
        if file_path and os.path.isfile(file_path):
            self.file_name = os.path.basename(file_path)
            self.file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            self.file_ext = os.path.splitext(file_path)[1].lower()
        else:
            self.file_name = ""
            self.file_size_mb = 0.0
            self.file_ext = ""
        self.is_raw = self.file_ext in RAW_EXTENSIONS
        self.raw_format = RAW_EXTENSIONS.get(self.file_ext, "")

    def analyze(self) -> dict[str, Any]:
        """执行完整的 EXIF 分析，返回结构化结果"""
        result: dict[str, Any] = {
            "file_info": {
                "name": self.file_name,
                "size_mb": round(self.file_size_mb, 2),
                "is_raw": self.is_raw,
                "raw_format": self.raw_format,
            },
            "is_camera_image": False,
            "exif_data": {},
            "shutter_count": None,
            "gps": {},
            "errors": [],
        }

        logger.info(
            f"[相机EXIF] 开始分析: {self.file_name} "
            f"(大小: {result['file_info']['size_mb']}MB, "
            f"RAW: {self.is_raw}, 格式: {self.file_ext})"
        )

        # Step 1: 先用 PIL 提取基础 EXIF（含 img.info 原始字节解析）
        pil_data = self._extract_pil_exif()
        pil_keys = [k for k in pil_data if not k.startswith("_")]
        logger.info(f"[相机EXIF] PIL步骤: {len(pil_keys)} 标签, 全部键: {sorted([k for k in pil_data if not k.startswith('_')])}")
        if pil_data:
            result["exif_data"].update(pil_data)

        # Step 2: 用 exifread 直接解析文件补充
        exifread_data = self._extract_exifread()
        exifread_keys = [k for k in exifread_data if not k.startswith("_")]
        logger.info(f"[相机EXIF] exifread步骤: {len(exifread_keys)} 标签: {exifread_keys[:10]}")
        if exifread_data:
            for k, v in exifread_data.items():
                if k not in result["exif_data"] or result["exif_data"].get(k, "") == "":
                    result["exif_data"][k] = v

        # 判断是否为相机图片（多重指标，不只依赖 Make）
        result["is_camera_image"] = self._is_camera_image(result["exif_data"])

        # Step 3: RAW 文件回退 — rawpy
        if self.is_raw and not result["is_camera_image"] and HAS_RAWPY:
            raw_data = self._extract_rawpy()
            if raw_data:
                result["exif_data"].update(raw_data)
                result["is_camera_image"] = True

        # Step 4: 提取快门次数
        if result["is_camera_image"]:
            try:
                makernote_raw = result.get("_raw_makernote", b"")
                if not isinstance(makernote_raw, bytes):
                    makernote_raw = str(makernote_raw).encode("utf-8", errors="replace")
                result["shutter_count"] = self._extract_shutter_count(
                    result["exif_data"].get("Make", ""),
                    makernote_raw,
                )
                if result["shutter_count"]:
                    logger.info(f"[相机EXIF] 快门次数: {result['shutter_count']}")
            except Exception as e:
                logger.warning(f"[相机EXIF] 快门提取整体失败: {e}")

        # Step 5: 格式化 GPS
        result["gps"] = self._extract_gps(result["exif_data"])

        total_keys = len([k for k in result["exif_data"] if not k.startswith("_")])
        logger.info(
            f"[相机EXIF] 分析完成: is_camera={result['is_camera_image']}, "
            f"总标签数={total_keys}, 快门={result['shutter_count']}"
        )

        return result

    @staticmethod
    def _is_camera_image(exif_data: dict[str, Any]) -> bool:
        """判断是否为相机拍摄的图片（多重指标）"""
        # 强信号：相机品牌/型号/机身序列号
        strong_signals = ("Make", "Model", "BodySerialNumber", "LensModel", "LensMake")
        for key in strong_signals:
            val = exif_data.get(key, "")
            if val and val.strip() and len(val.strip()) > 1:
                return True

        # 中等信号：至少3个相机独有参数
        camera_params = (
            "FNumber", "ExposureTime", "ISOSpeedRatings",
            "FocalLength", "FocalLengthIn35mmFilm",
            "ExposureProgram", "MeteringMode", "WhiteBalance",
            "Flash", "ExposureBiasValue",
            "LensSerialNumber", "LensSpecification",
        )
        count = sum(1 for k in camera_params if exif_data.get(k, ""))
        return count >= 3

    def _extract_pil_exif(self) -> dict[str, Any]:
        """使用 PIL 提取 EXIF 数据"""
        data: dict[str, Any] = {}
        if not HAS_PIL:
            return data

        try:
            img = PILImage.open(self.file_path)
            img.load()

            data["ImageWidth"] = str(img.width)
            data["ImageLength"] = str(img.height)

            # 方法1: PIL getexif()
            exif = img.getexif()
            if exif and len(exif) > 0:
                logger.info(f"[相机EXIF] PIL getexif() 获取到 {len(exif)} 个标签")
                for tag_id, value in exif.items():
                    tag_name = EXIF_TAGS.get(tag_id, f"Tag_{tag_id}")
                    if tag_name == "MakerNote":
                        data["_raw_makernote"] = (
                            value if isinstance(value, bytes) else str(value).encode()
                        )
                        continue
                    if tag_name == "UserComment":
                        try:
                            if isinstance(value, bytes):
                                value = value.decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    data[tag_name] = _clean_tag_value(value)

            # 方法2: 从 img.info 中解析原始 EXIF 字节 (核心修复)
            if not data.get("Make") and hasattr(img, "info"):
                raw_exif = img.info.get("exif") or img.info.get("Exif") or img.info.get("EXIF")
                if raw_exif and isinstance(raw_exif, bytes) and len(raw_exif) > 20:
                    logger.info(f"[相机EXIF] img.info有 {len(raw_exif)} 字节原始EXIF")
                    parsed = self._parse_raw_exif_bytes(raw_exif)
                    if parsed:
                        data.update(parsed)
                        logger.info(f"[相机EXIF] 原始EXIF解析成功: {len(parsed)} 标签")

            img.close()

        except Exception as e:
            logger.warning(f"[相机EXIF] PIL解析异常: {e}")
            data["_pil_error"] = str(e)

        return data

    def _parse_raw_exif_bytes(self, raw_exif: bytes) -> dict[str, Any]:
        """从原始 EXIF 字节数据中解析标签（处理 JPEG APP1 包装和 TIFF 格式）"""
        data: dict[str, Any] = {}
        logger.info(f"[相机EXIF] 原始字节: {len(raw_exif)}B, 前8B hex: {raw_exif[:8].hex()}")

        # 剥离 JPEG APP1 包装头 "Exif\x00\x00"（6字节）
        tiff_data = raw_exif
        if raw_exif[:6] == b"Exif\x00\x00":
            tiff_data = raw_exif[6:]
        elif raw_exif[:4] == b"Exif":
            tiff_data = raw_exif[4:]

        logger.info(f"[相机EXIF] 剥离前缀后 {len(tiff_data)}B, 前4B hex: {tiff_data[:4].hex()}")

        # 扫描 TIFF 头 (MM\x00\x2A for big-endian, II\x2A\x00 for little-endian)
        # 多种可能的偏移位置
        offsets_to_try: list[int] = [0]
        for scan_offset in range(len(tiff_data) - 4):
            chunk = tiff_data[scan_offset:scan_offset + 4]
            if chunk in (b"MM\x00\x2a", b"II\x2a\x00", b"MM\x00*", b"II*\x00"):
                if scan_offset > 0:
                    offsets_to_try.append(scan_offset)
                break
        # 去重并排序
        offsets_to_try = sorted(set(offsets_to_try))

        for offset in offsets_to_try:
            candidate = tiff_data[offset:]
            if len(candidate) < 10:
                continue
            hdr = candidate[:2]
            if hdr not in (b"MM", b"II"):
                continue

            logger.info(f"[相机EXIF] 偏移{offset}: TIFF头={candidate[:4].hex()}, 剩余{len(candidate)}B")
            try:
                import exifread
                from io import BytesIO
                stream = BytesIO(candidate)
                tags = exifread.process_file(stream, details=True, debug=False)
                tag_count = len(tags)
                if tag_count >= 2:  # 至少要有有意义的标签
                    for tag_name, tag_value in tags.items():
                        if tag_name in ("JPEGThumbnail", "TIFFThumbnail"):
                            continue
                        short_name = tag_name.split()[-1] if " " in tag_name else tag_name
                        val = str(tag_value)
                        if short_name in ("FNumber", "FocalLength", "ExposureTime",
                                          "ExposureBiasValue", "ISOSpeedRatings"):
                            try:
                                if hasattr(tag_value, "values") and tag_value.values:
                                    val = _format_fraction(tag_value.values[0])
                            except Exception:
                                pass
                        data[short_name] = val
                    logger.info(f"[相机EXIF] 偏移{offset}成功解析 {tag_count} 标签: {list(data.keys())[:10]}")
                    # 调试：打印所有键名，检查是否有 Make/Model 相关
                    all_keys = sorted(data.keys())
                    logger.info(f"[相机EXIF] 全部标签键({len(all_keys)}): {all_keys}")
                    break
                else:
                    logger.info(f"[相机EXIF] 偏移{offset}仅解析到 {tag_count} 标签(太少,跳过)")
            except Exception as e:
                logger.warning(f"[相机EXIF] 偏移{offset}解析异常: {type(e).__name__}: {e}")

        if not data:
            logger.warning(f"[相机EXIF] 所有偏移都未能解析EXIF")

        return data

    def _extract_exifread(self) -> dict[str, Any]:
        """使用 exifread 直接解析文件"""
        data: dict[str, Any] = {}
        try:
            import exifread
            with open(self.file_path, "rb") as f:
                tags = exifread.process_file(f, details=True, debug=False)

            logger.info(f"[相机EXIF] exifread直接解析: {len(tags)} 个标签, 原始键: {sorted(tags.keys())[:20]}")
            logger.info(f"[相机EXIF] exifread处理后键: {sorted(data.keys())}")

            for tag_name, tag_value in tags.items():
                if tag_name in ("JPEGThumbnail", "TIFFThumbnail"):
                    continue
                if "MakerNote" in tag_name:
                    if hasattr(tag_value, "values"):
                        data["_raw_makernote"] = str(tag_value.values).encode()
                    continue
                short_name = tag_name.split()[-1] if " " in tag_name else tag_name
                val = str(tag_value)
                try:
                    if short_name in ("FNumber", "FocalLength", "ExposureTime",
                                      "ExposureBiasValue", "ISOSpeedRatings"):
                        if hasattr(tag_value, "values") and tag_value.values:
                            val = _format_fraction(tag_value.values[0])
                except Exception:
                    pass
                data[short_name] = val

        except Exception as e:
            logger.warning(f"[相机EXIF] exifread直接解析异常: {e}")
            data["_exifread_error"] = str(e)

        return data

    def _extract_rawpy(self) -> dict[str, Any]:
        """使用 rawpy 解析 RAW 文件"""
        data: dict[str, Any] = {}
        if not HAS_RAWPY:
            return data

        timeout = self.config.get("raw_format_config", {}).get("raw_timeout_seconds", 30)

        def _do_parse():
            try:
                with rawpy.imread(self.file_path) as raw:
                    data["Make"] = str(raw.camera_whitebalance[0]) if raw.camera_whitebalance else ""
                    # black_level 和 white_level 可以作为元数据的一部分
                    data["_rawpy_black_level"] = str(raw.black_level_per_channel[:4])
                    data["_rawpy_white_level"] = str(raw.camera_white_level)
                    data["ImageWidth"] = str(raw.sizes.width)
                    data["ImageLength"] = str(raw.sizes.height)
                    data["Software"] = "RAW File (rawpy parsed)"
            except Exception as e:
                data["_rawpy_error"] = str(e)
            return data

        try:
            loop = asyncio.get_event_loop()
            return loop.run_in_executor(None, _do_parse)
            # Note: 这里同步调用，因为 rawpy 不支持异步
            # 在实际使用中通过 asyncio.to_thread 包装
            return _do_parse()
        except Exception as e:
            data["_rawpy_error"] = str(e)
            return data

    def _extract_shutter_count(
        self, make: str, makernote_raw: bytes
    ) -> str | None:
        """从 MakerNote 中提取快门次数（不依赖 Make/Model 字段）"""
        # 快门关键词
        SC_KEYWORDS = [
            "shuttercount", "shutter count", "shutter",
            "imagecount", "image count", "image number",
            "totalpictures", "total pictures",
            "totalshutterreleases", "total shutter releases",
            "totalshutter", "total shutter",
            "mechanicalshuttercount",
        ]

        try:
            import exifread
            with open(self.file_path, "rb") as f:
                tags = exifread.process_file(f, details=True)

            mn_entries: list[tuple[str, str]] = []
            binary_makernote: bytes = b""
            for tag_name, tag_value in tags.items():
                if "MakerNote" not in tag_name:
                    continue
                if not hasattr(tag_value, "values"):
                    continue
                vals = tag_value.values
                if isinstance(vals, dict):
                    # dict: 正常 IFD 结构，每个键值对是一个标签
                    for k, v in vals.items():
                        mn_entries.append((str(k), str(v)))
                elif isinstance(vals, (list, tuple)):
                    # list: 原始二进制 MakerNote(Nikon/Sony等)，不逐字节处理
                    try:
                        binary_makernote = bytes(vals)
                    except Exception:
                        binary_makernote = b"".join(bytes([int(b) if isinstance(b, int) else b]) if isinstance(b, int) else b for b in vals if isinstance(b, (int, bytes)))
                else:
                    mn_entries.append(("raw", str(vals)))

            logger.info(f"[相机EXIF] MakerNote: {len(mn_entries)}条键值对 + {len(binary_makernote)}字节二进制数据")

            # 策略1: 关键字匹配(仅dict结构的键值对)
            for k_str, v_str in mn_entries:
                kl = k_str.lower()
                for kw in SC_KEYWORDS:
                    if kw in kl:
                        try:
                            return str(int(float(v_str)))
                        except ValueError:
                            return v_str

            # 策略2: 厂商标签ID匹配(仅dict结构)
            for k_str, v_str in mn_entries:
                for tag_ids in SHUTTER_COUNT_TAGS.values():
                    for tag_id in tag_ids:
                        if isinstance(tag_id, int) and str(tag_id) in k_str:
                            try:
                                return str(int(float(v_str)))
                            except ValueError:
                                return v_str
                        if isinstance(tag_id, str) and tag_id.lower() in k_str.lower():
                            try:
                                return str(int(float(v_str)))
                            except ValueError:
                                return v_str

            # 策略3: 二进制 NIKON MakerNote 解析
            # Nikon MakerNote 格式: "Nikon\x00" + TIFF IFD，快门标签 0x00A7
            if binary_makernote and len(binary_makernote) > 100:
                # 尝试找到 "Nikon" 头并解析后续 IFD
                nikon_idx = binary_makernote.find(b"Nikon")
                if nikon_idx >= 0:
                    tiff_start = nikon_idx + 8  # 跳过 "Nikon\x00\x01\x00" 或类似头
                    if tiff_start + 8 < len(binary_makernote):
                        byte_order = binary_makernote[tiff_start:tiff_start+4]
                        logger.info(f"[相机EXIF] Nikon MakerNote TIFF头: {byte_order.hex()}")
                        # 尝试搜索原始字节中的数字模式
                        text = binary_makernote.decode("latin-1", errors="ignore")
                        # Nikon 快门通常跟在特定字节模式后
                        # 搜索 0x00A7 标记附近的数字
                        for pattern in [b"\xa7\x00", b"\x00\xa7"]:
                            pidx = binary_makernote.find(pattern)
                            if pidx >= 0:
                                nearby = binary_makernote[pidx:pidx+20]
                                logger.info(f"[相机EXIF] 标签0x00A7在偏移{pidx}, 附近: {nearby.hex()}")
                                # 尝试解析标签值(Long/SRational)
                                try:
                                    val = struct.unpack(">I", binary_makernote[pidx+8:pidx+12])
                                    if 100 <= val[0] <= 9999999:
                                        logger.info(f"[相机EXIF] Nikon标签0x00A7解析: {val[0]}")
                                        return str(val[0])
                                    val = struct.unpack("<I", binary_makernote[pidx+8:pidx+12])
                                    if 100 <= val[0] <= 9999999:
                                        logger.info(f"[相机EXIF] Nikon标签0x00A7(LE)解析: {val[0]}")
                                        return str(val[0])
                                except Exception:
                                    pass

        except Exception as e:
            logger.warning(f"[相机EXIF] 快门次数提取异常: {e}")

        # 备用：从 makernote raw bytes + binary data 中搜索关键词+数字
        all_raw = (makernote_raw or b"") + binary_makernote if binary_makernote else (makernote_raw or b"")
        if len(all_raw) > 100:
            try:
                text = all_raw.decode("latin-1", errors="ignore")
                for keyword in ["ShutterCount", "ImageCount", "Shutter", "Total"]:
                    idx = text.find(keyword)
                    if idx >= 0 and idx < len(text) - 5:
                        snippet = text[idx:idx + 100]
                        match = re.search(r"(\d{3,7})", snippet)
                        if match:
                            sc = match.group(1)
                            if 100 <= int(sc) <= 9999999:
                                logger.info(f"[相机EXIF] 关键词'{keyword}'附近找到: {sc}")
                                return sc
            except Exception:
                pass

        return None

    def _extract_gps(self, exif_data: dict[str, Any]) -> dict[str, Any]:
        """提取并格式化 GPS 信息"""
        gps: dict[str, Any] = {}

        # 从 exifread 标签
        lat = None
        lon = None
        lat_ref = exif_data.get("GPS GPSLatitudeRef", exif_data.get("GPSLatitudeRef", ""))
        lon_ref = exif_data.get("GPS GPSLongitudeRef", exif_data.get("GPSLongitudeRef", ""))
        lat_raw = exif_data.get("GPS GPSLatitude", exif_data.get("GPSLatitude"))
        lon_raw = exif_data.get("GPS GPSLongitude", exif_data.get("GPSLongitude"))
        alt_raw = exif_data.get("GPS GPSAltitude", exif_data.get("GPSAltitude"))

        if lat_raw and lon_raw:
            try:
                if hasattr(lat_raw, "values"):
                    lat = _convert_to_degrees(lat_raw.values)
                if hasattr(lon_raw, "values"):
                    lon = _convert_to_degrees(lon_raw.values)

                if lat is not None and lon is not None:
                    if lat_ref and "S" in str(lat_ref).upper():
                        lat = -lat
                    if lon_ref and "W" in str(lon_ref).upper():
                        lon = -lon

                    gps["latitude"] = round(lat, 6)
                    gps["longitude"] = round(lon, 6)

                    if alt_raw:
                        try:
                            gps["altitude"] = round(
                                _rational_to_float(
                                    alt_raw.values[0] if hasattr(alt_raw, "values") else alt_raw
                                ) or 0,
                                1,
                            )
                        except Exception:
                            pass

                    # 生成地图链接
                    gps["map_url"] = (
                        f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
                    )
            except Exception:
                pass

        # 从 PIL GPS 标签
        if not gps:
            lat_raw = exif_data.get("GPS_GPSLatitude")
            lon_raw = exif_data.get("GPS_GPSLongitude")
            lat_ref = exif_data.get("GPS_GPSLatitudeRef", "")
            lon_ref = exif_data.get("GPS_GPSLongitudeRef", "")

            if lat_raw and lon_raw:
                try:
                    lat = _convert_to_degrees(lat_raw)
                    lon = _convert_to_degrees(lon_raw)
                    if lat_ref and "S" in str(lat_ref).upper():
                        lat = -lat
                    if lon_ref and "W" in str(lon_ref).upper():
                        lon = -lon
                    gps["latitude"] = round(lat, 6)
                    gps["longitude"] = round(lon, 6)
                    gps["map_url"] = (
                        f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
                    )
                except Exception:
                    pass

        return gps

    # config key → EXIF tag 映射
    CONFIG_TO_TAG: dict[str, str] = {
        "camera_make": "Make", "camera_model": "Model",
        "lens_model": "LensModel", "focal_length": "FocalLength",
        "aperture": "FNumber", "shutter_speed": "ExposureTime",
        "iso": "ISOSpeedRatings", "exposure_mode": "ExposureProgram",
        "white_balance": "WhiteBalance", "metering_mode": "MeteringMode",
        "flash": "Flash", "exposure_compensation": "ExposureBiasValue",
        "date_time": "DateTimeOriginal", "image_size": "ImageWidth",
        "gps": "gps", "software": "Software",
        "shutter_count": "shutter_count", "serial_number": "BodySerialNumber",
    }

    @staticmethod
    def format_display_text(
        result: dict[str, Any], config_fields: dict[str, bool] | None = None
    ) -> str:
        """将分析结果格式化为用于显示的文本"""
        # 将 config key 转为 EXIF tag 开关映射
        display_fields: dict[str, bool] = {}
        if config_fields:
            for cfg_key, enabled in config_fields.items():
                tag = ExifAnalyzer.CONFIG_TO_TAG.get(cfg_key, cfg_key)
                display_fields[tag] = enabled

        file_info = result.get("file_info", {})
        exif = result.get("exif_data", {})
        sc = result.get("shutter_count")
        gps = result.get("gps", {})
        errors = result.get("errors", [])

        lines: list[str] = []
        lines.append("📸 图片 EXIF 分析结果")
        lines.append("─" * 32)

        # 文件信息
        if file_info.get("is_raw"):
            lines.append(f"📁 文件: {file_info.get('name', '')} ({file_info.get('raw_format', 'RAW')})")
        else:
            lines.append(f"📁 文件: {file_info.get('name', '')}")
        lines.append(f"📦 大小: {file_info.get('size_mb', 0)} MB")

        if not result.get("is_camera_image"):
            lines.append("")
            lines.append("⚠️ 该图片不包含相机 EXIF 数据")
            lines.append("   可能来源：截图、网络下载、手机App生成等")
            if errors:
                lines.append(f"   {errors[0]}")
            return "\n".join(lines)

        # 分隔线
        sep_count = 0
        shown_labels: set[str] = set()

        def add_field(tag: str, cn_name: str, value_transform=None):
            nonlocal sep_count
            if not display_fields.get(tag, True):
                return
            val = exif.get(tag, "")
            if not val:
                return
            if value_transform:
                val = value_transform(val)
            if tag in shown_labels:
                return
            shown_labels.add(tag)
            if sep_count == 0:
                lines.append("─" * 32)
            lines.append(f"  {cn_name}: {val}")
            sep_count += 1

        # 相机信息
        add_field("Make", self.TAG_NAMES_CN.get("Make", "相机品牌"))
        add_field("Model", self.TAG_NAMES_CN.get("Model", "相机型号"))

        # 镜头信息
        add_field("LensModel", self.TAG_NAMES_CN.get("LensModel", "镜头型号"))
        add_field("LensMake", self.TAG_NAMES_CN.get("LensMake", "镜头品牌"))

        # 拍摄参数
        add_field("FocalLength", self.TAG_NAMES_CN.get("FocalLength", "焦距"),
                  lambda v: f"{_format_fraction(v.split(',')[0] if ',' in v else v)}mm")
        add_field("FocalLengthIn35mmFilm", self.TAG_NAMES_CN.get("FocalLengthIn35mmFilm", "35mm等效焦距"),
                  lambda v: f"{v}mm")
        add_field("FNumber", self.TAG_NAMES_CN.get("FNumber", "光圈值"),
                  lambda v: f"f/{_format_fraction(v)}")
        add_field("ExposureTime", self.TAG_NAMES_CN.get("ExposureTime", "快门速度"),
                  lambda v: f"{v}s")
        add_field("ISOSpeedRatings", self.TAG_NAMES_CN.get("ISOSpeedRatings", "ISO感光度"))
        add_field("ExposureBiasValue", self.TAG_NAMES_CN.get("ExposureBiasValue", "曝光补偿"),
                  lambda v: f"{v} EV")
        add_field("ExposureProgram", self.TAG_NAMES_CN.get("ExposureProgram", "曝光模式"))
        add_field("ExposureMode", self.TAG_NAMES_CN.get("ExposureMode", "曝光模式(Exif)"))

        # 测光与白平衡
        add_field("MeteringMode", self.TAG_NAMES_CN.get("MeteringMode", "测光模式"))
        add_field("WhiteBalance", self.TAG_NAMES_CN.get("WhiteBalance", "白平衡"))

        # 闪光灯
        add_field("Flash", self.TAG_NAMES_CN.get("Flash", "闪光灯"))

        # 图片尺寸
        add_field("ImageWidth", self.TAG_NAMES_CN.get("ImageWidth", "图片宽度"),
                  lambda v: f"{v} × {exif.get('ImageLength', '?')} px")

        # 时间
        add_field("DateTimeOriginal", self.TAG_NAMES_CN.get("DateTimeOriginal", "原始拍摄时间"))
        add_field("DateTimeDigitized", self.TAG_NAMES_CN.get("DateTimeDigitized", "数字化时间"))
        add_field("DateTime", self.TAG_NAMES_CN.get("DateTime", "拍摄时间"))

        # 处理软件
        add_field("Software", self.TAG_NAMES_CN.get("Software", "处理软件"))

        # 序列号
        add_field("BodySerialNumber", self.TAG_NAMES_CN.get("BodySerialNumber", "机身序列号"))
        add_field("SerialNumber", self.TAG_NAMES_CN.get("SerialNumber", "序列号"))

        # 作者/版权
        add_field("Artist", self.TAG_NAMES_CN.get("Artist", "作者"))
        add_field("Copyright", self.TAG_NAMES_CN.get("Copyright", "版权"))

        # 快门次数
        if display_fields.get("shutter_count", True) and sc:
            lines.append("─" * 32)
            lines.append(f"  📷 快门次数: {sc}")

        # GPS 信息
        if display_fields.get("gps", False) and gps:
            lines.append("─" * 32)
            lines.append("  📍 GPS信息:")
            if gps.get("latitude"):
                lines.append(f"    纬度: {gps['latitude']}°")
            if gps.get("longitude"):
                lines.append(f"    经度: {gps['longitude']}°")
            if gps.get("altitude"):
                lines.append(f"    海拔: {gps['altitude']}m")
            if gps.get("map_url"):
                lines.append(f"    🗺️ {gps['map_url']}")

        if not sep_count and not sc:
            lines.append("  ⚠️ 未能提取到EXIF字段数据")

        return "\n".join(lines)

    @staticmethod
    def format_full_exif_text(result: dict[str, Any]) -> str:
        """格式化完整的 EXIF 数据（用于 /exif 指令）"""
        exif = result.get("exif_data", {})
        file_info = result.get("file_info", {})
        sc = result.get("shutter_count")
        gps = result.get("gps", {})

        lines: list[str] = []
        lines.append("📸 完整 EXIF 元数据")
        lines.append("═" * 36)

        if file_info.get("is_raw"):
            lines.append(f"文件: {file_info.get('name')} ({file_info.get('raw_format')})")
        else:
            lines.append(f"文件: {file_info.get('name')}")
        lines.append(f"大小: {file_info.get('size_mb')} MB")

        if not result.get("is_camera_image"):
            lines.append("⚠️ 该图片不含相机EXIF数据")
            return "\n".join(lines)

        # 所有字段（按分类分组）
        categories = [
            ("📷 相机信息", ["Make", "Model", "BodySerialNumber", "SerialNumber"]),
            ("🔭 镜头信息", ["LensModel", "LensMake"]),
            ("⚙️ 拍摄参数", [
                "FocalLength", "FocalLengthIn35mmFilm", "FNumber",
                "ExposureTime", "ISOSpeedRatings", "ExposureBiasValue",
                "ExposureProgram", "ExposureMode",
            ]),
            ("🎯 高级设置", [
                "MeteringMode", "WhiteBalance", "Flash",
                "ColorSpace", "SceneCaptureType",
            ]),
            ("🖼️ 图片属性", [
                "ImageWidth", "ImageLength", "Orientation",
                "Software",
            ]),
            ("📅 时间信息", [
                "DateTimeOriginal", "DateTimeDigitized", "DateTime",
            ]),
            ("👤 版权信息", ["Artist", "Copyright"]),
        ]

        for cat_name, tags in categories:
            items = []
            for tag in tags:
                val = exif.get(tag, "")
                if val:
                    cn = ExifAnalyzer.TAG_NAMES_CN.get(tag, tag)
                    items.append(f"  {cn}: {val}")
            if items:
                lines.append("─" * 36)
                lines.append(cat_name)
                lines.extend(items)

        # 快门次数
        if sc:
            lines.append("─" * 36)
            lines.append(f"📷 快门次数: {sc}")

        # GPS
        if gps:
            lines.append("─" * 36)
            lines.append("📍 GPS信息:")
            for k, v in gps.items():
                if k != "map_url":
                    lines.append(f"  {k}: {v}")
            if gps.get("map_url"):
                lines.append(f"  🗺️ {gps['map_url']}")

        # 其他所有未列出的标签
        all_listed = set()
        for _, tags in categories:
            all_listed.update(tags)
        others = {k: v for k, v in exif.items() if k not in all_listed and not k.startswith("_") and not k.startswith("GPS") and k not in ("MakerNote", "UserComment")}
        if others:
            lines.append("─" * 36)
            lines.append("📋 其他标签:")
            for k, v in others.items():
                cn = ExifAnalyzer.TAG_NAMES_CN.get(k, k)
                lines.append(f"  {cn}: {v}")

        return "\n".join(lines)

    @staticmethod
    def format_shutter_only(result: dict[str, Any]) -> str:
        """仅返回快门次数信息"""
        sc = result.get("shutter_count")
        exif = result.get("exif_data", {})
        make = exif.get("Make", "")
        model = exif.get("Model", "")

        if not result.get("is_camera_image"):
            return "⚠️ 该图片不含相机EXIF数据，无法获取快门次数"

        lines = ["📷 快门次数查询"]
        lines.append("─" * 20)
        if make or model:
            camera_info = f"{make} {model}".strip()
            lines.append(f"相机: {camera_info}")
        if sc:
            lines.append(f"📸 快门次数: {sc}")
        else:
            lines.append("⚠️ 未能从该图片提取快门次数")
            lines.append("   可能原因：")
            lines.append("   • 该相机型号不支持在EXIF中记录快门次数")
            lines.append("   • 图片经过后期处理丢失了MakerNote数据")
            lines.append("   • 相机厂商将快门次数存储在非标准标签中")
        return "\n".join(lines)


# ============================================================
# 插件主类
# ============================================================


class CameraExifPlugin(Star):
    """相机EXIF分析插件 —— 自动检测图片EXIF、RAW格式解析、快门次数提取"""

    # ── 安全常量 ──
    _TEMP_DIR_PREFIX: str = ""  # 启动时从第一个下载路径推断
    _ANALYSIS_TIMEOUT: int = 45  # 单次分析超时(秒)
    _RATE_LIMIT_WINDOW: float = 10.0  # 频率限制窗口(秒)
    _RATE_LIMIT_MAX: int = 3  # 窗口内最大分析次数
    _RATE_LIMIT_COMMAND_MAX: int = 5  # 窗口内最大指令次数

    # ── 字段查询指令映射（仅用于帮助菜单生成） ──
    FIELD_COMMAND_MAP: dict[str, str] = {
        "快门次数": "shutter_count",
        "快门次数查询": "shutter_count",
        "快门": "ExposureTime",
        "快门速度": "ExposureTime",
        "曝光时间": "ExposureTime",
        "相机型号": "Model",
        "相机型号查询": "Model",
        "相机品牌": "Make",
        "相机品牌查询": "Make",
        "镜头型号": "LensModel",
        "镜头型号查询": "LensModel",
        "镜头品牌": "LensMake",
        "镜头品牌查询": "LensMake",
        "焦距": "FocalLength",
        "焦距查询": "FocalLength",
        "光圈": "FNumber",
        "光圈查询": "FNumber",
        "ISO查询": "ISOSpeedRatings",
        "测光模式": "MeteringMode",
        "测光模式查询": "MeteringMode",
        "曝光模式": "ExposureProgram",
        "曝光模式查询": "ExposureProgram",
        "曝光补偿": "ExposureBiasValue",
        "曝光补偿查询": "ExposureBiasValue",
        "闪光灯": "Flash",
        "闪光灯查询": "Flash",
        "白平衡": "WhiteBalance",
        "白平衡查询": "WhiteBalance",
        "拍摄时间": "DateTimeOriginal",
        "拍摄时间查询": "DateTimeOriginal",
        "机身序列号": "BodySerialNumber",
        "机身序列号查询": "BodySerialNumber",
        "图片尺寸": "image_size",
        "图片尺寸查询": "image_size",
        "处理软件": "Software",
        "处理软件查询": "Software",
        "GPS": "gps",
        "GPS查询": "gps",
    }
    FIELD_CN_NAMES: dict[str, str] = {
        "shutter_count": "快门次数", "Make": "相机品牌", "Model": "相机型号",
        "LensModel": "镜头型号", "LensMake": "镜头品牌",
        "FocalLength": "焦距", "FNumber": "光圈值", "ExposureTime": "快门速度",
        "ISOSpeedRatings": "ISO感光度", "ExposureBiasValue": "曝光补偿",
        "ExposureProgram": "曝光模式", "MeteringMode": "测光模式",
        "Flash": "闪光灯", "WhiteBalance": "白平衡",
        "DateTimeOriginal": "拍摄时间", "BodySerialNumber": "机身序列号",
        "Software": "处理软件", "image_size": "图片尺寸", "gps": "GPS信息",
    }

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config: AstrBotConfig = config
        # 权限隔离缓存: {"{platform}:{type}:{session_id}::{user_id}" → {"result":..., "timestamp":...}}
        self._last_results: dict[str, dict[str, Any]] = {}
        self._max_cache_size = 200
        # 频率限制: {user_id: [timestamps]}
        self._rate_limits: dict[str, list[float]] = {}
        self._cmd_rate_limits: dict[str, list[float]] = {}

    # ── 生命周期 ──

    async def initialize(self) -> None:
        if not self.config.get("enabled", True):
            logger.info("[相机EXIF] 插件已禁用")
            return
        deps = []
        deps.append("Pillow ✓" if HAS_PIL else "Pillow ✗")
        deps.append("exifread ✓" if HAS_EXIFREAD else "exifread ✗")
        deps.append("rawpy ✓" if HAS_RAWPY else "rawpy ✗ (RAW深度解析不可用)")
        logger.info(f"[相机EXIF] 插件已激活 | 依赖: {', '.join(deps)}")
        logger.info(f"[相机EXIF] 自动检测: {'开启' if self.config.get('auto_detect_enabled', True) else '关闭'}")

    async def terminate(self) -> None:
        self._last_results.clear()
        self._rate_limits.clear()
        self._cmd_rate_limits.clear()
        logger.info("[相机EXIF] 插件已停止")

    # ── 频率限制（防刷屏/DoS） ──

    def _check_rate_limit(self, user_id: str, is_command: bool = False) -> bool:
        """检查是否超过频率限制，返回 True=允许, False=拒绝"""
        now = time.time()
        bucket = self._cmd_rate_limits if is_command else self._rate_limits
        window = self._RATE_LIMIT_WINDOW
        max_count = self._RATE_LIMIT_COMMAND_MAX if is_command else self._RATE_LIMIT_MAX

        if user_id not in bucket:
            bucket[user_id] = []
        # 清理过期记录
        bucket[user_id] = [t for t in bucket[user_id] if now - t < window]
        if len(bucket[user_id]) >= max_count:
            logger.warning(f"[相机EXIF] 频率限制: {user_id} ({len(bucket[user_id])}/{max_count} in {window}s)")
            return False
        bucket[user_id].append(now)
        return True

    # ── 权限隔离缓存 ──

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        """生成权限隔离缓存Key: 会话 + 用户"""
        session = str(event.session)
        user_id = event.get_sender_id()
        return f"{session}::{user_id}"

    def _cache_result(self, event: AstrMessageEvent, result: dict[str, Any]) -> None:
        key = self._get_cache_key(event)
        self._last_results[key] = {"result": result, "timestamp": time.time()}
        if len(self._last_results) > self._max_cache_size:
            sorted_items = sorted(self._last_results.items(), key=lambda x: x[1]["timestamp"])
            for k, _ in sorted_items[:len(sorted_items) - self._max_cache_size]:
                del self._last_results[k]

    def _get_cached_result(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        key = self._get_cache_key(event)
        cached = self._last_results.get(key)
        if cached and time.time() - cached["timestamp"] < 300:
            return cached["result"]
        if cached:
            del self._last_results[key]
        return None

    # ── 黑白名单检查 ──

    def _check_access(self, event: AstrMessageEvent) -> tuple[bool, str]:
        if not self.config.get("enabled", True):
            return False, "插件已禁用"
        msg_type = event.get_message_type()
        if msg_type == MessageType.GROUP_MESSAGE:
            fc = self.config.get("group_chat_filter", {})
            mode = fc.get("mode", "all")
            gid = str(event.get_group_id())
            if mode == "whitelist" and gid not in [str(g) for g in fc.get("whitelist", [])]:
                return False, "该群不在白名单中"
            if mode == "blacklist" and gid in [str(g) for g in fc.get("blacklist", [])]:
                return False, "该群在黑名单中"
            return True, ""
        else:
            fc = self.config.get("private_chat_filter", {})
            mode = fc.get("mode", "all")
            uid = event.get_sender_id()
            if mode == "whitelist" and uid not in [str(u) for u in fc.get("whitelist", [])]:
                return False, "您不在白名单中"
            if mode == "blacklist" and uid in [str(u) for u in fc.get("blacklist", [])]:
                return False, "您在黑名单中"
            return True, ""

    def _format_reply(self, event: AstrMessageEvent, text: str) -> MessageChain:
        """根据 reply_mode 配置生成回复消息"""
        reply_mode = self.config.get("reply_mode", "文本发送")
        forward_name = self.config.get("forward_display_name", "相机EXIF分析")
        if reply_mode == "转发发送":
            node = Node(uin=str(event.get_self_id() or "10000"), name=forward_name, content=[MsgPlain(text)])
            return event.chain_result([node])
        return event.plain_result(text)

    # ── 文件类型检测 ──

    @staticmethod
    def _is_processable_image(msg_component) -> bool:
        """判断消息组件是否是可处理的图片（Image 或带图片/RAW扩展名的 File）"""
        if isinstance(msg_component, CompImage):
            return True
        if isinstance(msg_component, File):
            fname = getattr(msg_component, "name", "") or ""
            furl = getattr(msg_component, "url", "") or ""
            ffile = getattr(msg_component, "file_", "") or ""
            logger.info(f"[相机EXIF] File组件: name={fname!r}, url={furl[:80] if furl else ''!r}, file_={ffile!r}")
            for candidate in (fname, furl, ffile):
                if candidate:
                    ext = os.path.splitext(candidate)[1].lower()
                    if ext in ALL_IMAGE_EXTS:
                        return True
            # 文件名未知也尝试处理
            return True
        return False

    @staticmethod
    async def _get_file_path(comp) -> str | None:
        """从 Image 或 File 组件获取本地文件路径"""
        if isinstance(comp, CompImage):
            return await comp.convert_to_file_path()
        if isinstance(comp, File):
            return await comp.get_file()
        return None

    # ── 图片清理（含路径安全校验） ──

    @staticmethod
    def _cleanup_temp_image(file_path: str) -> None:
        """安全删除临时图片文件，仅限 AstrBot temp 目录"""
        try:
            if not file_path or not os.path.isfile(file_path):
                return
            # 路径安全校验：必须位于 AstrBot 临时目录下
            abs_path = os.path.abspath(file_path)
            from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
            temp_dir = os.path.abspath(get_astrbot_temp_path())
            if not abs_path.startswith(temp_dir):
                logger.warning(f"[相机EXIF] 拒绝删除非临时目录文件: {abs_path}")
                return
            os.remove(file_path)
            logger.debug(f"[相机EXIF] 已清理临时图片: {os.path.basename(file_path)}")
        except Exception:
            pass

    # ── 图片分析核心 ──

    async def _analyze_image(
        self, file_path: str, event: AstrMessageEvent | None = None
    ) -> dict[str, Any] | None:
        # 路径安全校验
        if not file_path or not os.path.isfile(file_path):
            logger.warning(f"[相机EXIF] 无效文件路径: {file_path}")
            return None
        try:
            max_size = self.config.get("max_image_size_mb", 50)
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            if file_size > max_size:
                logger.info(f"[相机EXIF] 图片过大 ({file_size:.1f}MB)，跳过")
                return {
                    "file_info": {"name": os.path.basename(file_path), "size_mb": round(file_size, 2)},
                    "is_camera_image": False, "exif_data": {}, "shutter_count": None,
                    "gps": {}, "errors": [f"图片过大 ({file_size:.1f}MB > {max_size}MB)"],
                }
            config = {"raw_format_config": self.config.get("raw_format_config", {}), "max_image_size_mb": max_size}
            analyzer = ExifAnalyzer(file_path, config=config)
            loop = asyncio.get_event_loop()
            # 带超时的分析，防止大RAW文件阻塞
            result = await asyncio.wait_for(
                loop.run_in_executor(None, analyzer.analyze),
                timeout=self._ANALYSIS_TIMEOUT,
            )
            # 始终缓存（关闭引用时靠缓存查询自己的图片）
            if event:
                self._cache_result(event, result)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[相机EXIF] 分析超时 ({self._ANALYSIS_TIMEOUT}s): {os.path.basename(file_path)}")
            return None
        except Exception as e:
            logger.error(f"[相机EXIF] 分析失败: {e}", exc_info=True)
            return None

    async def _analyze_and_reply_image(
        self, event: AstrMessageEvent, file_path: str, show_analyzing: bool = False
    ) -> AsyncGenerator[MessageChain, None]:
        """分析图片并生成回复。分析完才判断是否为相机图片，避免误提示。"""
        # 先分析
        result = await self._analyze_image(file_path, event)
        if not result:
            self._cleanup_temp_image(file_path)
            return

        # 非相机图片 → 静默跳过
        if not result.get("is_camera_image"):
            self._cleanup_temp_image(file_path)
            return

        # 确认是相机图片后才发提示
        if show_analyzing:
            yield event.plain_result("🔍 检测到相机图片，正在分析 EXIF 数据...")

        # 格式化结果
        fields = self.config.get("display_fields", {})
        show_detailed = self.config.get("show_detailed_exif_default", False)
        if show_detailed:
            text = ExifAnalyzer.format_full_exif_text(result)
        else:
            text = ExifAnalyzer.format_display_text(result, fields)

        # GPS隐私提醒
        if result.get("gps") and fields.get("gps", False):
            text += "\n\n🔒 GPS位置信息已显示，请注意隐私保护"

        # 回复格式
        yield self._format_reply(event, text)

        self._cleanup_temp_image(file_path)

    # ── 消息处理 ──

    async def _process_auto_detect(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageChain, None]:
        if not self.config.get("auto_detect_enabled", True):
            return
        if self.config.get("reply_mode", "文本发送") == "不发送":
            return

        allowed, _ = self._check_access(event)
        if not allowed:
            return

        # 频率限制
        if not self._check_rate_limit(event.get_sender_id(), is_command=False):
            return

        messages = event.get_messages()
        images = [m for m in messages if self._is_processable_image(m)]
        if not images:
            return

        for img in images[:1]:
            try:
                file_path = await self._get_file_path(img)
                if not file_path or not os.path.isfile(file_path):
                    continue

                show_hint = self.config.get("show_analyzing_hint", True)
                async for reply in self._analyze_and_reply_image(event, file_path, show_analyzing=show_hint):
                    yield reply
            except Exception as e:
                logger.error(f"[相机EXIF] 自动检测错误: {e}", exc_info=True)

    async def _process_direct_image(
        self, event: AstrMessageEvent, show_analyzing: bool = False
    ) -> AsyncGenerator[MessageChain, None]:
        """处理消息中直接附带的图片/文件"""
        messages = event.get_messages()
        images = [m for m in messages if self._is_processable_image(m)]
        if not images:
            return
        for img in images[:1]:
            try:
                file_path = await self._get_file_path(img)
                if file_path and os.path.isfile(file_path):
                    async for reply in self._analyze_and_reply_image(event, file_path, show_analyzing=show_analyzing):
                        yield reply
                    return
            except Exception as e:
                logger.error(f"[相机EXIF] 图片分析失败: {e}")

    async def _process_reply_image(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageChain, None]:
        """处理引用回复中的图片"""
        for msg in event.get_messages():
            if not isinstance(msg, Reply):
                continue
            ref_chain = getattr(msg, "chain", []) or getattr(msg, "message", []) or []
            for ref_msg in ref_chain:
                if self._is_processable_image(ref_msg):
                    try:
                        file_path = await self._get_file_path(ref_msg)
                        if file_path and os.path.isfile(file_path):
                            async for reply in self._analyze_and_reply_image(event, file_path):
                                yield reply
                            return
                    except Exception as e:
                        logger.error(f"[相机EXIF] 引用图片分析失败: {e}")
                    return

    # ================================================================
    # 📋 /exif帮助 — 帮助菜单
    # ================================================================

    @filter.command("exif帮助", alias={"exif help", "exif菜单", "exif menu"})
    async def exif_help(self, event: AstrMessageEvent):
        """显示插件帮助菜单。支持 /exif帮助 /exif help /exif菜单 /exif menu"""
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"⚠️ {reason}")
            return
        cmds = "\n".join(
            f"  /{k}  └ 查询{fv}"
            for k, v in sorted(self.FIELD_COMMAND_MAP.items(), key=lambda x: (list(self.FIELD_COMMAND_MAP.values()).index(x[1]), x[0]))
            if not k.endswith("查询") and (fv := self.FIELD_CN_NAMES.get(v, v))
        )
        help_text = f"""📸 相机EXIF分析插件 — 使用帮助
═══════════════════════════════

📋 完整查询：
  /exif帮助  └ 显示本帮助菜单
  /exif      └ 查询我的图片完整EXIF元数据

📊 字段单独查询：
{cmds[:1200]}

📸 支持格式：JPEG/TIFF/PNG/RAW(CR2/NEF/ARW等)
═══════════════════════════════"""
        yield event.plain_result(help_text)

    # ================================================================
    # 🔍 /exif — 查询完整元数据（权限隔离）
    # ================================================================

    @filter.command("exif")
    async def query_exif(self, event: AstrMessageEvent):
        """查询完整EXIF元数据。支持发送图片后输入、@引用图片、或附带图片发送"""
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"⚠️ {reason}")
            return
        if not self._check_rate_limit(event.get_sender_id(), is_command=True):
            yield event.plain_result("⚠️ 查询过于频繁，请稍后再试")
            return

        # 尝试从事件获取图片并分析
        file_path = await self._get_image_path_from_event(event)
        if file_path:
            result = await self._analyze_image(file_path, event)
            self._cleanup_temp_image(file_path)
            if result:
                yield self._format_reply(event, ExifAnalyzer.format_full_exif_text(result))
                event.stop_event()
                return

        # 从缓存获取
        cached = self._get_cached_result(event)
        if cached:
            yield self._format_reply(event, ExifAnalyzer.format_full_exif_text(cached))
            event.stop_event()
            return

        # 权限提示：引用了别人的图片但被拦截
        if self._is_blocked_reference(event):
            yield event.plain_result(
                "🔒 这不是你发送的图片哦~\n"
                "主人没有开放别人查看其他人的 EXIF 信息呢"
            )
            return

        yield event.plain_result(
            "⚠️ 未找到可查询的图片\n请先发送图片再使用 /exif"
        )

    # ================================================================
    # 📊 字段单独查询指令
    # ================================================================

    def _is_blocked_reference(self, event: AstrMessageEvent) -> bool:
        """检查是否引用了别人的图片且被权限拦截"""
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
        """从事件中提取图片文件路径（优先级：引用图片 > 直接图片）
        权限控制：allow_reference_query=false 时只有原发送者能引用自己的图片。
        """
        allow_ref = self.config.get("allow_reference_query", False)
        my_uid = event.get_sender_id()

        for msg in event.get_messages():
            if isinstance(msg, Reply):
                ref_uid = str(getattr(msg, "sender_id", ""))
                # 权限判断：开启引用 或 自己引用自己的图片
                if allow_ref or ref_uid == my_uid:
                    ref_chain = getattr(msg, "chain", []) or getattr(msg, "message", []) or []
                    for ref_msg in ref_chain:
                        if self._is_processable_image(ref_msg):
                            try:
                                path = await self._get_file_path(ref_msg)
                                if path and os.path.isfile(path):
                                    return path
                            except Exception:
                                pass
        # 直接图片（始终允许）
        for msg in event.get_messages():
            if self._is_processable_image(msg):
                try:
                    path = await self._get_file_path(msg)
                    if path and os.path.isfile(path):
                        return path
                except Exception:
                    pass
        return None

    async def _query_field(self, event: AstrMessageEvent, field_key: str, cn_name: str):
        """通用字段查询：下载图片→分析→提取指定字段→回复"""
        allowed, reason = self._check_access(event)
        if not allowed:
            yield event.plain_result(f"⚠️ {reason}")
            return
        if not self._check_rate_limit(event.get_sender_id(), is_command=True):
            yield event.plain_result("⚠️ 查询过于频繁，请稍后再试")
            return

        # 尝试从事件获取图片并分析
        file_path = await self._get_image_path_from_event(event)
        result = None
        if file_path:
            result = await self._analyze_image(file_path, event)
            self._cleanup_temp_image(file_path)

        # 如果无图片或分析失败，从缓存取
        if not result:
            result = self._get_cached_result(event)

        if not result:
            if self._is_blocked_reference(event):
                yield event.plain_result(
                    "🔒 这不是你发送的图片哦~\n"
                    "主人没有开放别人查看其他人的 EXIF 信息呢"
                )
            else:
                yield event.plain_result(f"⚠️ 未找到可查询的图片，请先发送图片再 /{cn_name}")
            return

        exif = result.get("exif_data", {})

        # 提取指定字段
        if field_key == "shutter_count":
            sc = result.get("shutter_count") or "无法获取"
            yield event.plain_result(f"📷 快门次数: {sc}")
        elif field_key == "image_size":
            yield event.plain_result(
                f"🖼️ {cn_name}: {exif.get('ImageWidth', '?')} × {exif.get('ImageLength', '?')} px"
            )
        elif field_key == "gps":
            gps = result.get("gps", {})
            if gps:
                lines = [f"📍 {cn_name}:"]
                for k, v in gps.items():
                    if k != "map_url" and v:
                        lines.append(f"  {k}: {v}")
                if gps.get("map_url"):
                    lines.append(f"  🗺️ {gps['map_url']}")
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("📍 该图片无GPS信息")
        else:
            val = exif.get(field_key, "")
            if val:
                yield event.plain_result(f"📸 {cn_name}: {val}")
            else:
                yield event.plain_result(f"⚠️ 该图片未包含{cn_name}信息")

        event.stop_event()

    # 显式注册每个字段查询指令（完整中英文/大小写别名）

    # ── 快门次数（机械快门使用次数，如2863） ──
    @filter.command("快门次数", alias={"快门次数查询", "shuttercount", "SC"})
    async def q_shutter(self, event: AstrMessageEvent):
        """查询相机快门使用次数。支持 /快门次数 /快门次数查询 /shuttercount /SC"""
        async for r in self._query_field(event, "shutter_count", "快门次数"):
            yield r

    # ── 快门速度（曝光时间，如1/25s） ──
    @filter.command("快门", alias={"快门速度", "快门值", "快门查询", "曝光时间", "曝光时间查询", "shutterspeed", "shutter"})
    async def q_shutter_speed(self, event: AstrMessageEvent):
        """查询快门速度/曝光时间。支持 /快门 /快门速度 /曝光时间 /shutterspeed"""
        async for r in self._query_field(event, "ExposureTime", "快门速度"):
            yield r

    @filter.command("相机型号", alias={"相机型号查询", "型号", "型号查询", "model"})
    async def q_model(self, event: AstrMessageEvent):
        """查询相机型号。支持 /相机型号 /型号 /model"""
        async for r in self._query_field(event, "Model", "相机型号"):
            yield r

    @filter.command("相机品牌", alias={"相机品牌查询", "品牌", "品牌查询", "make"})
    async def q_make(self, event: AstrMessageEvent):
        """查询相机品牌/制造商。支持 /相机品牌 /品牌 /make"""
        async for r in self._query_field(event, "Make", "相机品牌"):
            yield r

    @filter.command("镜头型号", alias={"镜头型号查询", "镜头", "镜头查询", "lens"})
    async def q_lens_model(self, event: AstrMessageEvent):
        """查询镜头型号。支持 /镜头型号 /镜头 /lens"""
        async for r in self._query_field(event, "LensModel", "镜头型号"):
            yield r

    @filter.command("镜头品牌", alias={"镜头品牌查询", "lensmake"})
    async def q_lens_make(self, event: AstrMessageEvent):
        """查询镜头品牌/制造商。支持 /镜头品牌 /lensmake"""
        async for r in self._query_field(event, "LensMake", "镜头品牌"):
            yield r

    @filter.command("焦距", alias={"焦距查询", "focal", "focallength"})
    async def q_focal(self, event: AstrMessageEvent):
        """查询拍摄焦距。支持 /焦距 /焦距查询 /focal"""
        async for r in self._query_field(event, "FocalLength", "焦距"):
            yield r

    @filter.command("光圈", alias={"光圈查询", "光圈值", "光圈值查询", "aperture", "fnumber"})
    async def q_aperture(self, event: AstrMessageEvent):
        """查询光圈值。支持 /光圈 /光圈查询 /aperture /fnumber"""
        async for r in self._query_field(event, "FNumber", "光圈值"):
            yield r

    @filter.command("ISO", alias={"ISO查询", "iso", "iso查询", "感光度", "感光度查询", "ISOSpeed"})
    async def q_iso(self, event: AstrMessageEvent):
        """查询ISO感光度。支持 /ISO /iso /感光度 /ISO查询"""
        async for r in self._query_field(event, "ISOSpeedRatings", "ISO感光度"):
            yield r

    @filter.command("测光模式", alias={"测光模式查询", "测光", "测光查询", "metering"})
    async def q_metering(self, event: AstrMessageEvent):
        """查询测光模式。支持 /测光模式 /测光 /metering"""
        async for r in self._query_field(event, "MeteringMode", "测光模式"):
            yield r

    @filter.command("曝光模式", alias={"曝光模式查询", "曝光", "曝光查询", "exposure"})
    async def q_exposure_prog(self, event: AstrMessageEvent):
        """查询曝光模式(手动/光圈优先/快门优先等)。支持 /曝光模式 /曝光 /exposure"""
        async for r in self._query_field(event, "ExposureProgram", "曝光模式"):
            yield r

    @filter.command("曝光补偿", alias={"曝光补偿查询", "EV", "ev", "exposurebias"})
    async def q_exposure_bias(self, event: AstrMessageEvent):
        """查询曝光补偿值(EV)。支持 /曝光补偿 /EV /ev"""
        async for r in self._query_field(event, "ExposureBiasValue", "曝光补偿"):
            yield r

    @filter.command("闪光灯", alias={"闪光灯查询", "闪光", "闪光查询", "flash"})
    async def q_flash(self, event: AstrMessageEvent):
        """查询闪光灯状态。支持 /闪光灯 /闪光 /flash"""
        async for r in self._query_field(event, "Flash", "闪光灯"):
            yield r

    @filter.command("白平衡", alias={"白平衡查询", "whitebalance", "wb"})
    async def q_wb(self, event: AstrMessageEvent):
        """查询白平衡设置。支持 /白平衡 /whitebalance /wb"""
        async for r in self._query_field(event, "WhiteBalance", "白平衡"):
            yield r

    @filter.command("拍摄时间", alias={"拍摄时间查询", "时间", "时间查询", "datetime", "date"})
    async def q_datetime(self, event: AstrMessageEvent):
        """查询原始拍摄时间。支持 /拍摄时间 /时间 /datetime"""
        async for r in self._query_field(event, "DateTimeOriginal", "拍摄时间"):
            yield r

    @filter.command("机身序列号", alias={"机身序列号查询", "序列号", "序列号查询", "serial", "sn"})
    async def q_serial(self, event: AstrMessageEvent):
        """查询相机机身序列号。支持 /机身序列号 /序列号 /serial /sn"""
        async for r in self._query_field(event, "BodySerialNumber", "机身序列号"):
            yield r

    @filter.command("图片尺寸", alias={"图片尺寸查询", "尺寸", "尺寸查询", "分辨率", "分辨率查询", "size", "resolution"})
    async def q_size(self, event: AstrMessageEvent):
        """查询图片分辨率/尺寸。支持 /图片尺寸 /分辨率 /size"""
        async for r in self._query_field(event, "image_size", "图片尺寸"):
            yield r

    @filter.command("处理软件", alias={"处理软件查询", "软件", "软件查询", "software"})
    async def q_software(self, event: AstrMessageEvent):
        """查询后期处理软件。支持 /处理软件 /软件 /software"""
        async for r in self._query_field(event, "Software", "处理软件"):
            yield r

    @filter.command("GPS", alias={"GPS查询", "gps", "位置", "位置查询", "定位", "定位查询"})
    async def q_gps(self, event: AstrMessageEvent):
        """查询GPS位置信息。支持 /GPS /位置 /定位"""
        async for r in self._query_field(event, "gps", "GPS信息"):
            yield r

    # ================================================================
    # 🔄 自动检测 — 监听所有图片消息（无需@唤醒，优先级-10）
    # ================================================================

    @filter.custom_filter(_AlwaysPassFilter, False)
    async def auto_detect_images(self, event: AstrMessageEvent):
        """监听所有消息，自动检测图片/文件中的EXIF并回复。

        使用 regex(r\".*\") 而非 event_message_type，确保文件消息（无文本）
        也能触发。优先级-10避免与指令冲突。
        """
        if not self.config.get("enabled", True) or not self.config.get("auto_detect_enabled", True):
            return
        if event.is_stopped():
            return
        comps = event.get_messages()
        for comp in comps:
            if not self._is_processable_image(comp):
                continue
            try:
                file_path = await self._get_file_path(comp)
                if not file_path or not os.path.isfile(file_path):
                    continue
                show_hint = self.config.get("show_analyzing_hint", True)
                async for reply in self._analyze_and_reply_image(event, file_path, show_analyzing=show_hint):
                    yield reply
            except Exception as e:
                logger.error(f"[相机EXIF] 自动检测错误: {e}", exc_info=True)

    # ================================================================
    # 🪝 AstrBot 加载完成钩子
    # ================================================================

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        """AstrBot框架加载完成时触发，输出插件就绪日志和依赖检查结果"""
        logger.info("[相机EXIF] AstrBot已加载，相机EXIF分析插件就绪")
