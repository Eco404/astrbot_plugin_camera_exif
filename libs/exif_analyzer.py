"""
相机EXIF分析插件 — EXIF 分析引擎模块

ExifAnalyzer 类：核心分析引擎，多源提取 EXIF 元数据。
分析链路：
  1. PIL getexif() → IFD0 + 子 IFD + 原始字节 + XMP 提取
  2. exifread 补充 → 标准 IFD + MakerNote 子标签
  3. RAW rawpy 回退（RAW 文件专用）
  4. MakerNote 二进制解析 + 快门次数提取
  5. DNG 模块补充（DNG 文件专用）
  6. XMP 基础信息 + 品牌/型号回填
  7. GPS 信息提取
"""

from __future__ import annotations

import asyncio
import os
import re
from io import BytesIO
from typing import Any

from astrbot.api import logger

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

try:
    import rawpy

    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False

from .constants import RAW_EXTENSIONS
from .utils import (
    clean_tag_value,
    format_fraction,
    convert_to_degrees,
    rational_to_float,
    build_gps_map_url,
)
from .dng_parser import DngParser
from .shutter import (
    find_shutter_count,
    find_shutter_count_from_exif,
)
from .formatter import (
    format_display_text,
    format_full_exif_text,
    format_shutter_only,
)


# ================================================================
# MakerNote 子标签 → 标准 EXIF 键映射
# ================================================================
_MN_MODEL_KEYS = (
    "ModelID",
    "CameraModel",
    "Model",
)

# 品牌关键字 → 品牌名映射（用于从 ModelID 推断）
_BRAND_KEYWORDS: list[tuple[str, str]] = [
    ("Canon", "Canon"),
    ("NIKON", "NIKON CORPORATION"),
    ("SONY", "SONY"),
    ("FUJIFILM", "FUJIFILM"),
    ("OLYMPUS", "OLYMPUS"),
    ("Panasonic", "Panasonic"),
    ("PENTAX", "PENTAX"),
    ("LEICA", "LEICA"),
    ("SIGMA", "SIGMA"),
    ("HASSELBLAD", "HASSELBLAD"),
    ("DJI", "DJI"),
    ("GoPro", "GoPro"),
]


class ExifAnalyzer:
    """EXIF 数据解析引擎，支持 JPEG/TIFF/RAW/DNG 等多种格式。

    属性:
        file_path: 文件路径
        file_name: 文件名
        file_size_mb: 文件大小(MB)
        file_ext: 小写扩展名
        is_raw: 是否为 RAW 格式
        raw_format: RAW 格式显示名称
        config: 解析配置
    """

    # ── 常见 EXIF 标签中文名映射 ──
    TAG_NAMES_CN: dict[str, str] = {
        # 基础信息
        "Make": "相机品牌",
        "Model": "相机型号",
        "Software": "处理软件",
        "DateTime": "拍摄时间",
        "DateTimeOriginal": "原始拍摄时间",
        "DateTimeDigitized": "数字化时间",
        "SubSecTimeOriginal": "亚秒时间",
        "ImageDescription": "图像描述",
        "Orientation": "方向",
        "Artist": "作者",
        "Copyright": "版权",
        # 拍摄参数
        "ExposureTime": "快门速度",
        "FNumber": "光圈值",
        "ExposureProgram": "曝光模式",
        "ISOSpeedRatings": "ISO感光度",
        "FocalLength": "焦距",
        "FocalLengthIn35mmFilm": "35mm等效焦距",
        "ExposureBiasValue": "曝光补偿",
        "MaxApertureValue": "最大光圈",
        "MeteringMode": "测光模式",
        "Flash": "闪光灯",
        "WhiteBalance": "白平衡",
        "ExposureMode": "曝光模式(Exif)",
        "ColorSpace": "色彩空间",
        "SceneCaptureType": "场景类型",
        "Contrast": "对比度",
        "Saturation": "饱和度",
        "Sharpness": "锐度",
        "GainControl": "增益控制",
        "LightSource": "光源",
        "SubjectDistance": "拍摄距离",
        "DigitalZoomRatio": "数码变焦比",
        # 镜头信息
        "LensModel": "镜头型号",
        "LensMake": "镜头品牌",
        "LensSpecification": "镜头规格",
        "LensSerialNumber": "镜头序列号",
        # 序列号
        "BodySerialNumber": "机身序列号",
        "SerialNumber": "序列号",
        # 图片信息
        "ImageWidth": "图片宽度",
        "ImageLength": "图片高度",
        "PixelXDimension": "有效像素宽度",
        "PixelYDimension": "有效像素高度",
        # GPS
        "GPSLatitude": "GPS纬度",
        "GPSLongitude": "GPS经度",
        "GPSAltitude": "GPS海拔",
        # 快门
        "ShutterCount": "快门次数",
    }

    # 曝光程序映射
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
        0x1D: "自动(闪光,未检测到返回光)",
        0x1F: "自动(闪光,检测到返回光)",
        0x20: "无闪光功能",
        0x41: "防红眼",
        0x45: "防红眼(未检测到返回光)",
        0x47: "防红眼(检测到返回光)",
        0x49: "防红眼(强制闪光)",
        0x4D: "防红眼(强制,未检测到返回光)",
        0x4F: "防红眼(强制,检测到返回光)",
        0x59: "防红眼(自动闪光)",
        0x5D: "防红眼(自动,未检测到返回光)",
        0x5F: "防红眼(自动,检测到返回光)",
    }

    def __init__(
        self, file_path: str = "", config: dict[str, Any] | None = None
    ) -> None:
        """初始化分析器。

        Args:
            file_path: 图片文件路径
            config: 解析配置 {
                "raw_format_config": {...},
                "max_image_size_mb": int,
            }
        """
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

        # GPS 地图配置
        self._map_provider = self._resolve_map_provider()

    def _resolve_map_provider(self) -> tuple[str, str]:
        """从配置解析地图提供商和自定义 URL。

        Returns:
            (provider_key, custom_url)
            provider_key: google/amap/baidu/tencent/openstreetmap/custom
        """
        raw = self.config.get("gps_map_provider", "高德地图")
        custom = str(self.config.get("gps_custom_map_url", "")).strip()
        p = str(raw).lower().strip()
        mapping = {
            "高德地图": "amap",
            "百度地图": "baidu",
            "腾讯地图": "tencent",
            "google maps": "google",
            "openstreetmap": "openstreetmap",
            "自定义": "custom",
        }
        provider = mapping.get(p, "amap")
        return provider, custom

    # ================================================================
    # 主分析入口（重构版：所有提取器独立运行 + 智能合并去重）
    # ================================================================

    # 数据源优先级（数字越大越优先）
    _SOURCE_PRIORITY: dict[str, int] = {
        "pil": 10,
        "exifread": 20,
        "rawpy": 15,
        "dng": 40,
        "xmp_pil": 30,  # PIL img.info 中的 XMP
        "xmp_exif": 30,  # XMLPacket 中的 XMP
        "makernote": 25,
    }

    def analyze(self) -> dict[str, Any]:
        """执行完整的 EXIF 分析，返回结构化结果。

        新架构：
          1. 所有提取器独立运行（PIL / exifread / rawpy / DNG）
          2. 智能合并去重（按优先级 + 值质量选择最佳值）
          3. 后处理（品牌推断 / 快门 / XMP / GPS）
          4. 每个值标注数据来源

        Returns:
            {
                "file_info":       {...},
                "is_camera_image": bool,
                "exif_data":       {tag_name: value},
                "_exif_sources":   {tag_name: "pil|exifread|dng|xmp|..."},
                "shutter_count":   str|None,
                "gps":             {...},
                "maker_note":      {...},
                "xmp":             {...},
                "errors":          [str],
            }
        """
        result: dict[str, Any] = {
            "file_info": {
                "name": self.file_name,
                "size_mb": round(self.file_size_mb, 2),
                "is_raw": self.is_raw,
                "raw_format": self.raw_format,
            },
            "is_camera_image": False,
            "exif_data": {},
            "_exif_sources": {},
            "shutter_count": None,
            "gps": {},
            "maker_note": {},
            "xmp": {},
            "errors": [],
        }

        logger.info(
            f"[相机EXIF] 开始分析: {self.file_name} "
            f"(大小: {result['file_info']['size_mb']}MB, "
            f"RAW: {self.is_raw}, 格式: {self.file_ext})"
        )

        # ════════════════════════════════════════════════════════════
        # Phase 1: 所有提取器独立运行（各自产出完整数据）
        # ════════════════════════════════════════════════════════════
        sources: dict[str, dict[str, str]] = {}

        # Source 1: PIL（含 IFD0 + 子 IFD + 原始字节 + img.info XMP）
        pil_data = self._extract_pil_exif()
        # 把 XMP 部分拆出来作为独立源
        xmp_raw = pil_data.pop("_xmp_raw", "")
        sources["pil"] = {k: v for k, v in pil_data.items() if not k.startswith("_")}
        if xmp_raw:
            xmp_pil: dict[str, str] = {}
            self._backfill_from_xmp(xmp_pil, str(xmp_raw))
            sources["xmp_pil"] = xmp_pil

        logger.info(
            f"[相机EXIF] PIL: {len(sources['pil'])}K, "
            f"XMP(PIL): {len(sources.get('xmp_pil', {}))}K"
        )

        # Source 2: exifread
        exifread_data = self._extract_exifread()
        sources["exifread"] = {
            k: v for k, v in exifread_data.items() if not k.startswith("_")
        }

        logger.info(f"[相机EXIF] exifread: {len(sources['exifread'])}K")

        # Source 3: rawpy (仅 RAW)
        if self.is_raw and HAS_RAWPY:
            raw_data = self._extract_rawpy()
            sources["rawpy"] = {
                k: v for k, v in raw_data.items() if not k.startswith("_")
            }
            logger.info(f"[相机EXIF] rawpy: {len(sources['rawpy'])}K")
        else:
            sources["rawpy"] = {}

        # Source 4: DNG
        if self.file_ext == ".dng" or self._is_dng_candidate(sources["pil"]):
            dng = DngParser(self.file_path)
            dng_result = dng.parse()
            dng_exif = dng.to_exif_data()
            dng_shutter = dng_exif.pop("_dng_shutter", None)
            if dng_shutter:
                result["shutter_count"] = dng_shutter
            sources["dng"] = {
                k: v for k, v in dng_exif.items() if not k.startswith("_")
            }
            logger.info(
                f"[相机EXIF] DNG: {len(sources['dng'])}K, "
                f"vendor={dng_result.get('vendor', '')}, "
                f"shutter={dng_shutter}"
            )
        else:
            sources["dng"] = {}

        # ════════════════════════════════════════════════════════════
        # Phase 2: 智能合并去重
        # ════════════════════════════════════════════════════════════
        merged, sources_map = self._smart_merge(sources)

        result["exif_data"] = merged
        result["_exif_sources"] = sources_map

        # 保留内部用 MakerNote 原始字节
        mn_raw = exifread_data.get(
            "_raw_makernote", pil_data.get("_raw_makernote", b"")
        )
        if mn_raw and isinstance(mn_raw, bytes):
            result["exif_data"]["_raw_makernote"] = mn_raw
        # 保留 XMP 原始数据
        if xmp_raw:
            result["exif_data"]["_xmp_raw"] = xmp_raw
        # 保留 PreservedFileName
        preserved = pil_data.get(
            "_xmp_preserved", exifread_data.get("_xmp_preserved", "")
        )
        if preserved:
            result["exif_data"]["_xmp_preserved"] = preserved

        # ════════════════════════════════════════════════════════════
        # Phase 3: 后处理
        # ════════════════════════════════════════════════════════════

        # 判决
        if not result["is_camera_image"]:
            result["is_camera_image"] = self._is_camera_image(merged)

        # ── 最后兜底：全文搜索相机型号 ──
        if not result["exif_data"].get("Make") or not result["exif_data"].get("Model"):
            self._search_raw_file_for_model(result["exif_data"])

        # 品牌/型号 回填
        self._fill_missing_brand(result["exif_data"])

        # 快门（MakerNote 二进制 + exif_data 子标签）
        if result["is_camera_image"] and not result.get("shutter_count"):
            mn_bytes = result["exif_data"].get("_raw_makernote", b"")
            if isinstance(mn_bytes, str):
                mn_bytes = mn_bytes.encode("utf-8", errors="replace")
            if not isinstance(mn_bytes, bytes):
                mn_bytes = b""
            mn_data = self._extract_makernote_details(mn_bytes)
            if mn_data:
                result["maker_note"] = mn_data
                sc = mn_data.get("shutter_count")
                if sc:
                    result["shutter_count"] = str(sc)
                # Canon ModelID → 回填 Model
                canon_model = mn_data.pop("_canon_model", None)
                if canon_model and not result["exif_data"].get("Model"):
                    result["exif_data"]["Model"] = canon_model
                    result["exif_data"]["Make"] = "Canon"
                    logger.info(f"[相机EXIF] Model来自Canon MakerNote: {canon_model}")
            if not result["shutter_count"]:
                sc = find_shutter_count_from_exif(result["exif_data"])
                if sc:
                    result["shutter_count"] = str(sc)

        # XMP 基础信息（XMLPacket）
        xmp_final = self._extract_xmp_basics(result["exif_data"])
        if xmp_final:
            for xk, ek in [
                ("XmpMake", "Make"),
                ("XmpModel", "Model"),
                ("XmpSerial", "BodySerialNumber"),
            ]:
                xv = xmp_final.get(xk, "")
                if xv and not result["exif_data"].get(ek):
                    result["exif_data"][ek] = xv
            xmp_d = {k: v for k, v in xmp_final.items() if not k.startswith("Xmp")}
            result["xmp"] = xmp_d if xmp_d else xmp_final

        # GPS
        result["gps"] = self._extract_gps(result["exif_data"])

        # 统计
        total = len([k for k in merged if not k.startswith("_")])
        logger.info(
            f"[相机EXIF] 分析完成: is_camera={result['is_camera_image']}, "
            f"总标签={total}, 快门={result['shutter_count']}, "
            f"来源数={set(sources_map.values())}"
        )

        return result

    # ================================================================
    # 智能合并
    # ================================================================

    def _smart_merge(
        self, sources: dict[str, dict[str, str]]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """智能合并多来源 EXIF 数据。

        规则：
        1. 同一 key 多个 source → 按优先级选择值
        2. 同优先级 → 选择更长的值（更具体的）
        3. 值为空的 source 跳过
        4. 记录每个 key 的最终来源

        Args:
            sources: {source_name: {key: value}}

        Returns:
            (merged_dict, source_dict)
        """
        merged: dict[str, str] = {}
        source_map: dict[str, str] = {}

        # 收集所有 key
        all_keys: set[str] = set()
        for src_data in sources.values():
            all_keys.update(src_data.keys())

        for key in all_keys:
            best_val = ""
            best_src = ""
            best_prio = -1

            for src_name, src_data in sources.items():
                val = src_data.get(key, "")
                if not val or not val.strip():
                    continue
                prio = self._SOURCE_PRIORITY.get(src_name, 0)
                # 相同优先级选更长的值
                if prio > best_prio or (
                    prio == best_prio and len(str(val)) > len(str(best_val))
                ):
                    best_val = str(val)
                    best_src = src_name
                    best_prio = prio

            if best_val:
                merged[key] = best_val
                source_map[key] = best_src

        logger.info(
            f"[相机EXIF] 智能合并: {sum(len(v) for v in sources.values())}条 → "
            f"{len(merged)}条, 来源分布={ {s: list(source_map.values()).count(s) for s in sources if sources[s]} }"
        )
        return merged, source_map

    # ================================================================
    # 判断是否为 DNG 候选文件
    # ================================================================

    @staticmethod
    def _is_dng_candidate(exif_data: dict[str, Any]) -> bool:
        """通过 EXIF 数据判断文件是否为 DNG 格式候选。

        Args:
            exif_data: EXIF 数据字典

        Returns:
            是否可能为 DNG 文件
        """
        # 显式 DNG 版本标签
        if exif_data.get("DNGVersion") or exif_data.get("DNGBackwardVersion"):
            return True
        # UniqueCameraModel
        if exif_data.get("UniqueCameraModel"):
            return True
        # CameraSerialNumber
        if exif_data.get("CameraSerialNumber"):
            return True
        # OriginalRawFileName
        if exif_data.get("OriginalRawFileName"):
            return True
        return False

    # ================================================================
    # 相机图片判定
    # ================================================================

    @staticmethod
    def _is_camera_image(exif_data: dict[str, Any]) -> bool:
        """判断是否为相机拍摄的图片（多重指标）。

        Args:
            exif_data: EXIF 数据字典

        Returns:
            True 如果包含相机 EXIF 信息
        """
        # 强信号：相机品牌/型号/机身序列号/镜头信息
        strong = ("Make", "Model", "BodySerialNumber", "LensModel", "LensMake")
        for key in strong:
            val = exif_data.get(key, "")
            if val and val.strip() and len(val.strip()) > 1:
                return True

        # DNG 特有信号
        if exif_data.get("UniqueCameraModel") or exif_data.get("DNGVersion"):
            return True

        # 中等信号：至少 3 个相机独有参数
        camera_params = (
            "FNumber",
            "ExposureTime",
            "ISOSpeedRatings",
            "FocalLength",
            "FocalLengthIn35mmFilm",
            "ExposureProgram",
            "MeteringMode",
            "WhiteBalance",
            "Flash",
            "ExposureBiasValue",
            "LensSerialNumber",
            "LensSpecification",
        )
        count = sum(1 for k in camera_params if exif_data.get(k, ""))
        return count >= 3

    # ================================================================
    # PIL EXIF 提取
    # ================================================================

    def _extract_pil_exif(self) -> dict[str, Any]:
        """使用 PIL 提取 EXIF 数据。

        提取来源:
        - PIL getexif() → IFD0
        - PIL get_ifd(0x8769) → EXIF 子 IFD
        - PIL get_ifd(0x8825) → GPS 子 IFD
        - img.info['exif'] → 原始字节解析
        - img.info['xmp'] → XMP tiff:Make/Model
        - raw_exif latin-1 搜索 → 相机型号兜底

        Returns:
            {tag_name: value} 映射
        """
        data: dict[str, Any] = {}
        if not HAS_PIL:
            return data

        try:
            img = PILImage.open(self.file_path)
            img.load()

            # 图片尺寸
            data["ImageWidth"] = str(img.width)
            data["ImageLength"] = str(img.height)

            # ── 方法1: PIL getexif() ──
            exif = img.getexif()
            if exif and len(exif) > 0:
                logger.info(f"[相机EXIF] PIL getexif(): {len(exif)} 标签")
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
                    data[tag_name] = clean_tag_value(value)

                # Pillow >= 10.0.0: 子 IFD 提取
                sub_ifd_count = 0

                # EXIF 子 IFD (0x8769)
                try:
                    exif_ifd = exif.get_ifd(0x8769)
                    if exif_ifd:
                        for tag_id, value in exif_ifd.items():
                            tag_name = EXIF_TAGS.get(tag_id, f"Tag_{tag_id}")
                            if tag_name == "MakerNote":
                                data["_raw_makernote"] = (
                                    value
                                    if isinstance(value, bytes)
                                    else str(value).encode()
                                )
                                continue
                            if tag_name not in data:
                                data[tag_name] = clean_tag_value(value)
                                sub_ifd_count += 1
                except Exception:
                    pass

                # GPS 子 IFD (0x8825)
                try:
                    gps_ifd = exif.get_ifd(0x8825)
                    if gps_ifd:
                        for tag_id, value in gps_ifd.items():
                            tag_name = GPSTAGS.get(tag_id, f"GPS_{tag_id}")
                            if tag_name not in data:
                                data[tag_name] = clean_tag_value(value)
                                sub_ifd_count += 1
                except Exception:
                    pass

                if sub_ifd_count > 0:
                    logger.info(f"[相机EXIF] PIL 子IFD额外提取: {sub_ifd_count} 标签")

            # ── 方法2: img.info 原始字节解析 ──
            if hasattr(img, "info"):
                raw_exif = (
                    img.info.get("exif") or img.info.get("Exif") or img.info.get("EXIF")
                )
                if raw_exif and isinstance(raw_exif, bytes) and len(raw_exif) > 20:
                    parsed = self._parse_raw_exif_bytes(raw_exif)
                    if parsed:
                        data.update(parsed)

                # ── 方法2b: XMP 提取 ──
                xmp_raw = (
                    img.info.get("xmp") or img.info.get("XMP") or img.info.get("xml")
                )
                if xmp_raw:
                    try:
                        xmp_str = (
                            xmp_raw
                            if isinstance(xmp_raw, str)
                            else xmp_raw.decode("utf-8", errors="replace")
                        )
                        # 保存完整 XMP 供后续 _fill_missing_brand 使用
                        data["_xmp_raw"] = xmp_str[:50000]

                        # 提取 tiff:Make / tiff:Model（属性语法 + 元素语法）
                        for pattern, key in [
                            (r"tiff:Make[^>]*>([^<]+)<", "Make"),
                            (r"tiff:Model[^>]*>([^<]+)<", "Model"),
                            (r"""tiff:Make\s*=\s*["']([^"']+)["']""", "Make"),
                            (r"""tiff:Model\s*=\s*["']([^"']+)["']""", "Model"),
                        ]:
                            if not data.get(key):
                                m = re.search(pattern, xmp_str, re.IGNORECASE)
                                if m:
                                    data[key] = m.group(1).strip()
                                    logger.info(
                                        f"[相机EXIF] {key}回退自PIL XMP: {data[key]}"
                                    )

                        # 提取 dc:creator → Artist
                        if not data.get("Artist"):
                            m = re.search(
                                r"<dc:creator>\s*<rdf:Seq>\s*<rdf:li>([^<]+)</rdf:li>",
                                xmp_str,
                            )
                            if m:
                                data["Artist"] = m.group(1).strip()

                        # 提取 aux:SerialNumber
                        if not data.get("BodySerialNumber"):
                            for pat in [
                                r"<aux:SerialNumber>([^<]+)</aux:SerialNumber>",
                                r"""aux:SerialNumber\s*=\s*["']([^"']+)["']""",
                            ]:
                                m = re.search(pat, xmp_str, re.IGNORECASE)
                                if m:
                                    data["BodySerialNumber"] = m.group(1).strip()
                                    logger.info(
                                        f"[相机EXIF] BodySerialNumber回退自XMP: "
                                        f"{data['BodySerialNumber']}"
                                    )
                                    break

                        # 提取 xmpMM:PreservedFileName → 推断品牌
                        m = re.search(
                            r"<xmpMM:PreservedFileName>([^<]+)</xmpMM:PreservedFileName>",
                            xmp_str,
                        )
                        if m:
                            data["_xmp_preserved"] = m.group(1).strip()

                        # 提取 crs:RawFileName
                        m = re.search(
                            r"<crs:RawFileName>([^<]+)</crs:RawFileName>", xmp_str
                        )
                        if m and not data.get("_xmp_preserved"):
                            data["_xmp_preserved"] = m.group(1).strip()

                    except Exception:
                        pass

                # ── 方法2c: 原始字节搜索相机型号 ──
                if raw_exif and isinstance(raw_exif, bytes) and not data.get("Model"):
                    try:
                        text = raw_exif.decode("latin-1", errors="ignore")
                        for pattern, make_val in [
                            (r"Canon\s+EOS\s+[\w\d\s]+\b", "Canon"),
                            (r"NIKON\s+[\w\d\s]+\b", "NIKON CORPORATION"),
                            (r"SONY\s+[\w\d\s\-]+\b", "SONY"),
                            (r"LEICA\s+[\w\d\s\-]+\b", "LEICA"),
                            (r"PENTAX\s+[\w\d\s\-]+\b", "PENTAX"),
                        ]:
                            m = re.search(pattern, text)
                            if m:
                                found_model = m.group(0).strip()
                                if 4 < len(found_model) < 50:
                                    if not data.get("Make"):
                                        data["Make"] = make_val
                                    data["Model"] = found_model
                                    logger.info(
                                        f"[相机EXIF] Model回退自原始字节: {found_model}"
                                    )
                                    break
                    except Exception:
                        pass

            img.close()

        except Exception as e:
            logger.warning(f"[相机EXIF] PIL解析异常: {e}")
            data["_pil_error"] = str(e)

        return data

    # ================================================================
    # 原始 EXIF 字节解析
    # ================================================================

    def _parse_raw_exif_bytes(self, raw_exif: bytes) -> dict[str, Any]:
        """从原始 EXIF 字节数据中解析标签。

        处理 JPEG APP1 包装，剥离 Exif 前缀，定位 TIFF 头，
        使用 exifread 解析结构化标签。

        Args:
            raw_exif: 原始 EXIF 字节

        Returns:
            {tag_name: value} 映射
        """
        data: dict[str, Any] = {}

        # 剥离 "Exif\x00\x00" 前缀
        tiff_data = raw_exif
        if raw_exif[:6] == b"Exif\x00\x00":
            tiff_data = raw_exif[6:]
        elif raw_exif[:4] == b"Exif":
            tiff_data = raw_exif[4:]

        # 寻找 TIFF 头
        offsets_to_try: list[int] = [0]
        for scan_offset in range(len(tiff_data) - 4):
            chunk = tiff_data[scan_offset : scan_offset + 4]
            if chunk in (b"MM\x00\x2a", b"II\x2a\x00", b"MM\x00*", b"II*\x00"):
                if scan_offset > 0:
                    offsets_to_try.append(scan_offset)
                break

        for offset in sorted(set(offsets_to_try)):
            candidate = tiff_data[offset:]
            if len(candidate) < 10 or candidate[:2] not in (b"MM", b"II"):
                continue
            try:
                import exifread

                stream = BytesIO(candidate)
                tags = exifread.process_file(stream, details=True, debug=False)
                tag_count = 0
                for tag_name, tag_value in tags.items():
                    if tag_name in ("JPEGThumbnail", "TIFFThumbnail"):
                        continue
                    if "MakerNote" in tag_name:
                        # 提取二进制 MakerNote
                        if tag_name == "MakerNote" and hasattr(tag_value, "values"):
                            mn_bytes = self._coerce_makernote_bytes(tag_value.values)
                            old = data.get("_raw_makernote", b"")
                            if len(mn_bytes) > len(old):
                                data["_raw_makernote"] = mn_bytes
                        # 保留 MakerNote 子标签
                        data[tag_name] = str(tag_value)
                        tag_count += 1
                        continue
                    short_name = tag_name.split()[-1] if " " in tag_name else tag_name
                    val = str(tag_value)
                    if short_name in (
                        "FNumber",
                        "FocalLength",
                        "ExposureTime",
                        "ExposureBiasValue",
                        "ISOSpeedRatings",
                    ):
                        try:
                            if hasattr(tag_value, "values") and tag_value.values:
                                val = format_fraction(tag_value.values[0])
                        except Exception:
                            pass
                    data[short_name] = val
                    if " " in tag_name and short_name != tag_name:
                        data[tag_name] = val
                    tag_count += 1
                if tag_count >= 2:
                    logger.info(
                        f"[相机EXIF] 原始字节解析(偏移{offset}): {tag_count} 标签"
                    )
                    break
            except Exception:
                pass

        return data

    # ================================================================
    # exifread 解析
    # ================================================================

    def _extract_exifread(self) -> dict[str, Any]:
        """使用 exifread 直接解析文件。

        Returns:
            {tag_name: value} 映射
        """
        data: dict[str, Any] = {}
        if not HAS_EXIFREAD:
            return data

        try:
            import exifread

            with open(self.file_path, "rb") as f:
                tags = exifread.process_file(f, details=True, debug=False)

            for tag_name, tag_value in tags.items():
                if tag_name in ("JPEGThumbnail", "TIFFThumbnail"):
                    continue
                if "MakerNote" in tag_name:
                    # 顶层 MakerNote → 二进制字节
                    if tag_name == "MakerNote" and hasattr(tag_value, "values"):
                        mn = self._coerce_makernote_bytes(tag_value.values)
                        old = data.get("_raw_makernote", b"")
                        if len(mn) > len(old):
                            data["_raw_makernote"] = mn
                        continue

                    # MakerNote 子标签 → 结构化数据
                    tag_val_str = str(tag_value)
                    data[tag_name] = tag_val_str

                    # 检查是否为相机型号标签
                    short = tag_name.split()[-1] if " " in tag_name else tag_name
                    if short in _MN_MODEL_KEYS and tag_val_str:
                        if not data.get("Make"):
                            for brand_kw, brand_name in _BRAND_KEYWORDS:
                                if brand_kw.upper() in tag_val_str.upper():
                                    data["Make"] = brand_name
                                    break
                        if not data.get("Model") or data["Model"] == tag_val_str[:10]:
                            data["_mn_model"] = tag_val_str
                    continue

                short_name = tag_name.split()[-1] if " " in tag_name else tag_name
                val = str(tag_value)
                try:
                    if short_name in (
                        "FNumber",
                        "FocalLength",
                        "ExposureTime",
                        "ExposureBiasValue",
                        "ISOSpeedRatings",
                    ):
                        if hasattr(tag_value, "values") and tag_value.values:
                            val = format_fraction(tag_value.values[0])
                except Exception:
                    pass
                data[short_name] = val
                if " " in tag_name and short_name != tag_name:
                    data[tag_name] = val

            logger.info(
                f"[相机EXIF] exifread解析: {len(tags)} 标签, 提取 {len(data)} 有效标签"
            )

        except Exception as e:
            logger.warning(f"[相机EXIF] exifread解析异常: {e}")
            data["_exifread_error"] = str(e)

        return data

    # ================================================================
    # rawpy 回退
    # ================================================================

    def _extract_rawpy(self) -> dict[str, Any]:
        """使用 rawpy 解析 RAW 文件作为回退。

        Returns:
            {tag_name: value} 映射
        """
        data: dict[str, Any] = {}
        if not HAS_RAWPY:
            return data

        def _do_parse():
            try:
                import rawpy

                with rawpy.imread(self.file_path) as raw:
                    # rawpy 可提取部分元数据
                    data["Software"] = "RAW File (rawpy parsed)"
                    data["ImageWidth"] = str(raw.sizes.width)
                    data["ImageLength"] = str(raw.sizes.height)
                    try:
                        data["_rawpy_black_level"] = str(
                            raw.black_level_per_channel[:4]
                        )
                    except Exception:
                        pass
                    try:
                        data["_rawpy_white_level"] = str(raw.camera_white_level)
                    except Exception:
                        pass
            except Exception as e:
                data["_rawpy_error"] = str(e)
            return data

        try:
            return _do_parse()
        except Exception as e:
            data["_rawpy_error"] = str(e)
            return data

    # ================================================================
    # MakerNote 详细解析
    # ================================================================

    def _extract_makernote_details(self, mn_raw: bytes) -> dict[str, Any]:
        """解析已提取的 MakerNote 原始字节。

        Args:
            mn_raw: MakerNote 原始二进制数据

        Returns:
            解析结果 {field_name: value, "shutter_count": int}
        """
        result: dict[str, Any] = {}
        mn_entries: list[tuple[str, str]] = []
        binary_makernote: bytes = b""

        try:
            if isinstance(mn_raw, bytes) and len(mn_raw) > 10:
                if HAS_EXIFREAD:
                    try:
                        import exifread

                        tags = exifread.process_file(BytesIO(mn_raw), details=True)
                        for tag_name, tag_value in tags.items():
                            if "MakerNote" in tag_name:
                                continue
                            short_name = (
                                tag_name.split()[-1] if " " in tag_name else tag_name
                            )
                            mn_entries.append((short_name, str(tag_value)))
                    except Exception:
                        pass
                binary_makernote = mn_raw

            logger.info(
                f"[相机EXIF] MakerNote: {len(mn_entries)}KV "
                f"+ {len(binary_makernote)}B 二进制"
            )

            # 快门次数
            sc = find_shutter_count(mn_entries, binary_makernote)
            if sc:
                result["shutter_count"] = int(sc)

            # Canon MakerNote ModelID → 型号名称
            if binary_makernote and len(binary_makernote) > 20:
                model_name = self._parse_canon_model_from_makernote(binary_makernote)
                if model_name:
                    result["_canon_model"] = model_name

            # 结构化 MakerNote 字段
            from .constants import MAKERNOTE_CN_MAP

            for k_str, v_str in mn_entries:
                kl = k_str.lower()
                for kw, cn in MAKERNOTE_CN_MAP.items():
                    if kw in kl and cn not in result:
                        result[cn] = v_str

        except Exception as e:
            logger.warning(f"[相机EXIF] MakerNote解析异常: {e}")

        return result

    # ================================================================
    # Canon MakerNote ModelID 解析
    # ================================================================

    @staticmethod
    def _parse_canon_model_from_makernote(mn_bytes: bytes) -> str | None:
        """从 Canon MakerNote 二进制中提取 ModelID 并映射为型号名称。

        Canon MakerNote IFD 中 Tag 0x0010 = ModelID (int32u)。
        通过偏移扫描定位该标签并查表返回型号名。

        Args:
            mn_bytes: MakerNote 原始字节

        Returns:
            相机型号名称，未找到返回 None
        """
        from .constants import CANON_MODEL_MAP
        import struct

        if len(mn_bytes) < 20:
            return None

        # 扫描全部字节查找 tag=0x0010 的 IFD 条目
        # IFD 条目: TagID(2B) + Type(2B) + Count(4B) + Value(4B) = 12B
        for offset in range(0, len(mn_bytes) - 12):
            try:
                tid = struct.unpack_from("<H", mn_bytes, offset)[0]
            except struct.error:
                continue
            if tid != 0x0010:
                continue
            try:
                tag_type = struct.unpack_from("<H", mn_bytes, offset + 2)[0]
                # tag_count = struct.unpack_from("<I", mn_bytes, offset + 4)[0]
                tag_val = struct.unpack_from("<I", mn_bytes, offset + 8)[0]
            except struct.error:
                continue

            if tag_type in (3, 4, 7):  # SHORT, LONG, UNDEFINED
                model = CANON_MODEL_MAP.get(tag_val)
                if model:
                    logger.info(f"[相机EXIF] Canon ModelID: 0x{tag_val:08X} → {model}")
                    return model
                # 未知 ModelID 也记录
                logger.debug(f"[相机EXIF] Canon ModelID unknown: 0x{tag_val:08X}")
            break  # 找到 tag=0x0010 后停止（无论是否已知）

        return None

    # ================================================================
    # 品牌/型号回填
    # ================================================================

    @staticmethod
    def _backfill_from_xmp(exif_data: dict[str, Any], xmp_str: str) -> None:
        """从 XMP 原始字符串提前回填 Make/Model/Serial。

        在 _fill_missing_brand 之前调用，确保品牌/型号优先从 XMP 获取。
        """
        # tiff:Make / tiff:Model（元素语法 + 属性语法）
        for pattern, key in [
            (r"<tiff:Make>([^<]+)</tiff:Make>", "Make"),
            (r"<tiff:Model>([^<]+)</tiff:Model>", "Model"),
            (r"""tiff:Make\s*=\s*["']([^"']+)["']""", "Make"),
            (r"""tiff:Model\s*=\s*["']([^"']+)["']""", "Model"),
        ]:
            if not exif_data.get(key):
                m = re.search(pattern, xmp_str, re.IGNORECASE)
                if m:
                    exif_data[key] = m.group(1).strip()
                    logger.info(f"[相机EXIF] {key}回退自XMP(early): {exif_data[key]}")

        # aux:SerialNumber → BodySerialNumber
        if not exif_data.get("BodySerialNumber"):
            for pat in [
                r"<aux:SerialNumber>([^<]+)</aux:SerialNumber>",
                r"""aux:SerialNumber\s*=\s*["']([^"']+)["']""",
            ]:
                m = re.search(pat, xmp_str, re.IGNORECASE)
                if m:
                    exif_data["BodySerialNumber"] = m.group(1).strip()
                    logger.info(
                        f"[相机EXIF] BodySerialNumber回退自XMP(early): "
                        f"{exif_data['BodySerialNumber']}"
                    )
                    break

        # xmpMM:PreservedFileName / crs:RawFileName → 推断品牌
        if not exif_data.get("_xmp_preserved"):
            for tag in (
                r"<xmpMM:PreservedFileName>([^<]+)</xmpMM:PreservedFileName>",
                r"<crs:RawFileName>([^<]+)</crs:RawFileName>",
            ):
                m = re.search(tag, xmp_str)
                if m:
                    exif_data["_xmp_preserved"] = m.group(1).strip()
                    break

    @staticmethod
    def _fill_missing_brand(exif_data: dict[str, Any]) -> None:
        """通过已知数据反向推断缺失的 Make / Model。

        Args:
            exif_data: EXIF 数据字典（原地修改）
        """
        # ── XMP PreservedFileName 推断品牌（如 .CR2→Canon, .NEF→Nikon）──
        preserved = exif_data.pop("_xmp_preserved", "")
        if preserved and not exif_data.get("Make"):
            ext_map: dict[str, str] = {
                ".cr2": "Canon",
                ".cr3": "Canon",
                ".crw": "Canon",
                ".nef": "NIKON CORPORATION",
                ".nrw": "NIKON CORPORATION",
                ".arw": "SONY",
                ".srf": "SONY",
                ".sr2": "SONY",
                ".raf": "FUJIFILM",
                ".orf": "OLYMPUS",
                ".rw2": "Panasonic",
                ".pef": "PENTAX",
                ".dng": "",
                ".3fr": "HASSELBLAD",
                ".fff": "HASSELBLAD",
                ".srw": "SAMSUNG",
                ".mrw": "Minolta",
                ".x3f": "SIGMA",
                ".gpr": "GoPro",
            }
            ext = os.path.splitext(preserved)[1].lower()
            brand = ext_map.get(ext, "")
            if brand:
                exif_data["Make"] = brand
                logger.info(
                    f"[相机EXIF] 品牌推断自XMP PreservedFileName: {preserved} → {brand}"
                )

        # LensModel 推断品牌
        lens = (exif_data.get("LensModel") or "").upper()
        lens_orig = exif_data.get("LensModel") or ""
        if lens and not exif_data.get("Make"):
            for kw, brand in [
                ("NIKKOR", "NIKON CORPORATION"),
                ("CANON EF", "Canon"),
                ("EF-S", "Canon"),
                ("EF-M", "Canon"),
                ("RF", "Canon"),
                ("FUJINON", "FUJIFILM"),
                ("XF", "FUJIFILM"),
                ("M.ZUIKO", "OLYMPUS"),
                ("LUMIX", "Panasonic"),
            ]:
                if kw in lens:
                    exif_data["Make"] = brand
                    break

            # EF 开头镜头（不含 EF-S/EF-M），必须在 Sony 检查之前，防止 "EF 70-200" 误匹配 Sony
            if not exif_data.get("Make"):
                if (
                    lens.startswith("EF")
                    and "EF-S" not in lens
                    and "EF-M" not in lens
                    and not lens.startswith("EFE")
                ):
                    exif_data["Make"] = "Canon"
                    logger.info(
                        f"[相机EXIF] 品牌推断自EF镜头: {exif_data.get('LensModel', '')}"
                    )
            # Sony E/FE 卡口：用 \b 词边界防止误匹配
            # "iPhone 17" 中的 "Phone E" 因字母间无词边界不会匹配
            # 而 "E 18-55mm" / "FE 24-70mm" / "E PZ 16-50mm" 均正确匹配
            if not exif_data.get("Make"):
                sony_match = re.search(r"\b(E|FE)\s", lens_orig, re.IGNORECASE)
                if sony_match:
                    exif_data["Make"] = "SONY"
                    logger.info(
                        f"[相机EXIF] 品牌推断自Sony镜头: "
                        f"{exif_data.get('LensModel', '')}"
                    )

        # MakerNote ModelID
        mn_model = exif_data.pop("_mn_model", "")
        if not exif_data.get("Model"):
            if mn_model and 3 < len(mn_model) < 100:
                exif_data["Model"] = mn_model
                logger.info(f"[相机EXIF] Model回退自MakerNote ModelID: {mn_model}")
            else:
                for key in (
                    "Image Model",
                    "EXIF Model",
                    "UniqueCameraModel",
                    "LocalizedCameraModel",
                    "CameraModel",
                    "Model",
                ):
                    val = exif_data.get(key, "")
                    if val and 3 < len(val) < 100:
                        exif_data["Model"] = val
                        logger.info(f"[相机EXIF] Model回退自'{key}': {val}")
                        break

        if not exif_data.get("Model"):
            # 有 Make + 序列号时，显示品牌名 + 机身，比空白好
            sn = exif_data.get("BodySerialNumber") or exif_data.get("SerialNumber")
            make = exif_data.get("Make", "")
            if sn and make:
                exif_data["Model"] = "机身"
                logger.info(f"[相机EXIF] Model退化: {make} 机身 (序列号: {sn})")
            else:
                logger.info(
                    f"[相机EXIF] Model缺失: Make={make}, "
                    f"候选键={[k for k in exif_data if 'model' in k.lower() or 'camera' in k.lower()]}"
                )

    # ================================================================
    # 全文搜索相机型号（最后兜底）
    # ================================================================

    @staticmethod
    def _is_valid_model_string(s: str) -> bool:
        """验证字符串是否像真实的相机型号（而非二进制垃圾字节）。

        条件：
        1. 至少 90% 字符是 ASCII 可打印字符
        2. 不包含控制字符
        3. 包含至少一个数字或公认字母组合

        Args:
            s: 候选型号字符串

        Returns:
            True 如果看起来像真实型号
        """
        if not s or len(s) < 3 or len(s) > 80:
            return False
        printable = sum(1 for c in s if 32 <= ord(c) < 127)
        ratio = printable / len(s)
        if ratio < 0.88:  # 至少有 88% 可打印 ASCII
            return False
        # 检查是否包含常见的无效模式（纯随机字节的典型特征）
        garbage_count = sum(1 for c in s if ord(c) > 127)
        if garbage_count > len(s) * 0.15:  # 超过15%非ASCII → 垃圾
            return False
        # 必须包含字母
        has_alpha = any(c.isalpha() for c in s)
        if not has_alpha:
            return False
        return True

    def _search_raw_file_for_model(self, exif_data: dict[str, Any]) -> None:
        """直接读取文件原始字节，搜索相机品牌/型号字符串。

        这是最后的兜底方案，当所有 EXIF 解析器都无法找到 IFD0 Make/Model 时，
        直接在文件二进制中搜索已知相机型号模式。
        特别适用于 Canon（Make/Model 在 MakerNote 中）的场景。

        Args:
            exif_data: EXIF 数据字典（原地修改）
        """
        if not self.file_path or not os.path.isfile(self.file_path):
            return
        try:
            with open(self.file_path, "rb") as fh:
                raw = fh.read()
        except Exception:
            return

        text = raw.decode("latin-1", errors="ignore")

        # 已知相机型号模式 — 从文件全部字节中搜索
        # 优先级：完整型号 > 简短型号
        patterns: list[tuple[str, str, str]] = [
            # (正则, Make值, 说明)
            # Canon EOS + PowerShot
            (r"Canon\s+EOS\s+[\w\d]+\s*Mark?\s*[IVX\d]*", "Canon", "Canon EOS (完整)"),
            (r"Canon\s+EOS\s+[\w\d]+", "Canon", "Canon EOS (简短)"),
            (
                r"Canon\s+PowerShot\s+[\w\d]+\s*[A-Z]*\s*[IVX\d]*",
                "Canon",
                "Canon PowerShot",
            ),
            # Nikon
            (
                r"NIKON\s+(?:CORPORATION\s+)?[\w\d]+[\s\w\d]*",
                "NIKON CORPORATION",
                "NIKON",
            ),
            (r"NIKON\s+[\w\d]+\s*[\w\d]*", "NIKON CORPORATION", "NIKON short"),
            # Sony Alpha
            (r"SONY\s+[\w\d\-]+", "SONY", "SONY"),
            (r"ILCE-[\w\d]+", "SONY", "Sony ILCE"),
            (r"SLT-[\w\d]+", "SONY", "Sony SLT"),
            # Fujifilm
            (r"FUJIFILM\s+[\w\d\-]+", "FUJIFILM", "FUJIFILM"),
            (r"X-[\w\d]+", "FUJIFILM", "Fuji X series"),
            (r"GFX\s*[\w\d]+", "FUJIFILM", "Fuji GFX"),
            # Olympus / OM System
            (r"OLYMPUS\s+[\w\d\-]+", "OLYMPUS", "OLYMPUS"),
            (r"OM-[\w\d]+", "OLYMPUS", "OM System"),
            # Pentax
            (r"PENTAX\s+[\w\d\-]+", "PENTAX", "PENTAX"),
            # Hasselblad
            (r"HASSELBLAD\s+[\w\d\-]+", "HASSELBLAD", "HASSELBLAD"),
            # Leica
            (r"LEICA\s+[\w\d\-]+", "LEICA", "LEICA"),
            # Panasonic
            (r"Panasonic\s+[\w\d\-]+", "Panasonic", "Panasonic"),
            (r"DC-[\w\d]+", "Panasonic", "Panasonic DC"),
            # Samsung
            (r"SAMSUNG\s+[\w\d\-]+", "SAMSUNG", "SAMSUNG"),
            (r"NX[\w\d]+", "SAMSUNG", "Samsung NX"),
            # Sigma
            (r"SIGMA\s+[\w\d\s\-]+", "SIGMA", "SIGMA"),
            (r"fp[\s\d]*[\w\d]*", "SIGMA", "Sigma fp"),
            # Generic: Manufacturer + Model string
            (r"Canon\s+[\w\d][\w\d\s\-]{2,40}", "Canon", "Canon generic"),
            (r"NIKON\s+[\w\d][\w\d\s\-]{2,40}", "NIKON CORPORATION", "NIKON generic"),
        ]

        for pattern, make_val, desc in patterns:
            if exif_data.get("Make") and exif_data.get("Model"):
                break
            if (
                exif_data.get("Make")
                and exif_data["Make"].upper() not in make_val.upper()
            ):
                continue
            if exif_data.get("Model") and len(exif_data["Model"].strip()) > 3:
                continue

            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                found = m.group(0).strip()
                if 4 < len(found) < 80 and self._is_valid_model_string(found):
                    if not exif_data.get("Make") or not exif_data["Make"].strip():
                        exif_data["Make"] = make_val
                    if not exif_data.get("Model") or len(found) > len(
                        exif_data.get("Model", "")
                    ):
                        exif_data["Model"] = found
                    logger.info(
                        f"[相机EXIF] 全文搜索: {desc} → Make={exif_data.get('Make', '')} Model={exif_data.get('Model', '')}"
                    )
                    if exif_data.get("Make") and exif_data.get("Model"):
                        break

    # ================================================================
    # GPS 提取
    # ================================================================

    def _extract_gps(self, exif_data: dict[str, Any]) -> dict[str, Any]:
        """提取并格式化 GPS 信息。

        支持 exifread 和 PIL 两种来源的 GPS 标签。

        Args:
            exif_data: EXIF 数据字典

        Returns:
            GPS 信息 {latitude, longitude, altitude, map_url}
        """
        gps: dict[str, Any] = {}

        # 从 exifread 标签
        lat = None
        lon = None
        lat_ref = exif_data.get(
            "GPS GPSLatitudeRef", exif_data.get("GPSLatitudeRef", "")
        )
        lon_ref = exif_data.get(
            "GPS GPSLongitudeRef", exif_data.get("GPSLongitudeRef", "")
        )
        lat_raw = exif_data.get("GPS GPSLatitude", exif_data.get("GPSLatitude"))
        lon_raw = exif_data.get("GPS GPSLongitude", exif_data.get("GPSLongitude"))
        alt_raw = exif_data.get("GPS GPSAltitude", exif_data.get("GPSAltitude"))

        if lat_raw and lon_raw:
            try:
                if hasattr(lat_raw, "values"):
                    lat = convert_to_degrees(lat_raw.values)
                if hasattr(lon_raw, "values"):
                    lon = convert_to_degrees(lon_raw.values)

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
                                rational_to_float(
                                    alt_raw.values[0]
                                    if hasattr(alt_raw, "values")
                                    else alt_raw
                                )
                                or 0,
                                1,
                            )
                        except Exception:
                            pass

                    provider, custom_url = self._map_provider
                    gps["map_url"] = build_gps_map_url(lat, lon, provider, custom_url)
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
                    lat = convert_to_degrees(lat_raw)
                    lon = convert_to_degrees(lon_raw)
                    if lat_ref and "S" in str(lat_ref).upper():
                        lat = -lat
                    if lon_ref and "W" in str(lon_ref).upper():
                        lon = -lon
                    gps["latitude"] = round(lat, 6)
                    gps["longitude"] = round(lon, 6)
                    provider, custom_url = self._map_provider
                    gps["map_url"] = build_gps_map_url(lat, lon, provider, custom_url)
                except Exception:
                    pass

        return gps

    # ================================================================
    # XMP 提取
    # ================================================================

    @staticmethod
    def _extract_xmp_basics(exif_data: dict[str, Any]) -> dict[str, str]:
        """从 XMLPacket / XMP 数据中提取基础信息。

        Args:
            exif_data: EXIF 数据字典

        Returns:
            XMP 信息 {key: value}
        """
        xmp: dict[str, str] = {}
        raw = exif_data.get("XMLPacket", "")
        if not raw:
            return xmp
        try:
            raw_str = str(raw)
            for tag_pattern, key in [
                (r"<xmp:CreatorTool>([^<]+)</xmp:CreatorTool>", "CreatorTool"),
                (r"<xmp:CreateDate>([^<]+)</xmp:CreateDate>", "CreateDate"),
                (r"<xmp:Rating>([^<]+)</xmp:Rating>", "Rating"),
                (r"""tiff:Make\s*=\s*["']([^"']+)["']""", "XmpMake"),
                (r"""tiff:Model\s*=\s*["']([^"']+)["']""", "XmpModel"),
                (r"""aux:SerialNumber\s*=\s*["']([^"']+)["']""", "XmpSerial"),
            ]:
                m = re.search(tag_pattern, raw_str)
                if m:
                    xmp[key] = m.group(1).strip()
        except Exception:
            pass
        return xmp

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _coerce_makernote_bytes(values: Any) -> bytes:
        """将 MakerNote 值强制转换为字节。

        Args:
            values: exifread 的 values 属性

        Returns:
            字节数据
        """
        if isinstance(values, bytes):
            return values
        if isinstance(values, (list, tuple)):
            raw_bytes = bytearray()
            for b in values:
                try:
                    if isinstance(b, int):
                        raw_bytes.append(b & 0xFF)
                    elif isinstance(b, bytes):
                        raw_bytes.extend(b)
                    elif hasattr(b, "numerator") and hasattr(b, "denominator"):
                        raw_bytes.append(b.numerator & 0xFF)
                except Exception:
                    pass
            if raw_bytes:
                return bytes(raw_bytes)
        return str(values).encode()
