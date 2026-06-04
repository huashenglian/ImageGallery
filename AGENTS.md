# 图形化图库项目架构

## 核心入口
- `main.py` - 程序入口，初始化应用并迁移旧文件
- `app_meta.py` - 定义APP_VERSION版本号与CHANGELOG更新日志
- `paths.py` - 路径常量管理与旧缓存文件迁移

## 数据模型层
- `models/gallery.py` - 图库数据模型，含Gallery类、scan_gallery_tree扫描树构建
- `models/gallery_view.py` - 视图模型，DisplayImage类、build_gallery_view_rows展平逻辑

## 业务服务层
- `services/cache_manager.py` - 多缓存管理，CacheManager类、list_entries缓存列表
- `services/gallery_scanner.py` - 图库扫描引擎，full_scan_drives全盘扫描、incremental_scan增量扫描
- `services/reveal_path.py` - 跨平台文件管理器定位，reveal_in_file_manager函数
- `services/thumbnail_cache.py` - 缩略图缓存，ThumbnailCache类、ensure_thumbnail生成缩略图
- `services/model_manager.py` - 模型管理，ModelManager类、classify_image推理
- `services/models_registry.py` - 内置模型注册表，BUILTIN_MODELS常量、load_model_config
- `services/category_presets.py` - 分类预设管理，CategoryPreset类、get_default_preset
- `services/classifier.py` - 标签分类映射，map_label_to_category、classify_multi_label_set
- `services/classify_cache.py` - 分类结果缓存，ClassifyCache类、get_category_counts

## 用户界面层
- `ui/main_window.py` - 主窗口与页面调度，MainWindow类、start_scan开始扫描
- `ui/start_page.py` - 启动页，缓存列表与扫描控件
- `ui/gallery_page.py` - 图库展示页，GalleryPage类、_on_tile_clicked选择逻辑、_tile双击打开、_SelectableGrid框选
- `ui/classify_page.py` - 分类结果展示页，分类网格与进度控制
- `ui/image_preview.py` - 图片预览对话框，ImagePreviewDialog类
- `ui/properties_dialog.py` - 图片/图库属性对话框，详细文件信息展示
- `ui/image_tags_dialog.py` - 图片标签对话框，图片标签管理
- `ui/help_dialog.py` - 帮助对话框，版本信息与更新日志
- `ui/theme.py` - 主题配色与SVG图标，build_tile_qss、SVG_*常量

## 后台工作线程
- `workers/cache_scan_worker.py` - 缓存扫描线程，FullCacheScanWorker类、IncrementalCacheScanWorker类
- `workers/classify_worker.py` - 分类工作线程，ClassifyWorker类、分类进度保存
- `workers/thumb_worker.py` - 缩略图生成线程，ThumbTask类、global_thumb_pool

## 项目说明
这是一个基于Python+PySide6的图形化图库管理应用，支持本地图片扫描、缩略图缓存、多级图库导航、图片预览、图片/图库属性查看、排序、多选框选，以及基于ONNX模型的自动分类功能。

## 构建项目注意事项
1. 确保依赖安装完整：`pip install -r requirements.txt`
2. 优先确保基础架构（数据模型、基础服务）稳定后再开发UI层
3. 修改UI交互时注意与现有功能兼容性
4. 分类功能依赖模型文件，注意model_cache目录完整性
5. 每构建一个功能/模块/阶段都要检查一遍是否有效、没问题，再继续构建；当前需求全部完成时，再检查当前任务构建的所有代码，是否存在问题导致无法运行或不符合预期
