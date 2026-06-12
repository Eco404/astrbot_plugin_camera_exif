"""
相机EXIF分析插件 — DNG 格式专用解析器模块

DNG (Digital Negative) 是 Adobe 制定的开放 RAW 格式标准，基于 TIFF/EP 6.0。
Leica、Pentax、DJI、Apple ProRAW、GoPro、Hasselblad 等厂商原生支持 DNG。

本模块提供：
- DNG 标准标签提取（DNGVersion、UniqueCameraModel、CameraSerialNumber 等）
- DNG PrivateData 解析（厂商私有数据映射）
- DNG 文件中的快门次数提取
- 厂商来源推断（Make/UniqueCameraModel → 厂商名）
- DNG 相机校准、色彩矩阵等高级元数据
"""

from __future__ import annotations

import re
import struct
from typing import Any

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

from astrbot.api import logger


class DngParser:
    """DNG 文件格式专用解析器。

    支持：
    - 标准 DNG 标签提取（40+ 标签）
    - DNG PrivateData 解析
    - 快门次数提取（MakerNote / DNG PrivateData）
    - 厂商来源推断
    - Apple ProRAW、DJI、Leica、Pentax、GoPro 等特殊处理
    """

    # DNG 版本 → 发布日期
    DNG_VERSIONS: dict[str, str] = {
        "1.0.0.0": "2004-09 (DNG 1.0)",
        "1.1.0.0": "2005-02 (DNG 1.1)",
        "1.2.0.0": "2008-04 (DNG 1.2)",
        "1.3.0.0": "2009-06 (DNG 1.3)",
        "1.4.0.0": "2012-10 (DNG 1.4)",
        "1.5.0.0": "2019-05 (DNG 1.5)",
        "1.6.0.0": "2021-10 (DNG 1.6)",
        "1.7.0.0": "2023-06 (DNG 1.7)",
    }

    # DNG 私有数据厂商标识 → 厂商名
    PRIVATE_DATA_VENDORS: dict[bytes, str] = {
        b"MakN": "NIKON",  # Nikon MakerNote preserved
        b"CanN": "Canon",  # Canon MakerNote preserved
        b"Sony": "SONY",  # Sony MakerNote preserved
        b"Fuji": "FUJIFILM",
        b"Olym": "OLYMPUS",
        b"Pana": "Panasonic",
        b"Pent": "PENTAX",
        b"Leic": "LEICA",
        b"Hass": "HASSELBLAD",
        b"Appl": "APPLE",  # Apple ProRAW
        b"DJI\x00": "DJI",  # DJI
        b"GoPr": "GOPRO",
        b"SamS": "SAMSUNG",
    }

    def __init__(self, file_path: str) -> None:
        """初始化 DNG 解析器。

        Args:
            file_path: DNG 文件路径
        """
        self.file_path = file_path
        self.file_ext = ""
        if file_path:
            import os

            self.file_ext = os.path.splitext(file_path)[1].lower()

    # ================================================================
    # 主解析方法
    # ================================================================

    def parse(self) -> dict[str, Any]:
        """解析 DNG 文件，提取所有可用元数据。

        Returns:
            结构化解析结果:
            {
                "is_dng": bool,
                "dng_version": str,
                "dng_tags": dict,          # DNG 标准标签
                "vendor": str,              # 推断厂商
                "private_data": dict,        # DNG PrivateData 解析结果
                "shutter_count": int | None,
                "calibration": dict,         # 校准数据摘要
                "raw_info": dict,            # RAW 数据信息
            }
        """
        result: dict[str, Any] = {
            "is_dng": False,
            "dng_version": "",
            "dng_tags": {},
            "vendor": "",
            "private_data": {},
            "shutter_count": None,
            "calibration": {},
            "raw_info": {},
        }

        if not self.file_path or not HAS_PIL:
            return result

        try:
            # PIL 方式提取 DNG 标签
            img = PILImage.open(self.file_path)
            img.load()

            exif = img.getexif()
            if not exif:
                img.close()
                return result

            tags_found: dict[int, Any] = {}

            # 从 IFD0 提取
            for tag_id, value in exif.items():
                tags_found[tag_id] = value

            # 从子 IFD 提取
            for ifd_id in (0x8769, 0x8825, 0xA005):
                try:
                    sub = exif.get_ifd(ifd_id)
                    if sub:
                        for tid, val in sub.items():
                            if tid not in tags_found:
                                tags_found[tid] = val
                except Exception:
                    pass

            img.close()

            # 解析 DNG 版本
            dng_ver_raw = tags_found.get(0xC612, b"")
            if dng_ver_raw:
                result["is_dng"] = True
                try:
                    if isinstance(dng_ver_raw, bytes):
                        ver_nums = struct.unpack("4B", dng_ver_raw[:4])
                        result["dng_version"] = ".".join(str(n) for n in ver_nums)
                    else:
                        result["dng_version"] = str(dng_ver_raw)
                except Exception:
                    result["dng_version"] = "unknown"

            if not result["is_dng"]:
                # 也可能是 camera-native DNG（无 DNGVersion 标签）
                make = self._tag_to_str(tags_found.get(0x010F, ""))
                model = self._tag_to_str(tags_found.get(0x0110, ""))
                unique = self._tag_to_str(tags_found.get(0xC614, ""))
                if unique or self.file_ext == ".dng":
                    result["is_dng"] = True
                    logger.info(
                        f"[相机EXIF] DNG: 无DNGVersion标签，"
                        f"Make={make}, UniqueCameraModel={unique}"
                    )

            if not result["is_dng"]:
                return result

            # 提取 DNG 标准标签
            result["dng_tags"] = self._extract_dng_tags(tags_found)

            # 推断厂商
            result["vendor"] = self._infer_vendor(tags_found)

            # 解析 DNG PrivateData
            private_raw = tags_found.get(0xC634, b"")
            if private_raw:
                result["private_data"] = self._parse_private_data(
                    private_raw, result["vendor"]
                )

            # 提取快门次数
            result["shutter_count"] = self._extract_shutter_count(tags_found)

            # 校准数据
            result["calibration"] = self._extract_calibration(tags_found)

            # RAW 信息
            result["raw_info"] = self._extract_raw_info(tags_found)

            logger.info(
                f"[相机EXIF] DNG解析完成: ver={result['dng_version']}, "
                f"vendor={result['vendor']}, "
                f"tags={len(result['dng_tags'])}, "
                f"shutter={result['shutter_count']}"
            )

        except Exception as e:
            logger.warning(f"[相机EXIF] DNG解析异常: {e}")

        return result

    # ================================================================
    # DNG 标签提取
    # ================================================================

    def _extract_dng_tags(self, tags_found: dict[int, Any]) -> dict[str, str]:
        """从原始标签中提取 DNG 标准标签。

        Args:
            tags_found: {tag_id: raw_value} 字典

        Returns:
            {标签英文名: 格式化值} 映射
        """
        from .constants import DNG_TAGS

        result: dict[str, str] = {}
        for tag_id, (en_name, cn_name) in DNG_TAGS.items():
            raw_val = tags_found.get(tag_id)
            if raw_val is not None:
                formatted = self._format_dng_value(raw_val, tag_id)
                if formatted:
                    result[en_name] = formatted
                    # 同时存储中文名
                    result[f"{en_name}_CN"] = cn_name

        # 同时提取标准 EXIF 标签（在 DNG 文件中也有）
        std_tags = {
            0x010F: "Make",
            0x0110: "Model",
            0x010E: "ImageDescription",
            0x0131: "Software",
            0x0132: "DateTime",
            0x0112: "Orientation",
            0x013B: "Artist",
            0x8298: "Copyright",
            0x9003: "DateTimeOriginal",
            0xA432: "LensSpecification",
            0xA433: "LensMake",
            0xA434: "LensModel",
            0xA435: "LensSerialNumber",
        }
        for tid, name in std_tags.items():
            raw = tags_found.get(tid)
            if raw is not None and name not in result:
                result[name] = self._tag_to_str(raw)

        return result

    # ================================================================
    # 厂商推断
    # ================================================================

    def _infer_vendor(self, tags_found: dict[int, Any]) -> str:
        """从标签推断 DNG 文件的原始相机厂商。

        优先级：UniqueCameraModel ≈ Make > DNGPrivateData 厂商标识

        Args:
            tags_found: {tag_id: raw_value} 字典

        Returns:
            厂商名（大写）
        """
        from .constants import DNG_VENDOR_PATTERNS

        make = self._tag_to_str(tags_found.get(0x010F, "")).upper()
        model = self._tag_to_str(tags_found.get(0x0110, "")).upper()
        unique = self._tag_to_str(tags_found.get(0xC614, "")).upper()

        for candidate in (unique, model, make):
            if not candidate:
                continue
            for keyword, vendor in DNG_VENDOR_PATTERNS.items():
                if keyword.upper() in candidate:
                    logger.info(f"[相机EXIF] DNG厂商推断: {keyword} → {vendor}")
                    return vendor

        # 尝试从 Apple 特有标签判断
        if tags_found.get(0xC67B):  # SemanticInstanceID (Apple ProRAW)
            return "APPLE"

        # 从 DJI 特有数据判断
        if b"DJI" in str(make).encode() or b"DJI" in str(model).encode():
            return "DJI"

        return "UNKNOWN"

    # ================================================================
    # 快门次数提取
    # ================================================================

    def _extract_shutter_count(self, tags_found: dict[int, Any]) -> int | None:
        """从 DNG 文件中提取快门次数。

        策略：
        1. DNG 标准标签中的快门信息（CameraSerialNumber 可辅助定位）
        2. MakerNote 保留的快门数据
        3. DNG PrivateData 中的快门数据
        4. 厂商特定 Nikon/Canon MakerNote 在 DNG 中的保留

        Args:
            tags_found: {tag_id: raw_value} 字典

        Returns:
            快门次数或 None
        """
        # 策略1: MakerNote 二进制扫描
        mn_raw = tags_found.get(0x927C, b"")
        if mn_raw and isinstance(mn_raw, bytes) and len(mn_raw) > 20:
            sc = self._scan_makernote_in_dng(mn_raw)
            if sc:
                logger.info(f"[相机EXIF] DNG快门(MakerNote): {sc}")
                return sc

        # 策略2: exifread 补充解析
        sc = self._try_exifread_shutter()
        if sc:
            return sc

        # 策略3: DNG PrivateData 中的快门
        private_raw = tags_found.get(0xC634, b"")
        if private_raw and isinstance(private_raw, bytes) and len(private_raw) > 20:
            sc = self._scan_private_data_shutter(private_raw)
            if sc:
                logger.info(f"[相机EXIF] DNG快门(PrivateData): {sc}")
                return sc

        return None

    def _scan_makernote_in_dng(self, mn_raw: bytes) -> int | None:
        """扫描 DNG 中保留的 MakerNote 二进制数据获取快门次数。

        Args:
            mn_raw: MakerNote 原始二进制数据

        Returns:
            快门次数或 None
        """
        from .ifd_scanner import UnifiedIfdScanner
        from .constants import VENDOR_SIGNATURES

        vendor_found = ""
        ifd_start = 0

        # 搜索厂商签名
        for sig, ifd_off, vendor_name in VENDOR_SIGNATURES:
            idx = mn_raw.find(sig)
            if idx >= 0:
                vendor_found = vendor_name
                if vendor_name == "NIKON":
                    ifd_start = idx + 10
                elif vendor_name == "AOC":
                    ifd_start = idx + 4
                else:
                    ifd_start = idx + ifd_off
                break

        if not vendor_found:
            # 通用 TIFF 头搜索
            tiff = UnifiedIfdScanner.find_tiff_header(mn_raw)
            if tiff:
                ifd_start, byte_order = tiff
                vendor_found = "(unknown)"
            else:
                return None

        if ifd_start + 8 >= len(mn_raw):
            return None

        # 确定字节序
        tiff_magic = mn_raw[ifd_start : ifd_start + 4]
        bo: str = "<"
        if tiff_magic[:2] == b"MM":
            bo = ">"
        elif tiff_magic[:2] == b"II":
            bo = "<"

        sc = UnifiedIfdScanner.scan_for_shutter(mn_raw, ifd_start, bo, vendor_found)
        if sc:
            return sc

        # Canon 回退
        if not vendor_found or vendor_found == "(unknown)":
            sc = UnifiedIfdScanner.scan_canon_fallback(mn_raw)
            if sc:
                return sc

        return None

    def _try_exifread_shutter(self) -> int | None:
        """使用 exifread 库尝试从 DNG 文件提取快门。

        Returns:
            快门次数或 None
        """
        if not HAS_EXIFREAD:
            return None
        try:
            with open(self.file_path, "rb") as f:
                tags = exifread.process_file(f, details=True, debug=False)

            from .constants import SHUTTER_COUNT_KEYWORDS

            for tag_name, tag_value in tags.items():
                key_lower = tag_name.lower()
                val_str = str(tag_value)

                for kw in SHUTTER_COUNT_KEYWORDS:
                    if kw in key_lower:
                        try:
                            sc = int(float(val_str))
                            if 100 <= sc <= 9999999:
                                return sc
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass
        return None

    def _scan_private_data_shutter(self, private_raw: bytes) -> int | None:
        """扫描 DNG PrivateData 中的快门数据。

        DNG PrivateData 格式:
            [厂商标识(4B)] [数据长度(4B)] [厂商私有数据(可变)]

        Args:
            private_raw: DNG PrivateData 原始字节

        Returns:
            快门次数或 None
        """
        from .ifd_scanner import UnifiedIfdScanner

        if len(private_raw) < 8:
            return None

        try:
            vendor_id = private_raw[:4]
            data_len = struct.unpack(">I", private_raw[4:8])[0]
            vendor_data = private_raw[8 : 8 + data_len]

            # 尝试用 TIFF IFD 扫描
            sc = UnifiedIfdScanner.scan_canon_fallback(vendor_data)
            if sc:
                return sc

            # 搜索 TIFF 头
            tiff = UnifiedIfdScanner.find_tiff_header(vendor_data)
            if tiff:
                ifd_start, bo = tiff
                sc = UnifiedIfdScanner.scan_for_shutter(
                    vendor_data, ifd_start, bo, "UNKNOWN"
                )
                if sc:
                    return sc

            # latin-1 文本搜索
            try:
                text = vendor_data.decode("latin-1", errors="ignore")
                for kw in ("ShutterCount", "shuttercount", "TotalShutter"):
                    idx = text.find(kw)
                    if idx >= 0:
                        nums = re.findall(r"(\d{3,7})", text[idx : idx + 50])
                        if nums:
                            sc = int(nums[0])
                            if 100 <= sc <= 9999999:
                                return sc
            except Exception:
                pass

        except Exception:
            pass

        return None

    # ================================================================
    # PrivateData 解析
    # ================================================================

    def _parse_private_data(self, raw_data: bytes, vendor: str) -> dict[str, Any]:
        """解析 DNG PrivateData 中的厂商私有数据。

        Args:
            raw_data: DNG PrivateData 原始字节
            vendor: 厂商名

        Returns:
            解析结果
        """
        result: dict[str, Any] = {}
        if not raw_data or len(raw_data) < 8:
            return result

        try:
            vendor_id = raw_data[:4]
            data_len = struct.unpack(">I", raw_data[4:8])[0]
            vendor_data = raw_data[8 : 8 + data_len]

            vid_str = vendor_id.decode("ascii", errors="replace").rstrip("\x00")
            result["vendor_id"] = vid_str

            vendor_name = self.PRIVATE_DATA_VENDORS.get(vendor_id, vid_str)
            result["vendor_name"] = vendor_name
            result["data_size"] = data_len

            # 厂商专用解析
            if vendor_id == b"Appl":  # Apple ProRAW
                result.update(self._parse_apple_private(vendor_data))
            elif vendor_id == b"DJI\x00":
                result.update(self._parse_dji_private(vendor_data))
            elif vendor_id == b"MakN":  # Nikon MakerNote
                result["note"] = "Nikon MakerNote preserved in DNG"
            elif vendor_id == b"GoPr":
                result.update(self._parse_gopro_private(vendor_data))

        except Exception as e:
            logger.debug(f"[相机EXIF] DNG PrivateData解析异常: {e}")

        return result

    def _parse_apple_private(self, data: bytes) -> dict[str, Any]:
        """解析 Apple ProRAW 私有数据。

        Args:
            data: 私有数据字节

        Returns:
            解析结果
        """
        result: dict[str, Any] = {"apple_features": []}
        try:
            text = data.decode("latin-1", errors="ignore")
            # Smart HDR
            if "Smart HDR" in text or "HDR" in text:
                result["apple_features"].append("Smart HDR")
            # Deep Fusion
            if "Deep Fusion" in text or "DeepFusion" in text:
                result["apple_features"].append("Deep Fusion")
            # Night Mode
            if "Night" in text or "night" in text:
                result["apple_features"].append("Night Mode")
            # Semantic Segmentation
            if "Semantic" in text:
                result["apple_features"].append("语义分割")
        except Exception:
            pass
        return result

    def _parse_dji_private(self, data: bytes) -> dict[str, Any]:
        """解析 DJI 私有元数据（航拍姿态等）。

        Args:
            data: 私有数据字节

        Returns:
            解析结果
        """
        result: dict[str, Any] = {}
        try:
            text = data.decode("latin-1", errors="ignore")
            # 云台角度
            gimbal = re.findall(
                r"(?:gimbal|pitch|roll|yaw)[\s:=]+([-\d.]+)", text, re.I
            )
            if gimbal:
                result["gimbal_data"] = gimbal
            # 飞行参数
            if "altitude" in text.lower() or "高度" in text:
                result["flight_data"] = "含飞行参数"
            # 曝光包围
            if "AEB" in text or "bracket" in text.lower():
                result["aeb"] = "含自动包围曝光数据"
        except Exception:
            pass
        return result

    def _parse_gopro_private(self, data: bytes) -> dict[str, Any]:
        """解析 GoPro 私有元数据。

        Args:
            data: 私有数据字节

        Returns:
            解析结果
        """
        result: dict[str, Any] = {}
        try:
            text = data.decode("latin-1", errors="ignore")
            if "WDR" in text:
                result["wdr"] = True
            if "HyperSmooth" in text:
                result["hypersmooth"] = True
            if "IMU" in text or "gyro" in text.lower():
                result["imu_data"] = "含陀螺仪数据"
        except Exception:
            pass
        return result

    # ================================================================
    # 校准数据提取
    # ================================================================

    def _extract_calibration(self, tags_found: dict[int, Any]) -> dict[str, Any]:
        """提取 DNG 相机校准相关数据摘要。

        Args:
            tags_found: {tag_id: raw_value} 字典

        Returns:
            校准数据摘要
        """
        cal: dict[str, Any] = {}

        cal_tags = {
            0xC627: "BaselineExposure",  # 基准曝光
            0xC628: "BaselineNoise",  # 基准噪声
            0xC629: "BaselineSharpness",  # 基准锐度
            0xC619: "BlackLevel",  # 黑电平
            0xC61A: "WhiteLevel",  # 白电平
            0xC625: "AsShotNeutral",  # 中性色
        }

        for tid, name in cal_tags.items():
            val = tags_found.get(tid)
            if val is not None:
                cal[name] = self._format_dng_value(val, tid)

        return cal

    # ================================================================
    # RAW 信息提取
    # ================================================================

    def _extract_raw_info(self, tags_found: dict[int, Any]) -> dict[str, Any]:
        """提取 DNG RAW 数据基本信息。

        Args:
            tags_found: {tag_id: raw_value} 字典

        Returns:
            RAW 信息
        """
        info: dict[str, Any] = {}

        w = tags_found.get(0x0100)
        h = tags_found.get(0x0101)
        if w and h:
            try:
                info["width"] = int(w)
                info["height"] = int(h)
            except (ValueError, TypeError):
                pass

        # 有效区域
        active = tags_found.get(0xC63C)
        if active:
            info["active_area"] = str(active)

        # 原始文件名
        orig = tags_found.get(0xC63A)
        if orig:
            info["original_raw"] = self._tag_to_str(orig)

        # 色彩
        color_matrix = tags_found.get(0xC61E)
        if color_matrix:
            info["has_color_matrix"] = True

        return info

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _tag_to_str(value: Any) -> str:
        """将标签值转为字符串。"""
        if value is None:
            return ""
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="replace").rstrip("\x00")
            except Exception:
                return str(value)
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value).strip().rstrip("\x00")

    @staticmethod
    def _format_dng_value(value: Any, tag_id: int) -> str:
        """格式化 DNG 标签值。

        Args:
            value: 原始标签值
            tag_id: 标签 ID

        Returns:
            格式化后的字符串
        """
        if value is None:
            return ""

        # DNGVersion: bytes → "x.x.x.x"
        if tag_id == 0xC612 and isinstance(value, bytes):
            try:
                return ".".join(str(b) for b in value[:4])
            except Exception:
                return str(value)

        # 字节转换为字符串
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="replace").rstrip("\x00")
            except Exception:
                try:
                    return value.decode("latin-1", errors="replace").rstrip("\x00")
                except Exception:
                    return f"<binary:{len(value)}B>"

        # 数值类型
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value == int(value):
                return str(int(value))
            return str(value)

        # 分数类型
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            try:
                from .utils import format_fraction

                return format_fraction(value)
            except Exception:
                return str(value)

        # 元组/列表
        if isinstance(value, (tuple, list)):
            items = []
            for v in value[:10]:  # 最多10个元素
                if hasattr(v, "numerator") and hasattr(v, "denominator"):
                    from .utils import format_fraction

                    items.append(format_fraction(v))
                else:
                    items.append(str(v))
            return ", ".join(items)

        return str(value).strip()

    # ================================================================
    # 扩展方法：生成 to_exif_data 格式的字典
    # ================================================================

    def to_exif_data(self) -> dict[str, str]:
        """将 DNG 解析结果转换为标准 EXIF 数据格式（供 ExifAnalyzer 合并使用）。

        Returns:
            {标签名: 值} 映射
        """
        parsed = self.parse()
        if not parsed["is_dng"]:
            return {}

        data: dict[str, str] = {}
        dng_tags = parsed.get("dng_tags", {})

        # 标准 EXIF 标签映射
        key_map = {
            "Make": "Make",
            "Model": "Model",
            "Software": "Software",
            "DateTimeOriginal": "DateTimeOriginal",
            "Artist": "Artist",
            "Copyright": "Copyright",
            "LensModel": "LensModel",
            "LensMake": "LensMake",
            "LensSerialNumber": "LensSerialNumber",
            "Orientation": "Orientation",
        }

        for dng_key, exif_key in key_map.items():
            val = dng_tags.get(dng_key, "")
            if val:
                data[exif_key] = val

        # DNG 特有信息
        if parsed["dng_version"]:
            data["DNGVersion"] = parsed["dng_version"]

        unique_model = dng_tags.get("UniqueCameraModel", "")
        if unique_model:
            data["UniqueCameraModel"] = unique_model
            # 如果 Make 缺失，从 UniqueCameraModel 推断
            if not data.get("Make") and unique_model:
                for kw, brand in [
                    ("Leica", "LEICA"),
                    ("Hasselblad", "HASSELBLAD"),
                    ("DJI", "DJI"),
                    ("Apple", "APPLE"),
                    ("GoPro", "GoPro"),
                    ("Pentax", "PENTAX"),
                    ("RICOH", "PENTAX"),
                    ("Samsung", "SAMSUNG"),
                ]:
                    if kw.upper() in unique_model.upper():
                        data["Make"] = brand
                        break

        # 相机序列号 (DNG 标准)
        cam_sn = dng_tags.get("CameraSerialNumber", "")
        if cam_sn:
            data["BodySerialNumber"] = cam_sn

        # 原始文件名
        orig = dng_tags.get("OriginalRawFileName", "")
        if orig:
            data["OriginalRawFileName"] = orig

        # 快门次数
        if parsed["shutter_count"]:
            data["_dng_shutter"] = str(parsed["shutter_count"])

        # 校准数据摘要
        cal = parsed.get("calibration", {})
        for k, v in cal.items():
            if v:
                data[f"DNG_{k}"] = str(v)

        return data
