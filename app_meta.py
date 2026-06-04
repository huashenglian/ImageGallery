"""Application version and changelog."""

APP_VERSION = "0.17"

CHANGELOG = """
0.17
----
· 分类缓存后端从 JSON+gzip 迁移到 SQLite：
  - 新增 classify_result.db 数据库（WAL 模式），替代 classify_result.json.gz
  - 主表 classify_cache：image_path 主键 + category/label/confidence/model_id/fallback_used/classified_at
  - 元数据表 classify_meta：键值对存储版本、进度、预设等
  - 新增 category 和 model_id 索引，GROUP BY 聚合查询替代全量遍历
  - WAL 模式支持分类线程写入与 UI 线程读取并发，无阻塞
  - 首次运行自动检测旧 JSON 缓存并导入 SQLite，迁移成功后删除旧文件
  - update() 即时写入变更行，无需全量序列化；maybe_save()/flush() 仅持久化元数据
  - set_progress() 改为内存缓存，延迟到 flush 时写入 SQLite，减少 I/O
  - 新增 close() 方法显式关闭数据库连接，MainWindow.closeEvent 中调用
  - 所有 SQL 查询使用参数化占位符，防止 SQL 注入
  - 公共 API 完全兼容，classify_worker/classify_page/gallery_page 无需修改
· ModelManager 新增批量推理方法（保留供未来使用）：
  - batch_predict、batch_classify_image、batch_classify_image_top_n
  - 修复 batch_predict 预处理失败时结果顺序错位 bug
· 代码清理：
  - 移除 classify_worker.py 中未使用的导入（clear_unified_cache、is_project_path）
· 修改文件：services/classify_cache.py、services/model_manager.py、workers/classify_worker.py、ui/main_window.py

0.16
----
· 分类缓存后端从 JSON+gzip 迁移到 SQLite：
  - 新增 classify_result.db 数据库（WAL 模式），替代 classify_result.json.gz
  - 主表 classify_cache：image_path 主键 + category/label/confidence/model_id/fallback_used/classified_at
  - 元数据表 classify_meta：键值对存储版本、进度、预设等
  - 新增 category 和 model_id 索引，GROUP BY 聚合查询替代全量遍历
  - WAL 模式支持分类线程写入与 UI 线程读取并发，无阻塞
  - 首次运行自动检测旧 JSON 缓存并导入 SQLite，迁移成功后删除旧文件
  - update() 即时写入变更行，无需全量序列化；maybe_save()/flush() 仅持久化元数据
  - 新增 close() 方法显式关闭数据库连接，MainWindow.closeEvent 中调用
  - 所有 SQL 查询使用参数化占位符，防止 SQL 注入
  - 公共 API 完全兼容，classify_worker/classify_page/gallery_page 无需修改
· 修改文件：services/classify_cache.py、ui/main_window.py

0.15
----
· 修复「分类模型配置」进度条显示问题：
  - 下载进度条：替换 findChild 查找为直接引用字典 _dl_progress_widgets，避免刷新后找到已删除控件
  - 量化进度条：在 _handle_quantize_progress 中显式检查可见性并自动显示
  - 进度条最小高度设置为 20px，确保视觉可见
  - 下载完成信号新增 model_size 参数，避免对话框访问 _active_downloads 失败
  - _refresh_model_lists() 开头清除 _dl_progress_widgets，防止内存泄漏
· 修复分类过程中启用/禁用模型无法动态生效：
  - ClassifyWorker 新增 _refresh_active_models() 方法，每5秒检查一次启用模型列表
  - resume() 重置 _last_refresh_time，确保恢复分类后立即刷新
  - 新增 _initial_model_configs 配置快照，分类过程中模型详细配置保持不变
  - 启用模型变更时：加载新模型、释放被移除模型、重建 UnifiedCategoryBuilder
  - 模型加载失败自动跳过，不影响分类继续
  - 所有模型被禁用时自动中止分类
· 修复模型排序中模型无法下移问题：
  - _on_move_model() 中确保 order 包含所有已安装模型（不再只依赖已排序模型）
  - 过滤掉已卸载模型的 ID，保持 order 列表干净
  - 每次移动前自动补充所有新安装模型到 order 末尾
· 修改文件：workers/classify_worker.py、ui/main_window.py

0.14
----
· 多模型协同分类系统：
  - 新增统一类别映射构建器（UnifiedCategoryBuilder），将所有启用模型的类别体系合并为统一映射
  - 支持同名类别合并（提示词取并集去重）
  - 支持同提示词不同类别冲突解决（按模型优先级决定，日志记录冲突）
  - 支持独有类别自动补充
  - 统一映射内存缓存，配置变更时自动清除
  - 新增可配置回退级别：-1=全部模型回退，0=仅主模型，N=最多回退N次（默认2）
  - 新增模型排序功能：用户可通过▲▼按钮调整模型优先级，影响冲突解决和回退顺序
  - ClassifyWorker 重构为主模型优先+回退策略，按回退级别按需加载模型
  - 分类缓存新增 model_id 和 fallback_used 字段
  - ModelConfigDialog 新增回退级别控件和模型排序控件
  - ClassifyPage CATEGORY_ICONS 新增女性/男性/多人/文档文字图标
· 修复 MobileNet-V3 分类模型规格化失效：
  - 根因：MobileNet-V3 输出层名为 "output"（非 "logits"），不触发 sigmoid
  - 修复：对 preprocess_type == "imagenet" 的模型，在 sigmoid 未应用时改用 softmax 归一化（数值稳定版本）
  - Softmax 确保概率值在 [0, 1] 范围内且总和为 1
· 修复清除所有缓存后分类界面未重置：
  - 根因：清除缓存时未停止正在运行的分类 Worker，Worker 继续发射信号覆盖了空状态
  - 修复：在 _on_clear_all_caches() 开头增加对运行中分类 Worker 的 request_stop() + wait(3000) + 置空操作
  - 确保 Worker 停止后再清除缓存和重置 UI
· 新增文件：services/classifier.py::UnifiedCategoryBuilder
· 修改文件：services/models_registry.py、services/classify_cache.py、workers/classify_worker.py、ui/main_window.py、ui/classify_page.py

0.13.3
-----
· 修复分类暂停后重启程序无法继续分类的问题：
  - ClassifyCache.has_incomplete() 同时检查 incomplete 标志和进度值
  - 关闭程序时先保存进度再请求停止分类线程
  - 增加分类线程等待超时时间到 5 秒
  - ClassifyPage._set_buttons_for_paused() 显式设置所有按钮可见性
· 新增图库排序功能：
  - 排序选项：名称、分辨率、格式、创建时间、修改时间
  - 正序/倒序切换按钮（带文字标识和提示）
  - 图库卡片也支持排序（按名称或创建/修改时间）
  - DisplayImage 新增 resolution、format、created_time、modified_time 字段
· 新增图片/图库属性对话框：
  - 图片属性：类型、大小、占用空间、分辨率、位置、创建/修改/访问时间
  - 图库属性：类型、大小、占用空间、位置、创建时间、包含子图库和图片数量（递归统计）
  - 右键菜单"查看路径"替换为"属性"
· 图片预览对话框显示分辨率
· 图片右键菜单新增"默认查看器打开"（使用 os.startfile 安全打开）
· 网格卡片固定宽度，不再随窗口拉伸
· 递归统计子图库和图片数量：
  - models/gallery_view.py 新增 count_sub_galleries_recursive() 和 count_images_recursive()
· 修复大图片导致的 PIL DecompressionBombWarning：
  - 在读取图片元数据时临时设置 Image.MAX_IMAGE_PIXELS = None
· 分类进度实时更新到图库界面：
  - 当分类进行中时，图库界面可实时看到新增图片
· 缩略图线程信号异常处理：
  - 包裹信号发射到 try/except RuntimeError，避免窗口关闭时报错
· 新增文件：ui/properties_dialog.py

0.13.2
-----
· 重构多标签模型分类逻辑为集合交集匹配（set-based classification）：
  - 旧逻辑：逐标签迭代匹配 —— 遍历 top-30 标签，对每个标签调用 map_label_to_category
    做关键词子串匹配，首个命中即返回。问题在于单标签视野狭窄，属性标签
    （如 long_hair、smile、blush）占据高置信度位置但无法映射到有效分类，
    尽管「1girl」「scenery」等分类标签也在列表中却因阈值或匹配顺序被错过。
  - 新逻辑（classify_multi_label_set）：先将所有高于阈值的标签收集为归一化集合，
    再对每个分类的关键词集合做交集匹配（集合 & 运算）。多个标签共同印证一个分类，
    避免单标签噪音。集合交集失败时自动回退到子串匹配兜底。
  - 增强调试日志：打印高于阈值的完整标签集合、命中的分类及命中标签、子串匹配详情、
    未命中时的全部标签列表，符合需求文档的调试输出规范。
· 新增 services/classifier.py::classify_multi_label_set() 函数。
· classify_worker.py 的 _classify_multi_label() 改为调用 classify_multi_label_set()。

0.13.1
-----
· 修复所有图片仍分类到「其他」的根因（第二轮修复）：
  - Bug 1（核心）：scan_installed_models 中 builtin 模型 category_map_type 迁移条件
    `not config.preset_name` 永远为 False（from_dict 默认 preset_name="ImageNet 通用分类"），
    导致 WD14 模型的 category_map_type 无法从旧值更新为「WD14 动漫标签」。
    修复：改为检测 category_map_type 是否为内置通用类型（imagenet/danbooru/generic），
    若与 builtin default_map 不一致则自动迁移，不再依赖 preset_name 判断。
  - Bug 2：auto_detect_category_map 对用户导入的动漫模型返回 "danbooru" 而非
    「WD14 动漫标签」，导致使用 Danbooru 硬编码映射而非更全面的 WD14 预设。
    修复：返回 CATEGORY_MAP_WD14。
  - Bug 3：map_label_to_category 的关键词匹配未做下划线归一化，当标签含下划线
    而关键词含空格（或反之）时匹配失败。修复：匹配前统一将下划线替换为空格。
· 增强 WD14 预设关键词覆盖度：
  - 女性分类新增 anime_coloring、flat_color。
  - NSFW 分类新增 nsfw。
  - 新增「文档/文字」分类（text、signature、watermark、logo、english_text 等）。
· 改进分类调试日志：
  - 多标签模型日志从 top-10 扩展到 top-30。
  - 每个标签的映射结果从 debug 级别提升到 info 级别，便于排查。
  - 分类开始时打印每个模型的 map_type、threshold、label 数量等配置信息。

0.13.0
-----
· 新增文本展开编辑功能：在所有输入框中按 Shift+Ctrl+T 可弹出可调整大小的文本编辑窗口。
  - 文本窗口继承输入框当前内容，支持多行编辑。
  - 点击「确定」关闭窗口并将修改后的内容同步回输入框。
  - 直接关闭窗口时弹出确认提示「内容将不会修改，是否确认关闭？」。
  - 适用于模型名称、标签名称、提示词、预设名称等所有输入框。

0.12.3
-----
· 修复 WD14 ConvNext Tagger 全部分类到「其他」的根因：
  - Bug 1（核心）：标签文件 URL 指向 CSV，但保存为 .json 后缀。CSV 内容被按行解析，
    第一行 header（tag_id,name,category,count）导致所有标签索引偏移+1，标签与模型输出完全错位。
    修复：下载后自动检测文件内容是否为 CSV，若是则正确解析并转换为 JSON 格式。
  - Bug 2：旧配置未迁移 preprocess_type。WD14 模型在代码更新前下载的配置仍为 preprocess_type="imagenet"，
    导致 _classify_wd14 方法不被调用。修复：scan_installed_models 对内置模型自动检测并更新配置。
  - Bug 3：WD14 ONNX 模型从 Keras 导出时已包含 sigmoid 激活层，手动再应用 sigmoid 导致
    所有置信度被压缩到 0.5~0.73 窄范围。修复：移除手动 sigmoid 计算。

0.12.2
-----
· 修复 WD14 ConvNext Tagger 全部分类到「其他」的问题：
  - 根因：WD14 是多标签模型，top-1 输出常为属性标签（如 long_hair、smile），而非分类标签（如 1girl、scenery）。
  - 新增 classify_image_top_n 方法，对 WD14 模型检查 top-30 标签，找到第一个可映射到非「其他」分类的标签。
  - 新增 _classify_wd14 方法在 ClassifyWorker 中专门处理 WD14 推理逻辑。
· 新增「WD14 动漫标签」默认预设（6个分类）：
  - 女性：1girl、solo、girl、female、woman、long_hair、breasts、dress、skirt 等
  - 男性：1boy、boy、male、man、muscular 等
  - 风景：no_humans、scenery、landscape、sky、outdoors、nature 等
  - NSFW：nude、explicit、questionable、sensitive 等
  - 多人：2girls、2boys、multiple_girls、multiple_boys、group、couple 等
  - 其他
· WD14 模型默认使用「WD14 动漫标签」预设（而非 Danbooru 动漫标签）。

0.12.1
-----
· 修复添加自定义标签报错 TypeError（QPushButton.clicked 信号传递 bool 参数）。
· 修复「重新分类」无法重新分类已分类图片：新增 reclassify_requested 信号，重新分类时先清空缓存再分类。
· 修复导入模型后界面不自动刷新：导入后立即刷新模型列表并强制处理事件循环。
· 模型配置中模型名称改为可选中复制的只读输入框。
· 每个自定义标签行右侧增加红色「删除」按钮。
· 新增「删除预设」按钮：可删除当前选中的自定义预设（默认预设不可删除，需确认）。
· 替换 DeepDanbooru 为 WD14 ConvNext Tagger（SmilingWolf/wd-v1-4-convnext-tagger-v2，388MB，Apache-2.0）。
  - 支持 ONNX 多标签推理（sigmoid 输出），自动检测高分辨率+多标签模型并切换预处理方式。
  - 标签文件从 CSV 自动转换为 JSON 格式。
  - 下载源：hf-mirror.com（主）/ huggingface.co（镜像）。

0.12.0
-----
· 多模型分类系统：支持同时使用多个分类模型，自动取置信度最高的非「其他」分类结果。
  - 新增内置模型注册表（ModelRegistryEntry）：MobileNet-V3-Small (12MB)、MobileNet-V3-Large (20.9MB)、DeepDanbooru（待适配）。
  - 新增「分类模型配置」弹窗（编辑菜单 → 分类模型配置）：查看已安装模型、启用/禁用、下载内置模型、导入本地 ONNX 文件。
  - 下载进度显示（百分比、速度、镜像加速标识），支持切换 hf-mirror / HuggingFace 源。
  - 模型自动验证（onnxruntime InferenceSession 检测输入/输出规格），自动生成标签文件。
· 分类预设系统：支持自定义标签映射，可将自定义标签保存为新预设。
  - 新增内置预设：ImageNet 通用分类、Danbooru 动漫标签、通用（无映射）。
  - 自定义预设管理：在模型详细配置中可添加/删除自定义标签（标签名称 + 提示词），读取预设至自定义栏编辑，保存为新预设。
  - 预设名称冲突检测：输入默认预设名显示「无法掩盖默认预设」，输入已有自定义预设名显示「将覆盖已有预设」。
  - 预设持久化至 model_cache/presets/，支持分类映射的动态加载与缓存刷新。
· 分类图库界面同步自定义标签：图库类型根据当前预设动态显示（如风景/文字/人物/其他 → 动漫/女性/动物/纯色）。
  - ClassifyCache 新增 preset_name 字段追踪使用的预设。
  - ClassifyWorker 分类时写入预设名到缓存，ClassifyPage 根据预设名获取动态分类列表。
· 新增文件：services/models_registry.py、services/category_presets.py。
· 重写文件：services/classifier.py（动态映射缓存）、workers/classify_worker.py（多模型并行）、ui/main_window.py 的 ModelConfigDialog/ModelDetailDialog。

0.11.0
-----
· 修复 ModuleNotFoundError: No module named 'models':
  - MODEL_DIR 从 models/ 改为 model_cache/，避免与 Python 包目录冲突。
  - 迁移逻辑：程序启动时自动将旧 models/ 下的 model.onnx 和 imagenet_classes.txt 移动到 model_cache/。
  - 重建缺失的 models/__init__.py、models/gallery.py、models/gallery_view.py。
· 修复 PIL DecompressionBombWarning: 在 model_manager.py._preprocess() 中添加 Image.MAX_IMAGE_PIXELS = None。
· 修复点击「删除缩略图」报错：删除 PreferencesDialog._on_clear_thumbnails() 中对 self._status 的引用（该属性不存在）。
· 修复分类图库中包含缩略图路径的问题：
  - ClassifyCache.load() 自动调用 _remove_project_paths()，过滤 cache/、thumbnails/、logs/、model_cache/ 路径下的图片。
  - 自动清理并重新保存分类缓存。
· 修复 Bug1 - 空文件夹被错误显示为图库：
  - models/gallery.py 的 build_gallery_tree_from_cache() 和 _build_subtree() 新增过滤条件：仅当子图库包含图片或子图库时才被保留。
· 修复 Bug2 - 图库深度展平算法错误：
  - 完全重写 models/gallery_view.py 的展平逻辑，按需求文档规范实现：
    - depth=-1：完整层级，不展平。
    - depth=0：根目录直接显示所有图片（带来源标签），无子图库入口。
    - depth=N（N≥0）：前 N 层保留为文件夹；当导航到第 N 层时，该层下图片全部展平并标注来源。
    - depth ≥ max_depth：等同于 -1（完整层级）。
  - 新增 collect_images_with_source() 递归收集图片并标注来源子图库路径。
  - DisplayImage 新增 source_folder 字段。
  - ui/gallery_page.py 的 tile 显示时，若来源文件夹非空，则在标题下方附加「[来源文件夹]」标签。
· 修复模型下载效率问题：主 URL 改为 hf-mirror.com，标签主 URL 改为 CDN 加速，连接超时/读取超时均缩短（分别为 8 秒/15 秒）。
· 新增 paths.py._migrate_model_dir() 用于自动迁移旧的模型文件到 model_cache/。

0.10.0
-----
· 新增图片自动分类功能（基于 MobileNetV3-Small ONNX 模型）：
  - 首选项新增「自动分类模型」区域：一键下载/卸载模型（约10MB），自动切换镜像加速。
  - 下载进度显示（百分比、速度、镜像标识），支持取消下载。
  - 模型下载后自动验证完整性，卸载时同步清空分类缓存。
  - 新增「分类完成后释放模型内存」开关（默认开启，节省约100MB）。
  - 启动页底部新增「🤖 自动分类」按钮（仅模型已安装时显示）。
  - 分类图库界面：三种状态（空/分类中/结果），支持暂停/继续/中断。
  - 分类进度：进度条、已处理/总数、当前文件名。
  - 分类结果：8类（人物、风景、动物、食物、建筑、车辆、截图/文字、其他）卡片网格展示。
  - 点击分类卡片进入该类别的图片网格视图。
  - 分类缓存持久化（classify_result.json.gz），支持增量分类。
  - 置信度阈值 0.3，低于阈值归入「其他」。
  - 图片损坏自动跳过并记录，分类完成后显示跳过数量。
· 新增依赖：onnxruntime>=1.16
· 新增文件：services/model_manager.py、services/classifier.py、services/classify_cache.py、
  workers/classify_worker.py、ui/classify_page.py
· paths.py 新增 MODEL_DIR 常量，ensure_dirs() 自动创建 models/ 目录

0.9.3
-----
· 修复启动时图库网格默认为深蓝色而非已保存颜色：GalleryPage 不再从 QSettings 独立读取颜色，
  改为由 MainWindow 构造时直接传入（PreferencesDialog.base_color()），确保与全局主题使用同一颜色源。
· PreferencesDialog._save() 新增 sync() 调用，强制立即写入 QSettings 防止延迟落盘导致读取不一致。
· 修复切换颜色后部分背景框仍为白色/浅灰：
  - 全局 QSS 恢复 QWidget{{background:{bg}}}（之前为修复卡片颜色问题而移除），确保子控件继承背景色。
    卡片通过 #imgTile / #subTile ID 选择器（Qt QSS 最高优先级）安全覆盖，不会被 QWidget 规则影响。
  - QStackedWidget、StartPage、QScrollArea viewport 等无显式背景的控件不再使用平台默认白色调色板。
  - _CacheCard 添加 objectName(#cacheCard) + 全局 QSS 规则，使用 _surface 色值生成卡片背景和悬停效果。
· 修复大量硬编码文字颜色（#888/#999/#555），替换为 _text_dim / _border 主题派生函数：
  - StartPage: _scan_path_label、_no_cache_label、_cache_section_label
  - GalleryPage: _depth_label、_loading_label、面包屑分隔符
  - PreferencesDialog: 提示标签、颜色选择按钮边框
  - StartPage/GalleryPage 新增 update_theme/set_base 中同步更新这些颜色。
· StartPage 新增 base 构造参数和 update_theme() 方法，MainWindow 在构造和主题切换时同步传入。

0.9.2
-----
· 修复图库网格颜色不随界面颜色变化（核心修复）：
  - 使用 #objectName ID 选择器替代 QFrame/QWidget 类型选择器，确保最高优先级。
  - _Tile 使用 #imgTile，_SubGalleryTile 使用 #subTile。
  - 缩略图占位符使用 #thumbPlaceholder / #subThumbPlaceholder。
  - 堆叠卡片背景使用 #stackBack1 / #stackBack2。
  - 全局 QLabel{background:transparent} 改为 QMainWindow QLabel 限定范围，避免覆盖卡片内标签。

0.9.1
-----
· 修复图库网格颜色不随界面颜色变化：全局样式表 QWidget{background:bg} 会级联覆盖卡片自身的 QFrame 样式。
  将 QWidget 的 background 移至 QMainWindow，各控件单独设置背景色。
  卡片样式表同时声明 QFrame 和 QWidget 选择器确保优先级。
· 新增 QDialog、QScrollArea 的显式背景色设置。

0.9.0
-----
· 全新多缓存文件系统：每个扫描路径生成独立缓存文件，存放在 ./cache/ 目录。
  - 缓存命名规则：D_Photos_galleries_cache.json.gz / all_disks_galleries_cache.json.gz
  - 路径转安全文件名：冒号去除，反斜杠转下划线，连续下划线合并。
· 缓存冲突处理：
  - 全盘扫描自动清理所有单路径缓存（弹窗确认）。
  - 扫描父路径时自动清理子路径缓存。
  - 扫描子路径时复用父路径缓存，不新建。
· 启动页重设计：显示已缓存图库列表（路径名、图片数、文件夹数、最后扫描时间）。
  - 点击缓存卡片直接进入图库。
  - 右键菜单：删除缓存 / 重新扫描。
  - "清除所有缓存"按钮。
· 单路径多线程扫描：新增 scan_single_path()，复用全盘扫描的多线程并行逻辑。
· 动态线程数：首选项新增"非全盘动态线程"开关，根据子目录数量自动调整线程数。
· 扫描跳过项目目录：自动跳过 ./cache/、./thumbnails/、./logs/ 等项目自身目录。
· 路径管理重构：
  - 新增 paths.py：PROJECT_ROOT、CACHE_DIR、THUMBNAIL_DIR、LOG_DIR。
  - 缩略图从系统临时目录迁移至 ./thumbnails/。
  - 旧缓存文件自动迁移至 ./cache/all_disks_galleries_cache.json.gz。
  - 项目文件夹可移动，所有路径自动适配。
· 删除旧文件：gallery_cache_store.py → cache_manager.py，scan_worker.py → 合并入 cache_scan_worker.py。

0.8.1
-----
· 修复纯黑/纯白背景下颜色派生失效：QColor.darker/lighter 是乘法操作，纯黑(V=0)乘任何数仍为0，导致区块与背景完全一样无法区分。
  改用 HSV 加法偏移：surface ±0.10、border ±0.22、hover ±0.05，确保极端颜色下仍有清晰边界。
· 修复 _accent_dark 同样使用乘法 darker 的问题，改为 HSV 减法偏移。

0.8.0
-----
· 全新颜色系统：用户选取的颜色即为界面背景色，所有其他颜色自动派生。
  - 亮色背景（如白色）→ 区块颜色自动变深（如灰色），文字自动变黑。
  - 暗色背景（如深蓝）→ 区块颜色自动变浅，文字自动变白。
  - 点缀色从背景色色相自动派生（饱和+鲜亮），无色背景默认蓝色点缀。
· 颜色派生函数：_is_light / _text / _surface / _border / _hover / _accent。
· 首选项「点缀颜色」更名为「界面颜色」，QSettings 键改为 appearance/base_color。
· 图库卡片、子图库堆叠、缩略图占位符、面包屑、标题文字均跟随界面颜色自动调整。
· 删除旧的 _accent_border / _accent_surface / _accent_hover / _darker 函数。

0.7.1
-----
· 修复菜单栏重复堆叠：更改颜色后菜单栏不断复制（5→10→15），现改为重建前先清除旧菜单。
· 删除半透明深色样式及亚克力模糊功能（移除 _try_acrylic、WA_TranslucentBackground、showEvent 等）。
· 删除首选项中的「界面显示样式」下拉框，仅保留扫描线程数和点缀颜色设置。

0.7.0
-----
· 修复颜色切换不生效：默认深色样式下更换点缀颜色后，窗口所有颜色均跟随变化（按钮背景、边框、滚动条、菜单等）。
· 新增 HSV 色彩派生函数（_accent_border / _accent_surface / _accent_hover），从点缀色自动生成边框色、表面色、悬停色。
· 图库卡片、子图库堆叠卡片、缩略图占位符背景均跟随点缀颜色变化。
· 面包屑导航栏背景和边框跟随点缀颜色变化。
· 新增小房子图标按钮（返回启动页），位于图库界面「返回」按钮与标题之间，透明背景+悬停高亮。
· 删除「界面」菜单中的「返回启动页」选项（改用小房子按钮）。

0.6.1
-----
· 首选项新增 RGB 颜色选择器：可自定义点缀颜色（按钮、滚动条、选中效果等），通过 QColorDialog 选取。
· 修复半透明深色样式无效：设置 WA_TranslucentBackground 属性 + rgba 半透明背景 + AccentState=4 亚克力模糊。
· 点缀颜色通过 QSettings 持久化，重启后保留。
· 切换颜色/样式后立即刷新整个界面（菜单图标、卡片、面包屑等）。

0.6.0
-----
· 全局暗色主题：深色背景 #1a1a2e，浅色文字 #e0e0e0，红色点缀 #e94560。
· 图库卡片圆角 12px、悬停高亮边框、QPropertyAnimation 淡入效果。
· 按钮：圆角 8px、悬停变红、按下变深红。
· 滚动条：细条样式，宽度 8px，悬停变红。
· 内嵌 SVG 图标：文件夹、图片、返回箭头、搜索、设置（代码内定义，无外部文件）。
· Windows 亚克力模糊背景：首选项可切换「半透明深色」样式。
· 面包屑导航暗色适配。

0.5.1
-----
· 缩略图缓存自动清理：增量扫描完成后自动删除已不存在图片对应的缩略图，防止缓存无限增长。
· 切换缩略图尺寸时自动清理旧尺寸缓存，避免多尺寸缩略图同时堆积。
· 新增 purge_orphans / purge_other_sizes 方法，精确按 SHA256 前缀匹配清理。

0.5.0
-----
· 启动页快捷盘符新增「所有盘（从缓存加载）」选项，直接加载缓存跳转图库界面。
· 图库界面新增路径导航栏（面包屑），显示当前导航路径，点击任意层级可快速跳转。
· 新增「界面」菜单：包含「返回启动页」和「返回已缓存图库」，无需重新扫描即可返回图库。
· 「返回已缓存图库」在启动页和图库界面均可使用，自动从缓存加载。

0.4.1
-----
· 修复从缓存加载图库时 RecursionError（_build_node 无限递归）：添加 visited 集合防止循环引用。
· 修复 _scan_tree_worker 未过滤符号链接/junction，可能导致缓存数据中出现循环。
· 修复 Path.name 在极端递归深度下的 AttributeError（添加 try/except 回退）。

0.4.0
-----
· 新增右下角「中断扫描」按钮：扫描时显示红色中断按钮，点击后立即停止扫描、不生成缓存、留在启动页。
· 修复文件夹扫描未利用缓存的问题：全盘扫描后再次扫描指定文件夹，若该路径已在缓存中则直接从缓存加载，无需重新扫描磁盘。

0.3.1
-----
· 修复全盘扫描报错 '<=' not supported between instances of 'MainWindow' and 'int'（Worker 构造参数位置错误）。
· 新增「编辑 → 首选项」菜单（快捷键 Ctrl+,），可设置扫描线程数（1~32，默认 8）。
· 线程数设置通过 QSettings 持久化，重启后保留。

0.3.0
-----
· 多线程并行扫描：使用 ThreadPoolExecutor（默认 8 线程）并行扫描盘符一级子目录，每个线程独立 os.walk() 一棵目录树。
· 系统目录过滤：自动跳过 Windows、Program Files、ProgramData、$Recycle.Bin 等系统目录，仅扫描用户数据。
· 优先扫描用户文件夹：Users、Desktop 及非 C 盘目录优先调度，更快呈现用户图片。
· 预编译后缀元组：图片匹配使用 IMAGE_SUFFIX_TUPLE + str.endswith()，避免逐文件创建 Path 对象。
· queue.Queue 进度回传：子线程通过队列回传扫描结果，主线程实时更新进度（已完成/总目录树数）。
· 可配置线程数：FullCacheScanWorker / IncrementalCacheScanWorker 支持 max_workers 参数。
· 增量扫描与发现新文件夹同样应用系统目录过滤。

0.2.0
-----
· 无限滚动自动加载：移除"加载更多"按钮，滚动到底部自动加载下一页图片。
· 响应式图片网格布局：窗口宽度变化时自动重新计算列数，无横向滚动条。
· 删除图片后保持滚动位置：仅移除被删除项，后续图片自动补位，不跳回顶部。
· 缩略图尺寸切换：工具栏下拉框 / 视图菜单可切换超大图标(256px)、大图标(160px)、中等图标(96px)、小图标(64px)。
· 子图库卡片内部几何按比例缩放，适配不同缩略图尺寸。

0.1.1
-----
· 新增「帮助」菜单：显示版本号、缓存文件路径与更新日志。
· 子图库角标显示「子图库数量 + 所含图片总数（含子目录）」。
· 新增「图库深度展平视图」：可设置 depth（≥-1），在视图层展平图片列表，不修改真实路径。
· 展平模式下自动处理重名图片的显示名称（附带来源文件夹名）。
· 保留缓存加载、增量扫描与缩略图磁盘缓存机制。

0.1.0
-----
· 图形化图库：盘符/文件夹扫描、网格缩略图、子图库导航。
· galleries_cache.json.gz 全盘缓存与后台增量更新。
· 缩略图缓存、右键定位文件管理器、图片预览。
""".strip()
