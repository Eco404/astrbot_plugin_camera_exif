# CHANGELOG

## [v1.2.0] - 2026-06-12

### 新增
- **DNG 格式深度解析**：`DngParser` 模块，支持 80+ DNG 标准标签、PrivateData 厂商私有数据
- **Canon MakerNote ModelID 解析**：从 MakerNote Tag 0x0010 直接提取 EOS 机身型号（70+ 机型映射）
- **全文搜索兜底**：直接读取原始文件字节搜索相机品牌/型号字符串
- **GPS 多地图支持**：高德/百度/腾讯/Google/OSM，自动坐标系转换（WGS-84→GCJ-02/BD-09）
- **智能合并引擎**：多源提取器并行运行 + 按优先级和值质量合并去重
- **数据来源追踪**：每个 EXIF 标签标注来源（pil/exifread/dng/xmp/makernote）
- **DNG RAW 数据过滤**：自动过滤 Profile/校准查找表等超大 RAW 数据，防止 QQ 转发超限
- **回复模式自动降级**：文本超 8000 字时自动省略非核心标签，防止刷屏

### 优化
- **架构重构**：`main.py` 拆分为 8 个模块（constants/utils/ifd_scanner/dng_parser/shutter/formatter/exif_analyzer）
- **Sony 品牌识别**：修复 "E " 关键词误匹配 iPhone 的问题（新增词边界检测）
- **模型退化**：无机身型号时显示品牌名 + 序列号，而非空白
- **DNG 兼容版本**：修正版本号字节显示为可读格式

### 文档
- README 更新架构图、GPS 配置、DNG 支持说明
- 新增已知问题：QQ 图片传输使 IFD0 EXIF 标签丢失
