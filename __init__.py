"""
astrbot_plugin_camera_exif —— AstrBot 相机EXIF分析插件
自动检测接收的图片是否为相机拍摄，提取并分析EXIF数据。

模块结构:
    main.py              - 插件主入口 (CameraExifPlugin ← Star)
    libs/constants.py    - 常量定义（标签映射、厂商签名、DNG标签等）
    libs/utils.py        - 工具函数（GPS转换、标签清理、缩略图生成）
    libs/ifd_scanner.py  - 统一TIFF IFD扫描器（跨厂商MakerNote/DNG解析）
    libs/dng_parser.py   - DNG格式专用解析器（60+ 标准标签）
    libs/shutter.py      - 快门次数提取（4层策略 + 厂商二进制解析）
    libs/formatter.py    - 输出格式化（摘要/完整/字段查询）
    libs/exif_analyzer.py- EXIF分析引擎主类（7步分析管线）
"""

from .main import CameraExifPlugin

__all__ = ["CameraExifPlugin"]
