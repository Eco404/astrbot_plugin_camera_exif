"""
astrbot_plugin_camera_exif 核心库
提供 EXIF 解析、快门次数提取、RAW/DNG 格式支持等功能模块。
"""

from .constants import (
    RAW_EXTENSIONS,
    ALL_IMAGE_EXTS,
    SHUTTER_COUNT_TAGS,
    VENDOR_SIGNATURES,
    DNG_TAGS,
    EXIF_TAG_NAMES_CN,
    EXPOSURE_PROGRAMS,
    METERING_MODES,
    FLASH_STATUS,
    CONFIG_TO_TAG,
    FIELD_COMMAND_MAP,
    FIELD_CN_NAMES,
)
from .utils import (
    convert_to_degrees,
    clean_tag_value,
    format_fraction,
    rational_to_float,
)
from .ifd_scanner import UnifiedIfdScanner
from .dng_parser import DngParser
from .shutter import (
    find_shutter_count,
    find_shutter_count_from_exif,
    parse_makernote_binary,
)
from .formatter import (
    format_display_text,
    format_full_exif_text,
    format_shutter_only,
)
from .exif_analyzer import ExifAnalyzer

__all__ = [
    # Analyzer
    "ExifAnalyzer",
    # Scanner
    "UnifiedIfdScanner",
    # DNG Parser
    "DngParser",
    # Shutter
    "find_shutter_count",
    "find_shutter_count_from_exif",
    "parse_makernote_binary",
    # Formatter
    "format_display_text",
    "format_full_exif_text",
    "format_shutter_only",
    # Constants
    "RAW_EXTENSIONS",
    "ALL_IMAGE_EXTS",
    "SHUTTER_COUNT_TAGS",
    "VENDOR_SIGNATURES",
    "DNG_TAGS",
    "EXIF_TAG_NAMES_CN",
    "EXPOSURE_PROGRAMS",
    "METERING_MODES",
    "FLASH_STATUS",
    "CONFIG_TO_TAG",
    "FIELD_COMMAND_MAP",
    "FIELD_CN_NAMES",
    # Utils
    "convert_to_degrees",
    "clean_tag_value",
    "format_fraction",
    "rational_to_float",
]
