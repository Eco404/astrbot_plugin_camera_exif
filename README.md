# 📸 相机EXIF分析插件

AstrBot 插件 — 自动检测接收的图片是否为相机拍摄，提取并分析 EXIF 数据，支持 RAW 格式，快门次数提取，19 种字段单独查询指令，权限隔离与频率限制。

> 作者：**昊天兽王** | v1.0.0

## ✨ 功能特性

- 🔍 **自动检测**：收到图片/文件时自动分析，仅相机图片触发回复（可关闭）
- 📊 **EXIF 解析**：提取 45+ 种元数据字段（品牌、型号、光圈、快门、ISO、焦距等）
- 📷 **快门次数**：支持 Canon / Nikon / Sony / Fujifilm / Olympus / Pentax / Panasonic
- 🎞️ **RAW 格式**：CR2/CR3、NEF、ARW、RAF、ORF、RW2、PEF、DNG、SRW 等 20+ 种
- 🔐 **权限隔离**：每人只能查自己的图片（可配置），跨群查询自动隔离
- 🛡️ **安全防护**：频率限制、分析超时、路径校验、临时文件自动清理
- ⚙️ **可视化配置**：WebUI 控制所有选项，`options` 下拉选择器
- 📋 **19 种字段指令**：中英文/大小写别名全覆盖，支持@引用图片查询
- 📨 **QQ 合并转发**：转发发送模式使用 OneBot v11 Node 组件

## 📋 全部指令

### 综合查询

| 指令 | 别名 | 说明 |
|------|------|------|
| `/exif帮助` | `/exif help` `/exif菜单` `/exif menu` | 显示帮助菜单 |
| `/exif` | — | 查询完整 EXIF 元数据 |

### 字段单独查询

| 指令 | 别名 | 查询内容 |
|------|------|----------|
| `/快门次数` | `/快门次数查询` `/shuttercount` `/SC` | 快门使用次数(如 2863) |
| `/快门` | `/快门速度` `/曝光时间` `/shutterspeed` `/shutter` | 快门速度/曝光时间(如 1/25s) |
| `/相机型号` | `/型号` `/model` | 相机型号 |
| `/相机品牌` | `/品牌` `/make` | 相机品牌/制造商 |
| `/镜头型号` | `/镜头` `/lens` | 镜头型号 |
| `/镜头品牌` | `/lensmake` | 镜头品牌 |
| `/焦距` | `/focal` `/focallength` | 拍摄焦距 |
| `/光圈` | `/光圈值` `/aperture` `/fnumber` | 光圈值 |
| `/ISO` | `/iso` `/感光度` `/ISOSpeed` | ISO 感光度 |
| `/测光模式` | `/测光` `/metering` | 测光模式 |
| `/曝光模式` | `/曝光` `/exposure` | 曝光模式 |
| `/曝光补偿` | `/EV` `/ev` `/exposurebias` | 曝光补偿值 |
| `/闪光灯` | `/闪光` `/flash` | 闪光灯状态 |
| `/白平衡` | `/wb` `/whitebalance` | 白平衡设置 |
| `/拍摄时间` | `/时间` `/datetime` `/date` | 原始拍摄时间 |
| `/机身序列号` | `/序列号` `/serial` `/sn` | 机身序列号 |
| `/图片尺寸` | `/分辨率` `/size` `/resolution` | 图片尺寸 |
| `/处理软件` | `/软件` `/software` | 后期处理软件 |
| `/GPS` | `/位置` `/定位` `/gps` | GPS 位置信息 |

## 🚀 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/haotianshouwang/astrbot_plugin_camera_exif.git
cd astrbot_plugin_camera_exif
pip install -r requirements.txt
```

## 📦 依赖

| 依赖 | 必需 | 说明 |
|------|------|------|
| Pillow | ✅ | 基础 EXIF 读取 |
| exifread | ✅ | 详细标签解析、MakerNote 快门提取 |
| rawpy | ❌ 可选 | RAW 文件深度解析（需 libraw） |

## ⚙️ 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 插件总开关 |
| `auto_detect_enabled` | bool | true | 自动检测开关 |
| `reply_mode` | select | 文本发送 | 文本发送 / 转发发送 / 不发送 |
| `forward_display_name` | string | 相机EXIF分析 | 转发发送时的外显名称 |
| `allow_reference_query` | bool | false | 允许引用查询他人图片 |
| `show_analyzing_hint` | bool | true | 分析时显示提示 |
| `show_detailed_exif_default` | bool | false | 默认显示完整 EXIF |
| `max_image_size_mb` | int | 50 | 最大处理图片大小(MB) |
| `display_fields.*` | 18 项 bool | — | 自定义显示字段开关 |
| `group_chat_filter.mode` | string | all | 群聊过滤：all/whitelist/blacklist |
| `private_chat_filter.mode` | string | all | 私聊过滤：all/whitelist/blacklist |
| `raw_format_config.*` | object | — | RAW 格式解析配置 |

## 📸 支持的 RAW 格式

Canon (CR2/CR3/CRW)、Nikon (NEF/NRW)、Sony (ARW/SRF/SR2)、Fujifilm (RAF)、Olympus (ORF)、Panasonic (RW2)、Pentax (PEF/DNG)、Leica (RAW/RWL)、Samsung (SRW)、Sigma (X3F)、Adobe (DNG) 等

## 🔐 安全特性

- **权限隔离**：缓存 Key = 会话 + 用户，跨用户跨群隔离
- **引用控制**：`allow_reference_query` 控制别人能否引用你的图查 EXIF
- **频率限制**：自动检测 10s/3 次，指令 10s/5 次
- **分析超时**：45 秒超时防止 RAW 文件阻塞
- **路径安全**：临时文件删除前校验路径范围
- **文件清理**：分析后立即删除临时下载文件
- **GPS 隐私**：显示 GPS 时追加隐私提醒

## 📄 License

MIT
