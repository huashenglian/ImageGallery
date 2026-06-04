from __future__ import annotations

import logging

from services.models_registry import (
    CATEGORY_MAP_IMAGENET,
    CATEGORY_MAP_DANBOORU,
    CATEGORY_MAP_WD14,
    CATEGORY_MAP_GENERIC,
)

CATEGORY_MAPPING = {
    "人物": ["person", "people", "face", "man", "woman", "child", "boy", "girl",
             "portrait", "selfie", "human", "crowd", "audience"],
    "风景": ["landscape", "mountain", "beach", "sunset", "sunrise", "sky", "cloud",
             "ocean", "lake", "river", "forest", "tree", "flower", "garden", "snow",
             "waterfall", "canyon", "valley", "island", "coast"],
    "动物": ["cat", "dog", "bird", "animal", "pet", "wildlife", "fish", "horse",
             "cow", "sheep", "elephant", "lion", "tiger", "bear", "rabbit", "squirrel",
             "butterfly", "insect", "snake", "frog", "turtle", "whale", "dolphin"],
    "食物": ["food", "meal", "dish", "fruit", "vegetable", "drink", "dessert", "cake",
             "bread", "pizza", "burger", "salad", "rice", "pasta", "soup", "coffee",
             "wine", "beer", "juice", "ice cream", "chocolate"],
    "建筑": ["building", "architecture", "house", "church", "castle", "bridge", "tower",
             "skyscraper", "stadium", "museum", "library", "temple", "palace", "lighthouse",
             "city", "street"],
    "车辆": ["car", "vehicle", "bike", "bicycle", "motorcycle", "bus", "train", "truck",
             "airplane", "boat", "ship", "helicopter", "taxi", "ambulance", "fire engine"],
    "截图/文字": ["text", "screenshot", "document", "menu", "web page", "book", "sign",
                  "keyboard", "computer", "screen", "monitor"],
    "其他": []
}

DANBOORU_CATEGORY_MAPPING = {
    "人物": ["1girl", "2girls", "1boy", "2boys", "solo", "multiple_girls",
             "girl", "boy", "female", "male", "woman", "man", "loli", "shota"],
    "风景": ["scenery", "landscape", "sky", "cloud", "sunset", "night", "outdoors",
             "nature", "water", "sea", "ocean", "forest", "mountain"],
    "动物": ["cat", "dog", "animal", "bird", "rabbit", "horse", "fish",
             "cat_ears", "tail", "animal_ears"],
    "食物": ["food", "drink", "fruit", "dessert", "cake", "tea", "coffee",
             "bento", "rice", "noodle"],
    "建筑": ["building", "architecture", "house", "city", "street", "indoors",
             "room", "classroom", "school", "temple", "castle"],
    "车辆": ["car", "vehicle", "train", "bus", "bicycle", "motorcycle",
             "airplane", "boat", "ship"],
    "截图/文字": ["text", "signature", "watermark", "logo", "english_text",
                  "japanese_text", "chinese_text"],
    "其他": []
}

CONFIDENCE_THRESHOLD = 0.2

_preset_mapping_cache: dict[str, dict[str, list[str]]] = {}
_STRICT_SUBJECT_KEYWORDS = {
    "女性": {"1girl", "girl", "female", "woman"},
    "男性": {"1boy", "boy", "male", "man"},
    "多人": {"2girls", "2boys", "3girls", "4girls", "5girls", "6+girls",
           "3boys", "4boys", "5boys", "6+boys", "multiple_girls",
           "multiple_boys", "group", "couple"},
}


def _get_default_mapping(map_type: str) -> dict[str, list[str]]:
    if map_type == CATEGORY_MAP_DANBOORU:
        return DANBOORU_CATEGORY_MAPPING
    if map_type == CATEGORY_MAP_GENERIC:
        return {}
    return CATEGORY_MAPPING


def _get_mapping(map_type: str) -> dict[str, list[str]]:
    if map_type in (CATEGORY_MAP_IMAGENET, CATEGORY_MAP_DANBOORU, CATEGORY_MAP_GENERIC):
        return _get_default_mapping(map_type)

    # WD14 is a preset type – always resolve via preset loading
    if map_type == CATEGORY_MAP_WD14:
        if map_type in _preset_mapping_cache:
            return _preset_mapping_cache[map_type]
        from services.category_presets import load_preset, preset_to_mapping
        preset = load_preset(map_type)
        if preset:
            mapping = preset_to_mapping(preset)
            _preset_mapping_cache[map_type] = mapping
            return mapping
        return {}

    if map_type in _preset_mapping_cache:
        return _preset_mapping_cache[map_type]

    from services.category_presets import load_preset, preset_to_mapping
    preset = load_preset(map_type)
    if preset:
        mapping = preset_to_mapping(preset)
        _preset_mapping_cache[map_type] = mapping
        return mapping

    return CATEGORY_MAPPING


def clear_mapping_cache() -> None:
    _preset_mapping_cache.clear()


_unified_cache: dict[tuple[str, ...], dict[str, list[str]]] = {}


def clear_unified_cache() -> None:
    _unified_cache.clear()


class _ACAutomaton:

    def __init__(self) -> None:
        self._children: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._output: list[list[str]] = [[]]
        self._built = False

    def add(self, keyword: str) -> None:
        if not keyword:
            return
        node = 0
        for ch in keyword:
            if ch not in self._children[node]:
                self._children[node][ch] = len(self._children)
                self._children.append({})
                self._fail.append(0)
                self._output.append([])
            node = self._children[node][ch]
        self._output[node].append(keyword)
        self._built = False

    def build(self) -> None:
        from collections import deque
        q: deque[int] = deque()
        for ch, child in self._children[0].items():
            self._fail[child] = 0
            q.append(child)
        while q:
            r = q.popleft()
            for ch, u in self._children[r].items():
                q.append(u)
                v = self._fail[r]
                while v and ch not in self._children[v]:
                    v = self._fail[v]
                self._fail[u] = self._children[v].get(ch, 0) if v else 0
                self._output[u] = self._output[u] + self._output[self._fail[u]]
        self._built = True

    def search(self, text: str) -> list[str]:
        if not self._built:
            self.build()
        node = 0
        matches: list[str] = []
        for ch in text:
            while node and ch not in self._children[node]:
                node = self._fail[node]
            node = self._children[node].get(ch, 0)
            if self._output[node]:
                matches.extend(self._output[node])
        return matches


class UnifiedCategoryBuilder:

    def __init__(self, enabled_models: list[dict], model_order: list[str]) -> None:
        self._enabled_models = enabled_models
        self._model_order = model_order
        self._mapping: dict[str, list[str]] | None = None
        self._keyword_index: dict[str, str] = {}
        self._ac_automaton: _ACAutomaton | None = None
        self._logger = logging.getLogger("classifier")

    def build(self) -> dict[str, list[str]]:
        cache_key = tuple(self._model_order)
        if cache_key in _unified_cache:
            self._mapping = _unified_cache[cache_key]
            self._build_keyword_index()
            self._build_ac_automaton()
            return dict(self._mapping)

        model_map: dict[str, dict[str, list[str]]] = {}
        for m in self._enabled_models:
            model_id = m.get("model_id", "")
            config = m.get("config")
            if config is None:
                continue
            map_type = getattr(config, "category_map_type", None)
            if not map_type:
                continue
            mapping = _get_mapping(map_type)
            if mapping:
                model_map[model_id] = mapping

        merged: dict[str, list[str]] = {}
        kw_to_cat: dict[str, str] = {}
        kw_to_model: dict[str, str] = {}
        cat_sources: dict[str, list[str]] = {}

        for model_id in self._model_order:
            if model_id not in model_map:
                continue
            mapping = model_map[model_id]
            for category, keywords in mapping.items():
                if category == "其他":
                    continue
                if category in merged:
                    added: list[str] = []
                    for kw in keywords:
                        kw_norm = kw.lower().replace("_", " ")
                        if kw_norm in kw_to_cat and kw_to_cat[kw_norm] != category:
                            existing_cat = kw_to_cat[kw_norm]
                            existing_model = kw_to_model.get(kw_norm, "")
                            self._logger.info(
                                "标签冲突: keyword='%s', 模型A(%s)映射到'%s', 模型B(%s)映射到'%s', 采用'%s'(优先级更高)",
                                kw, existing_model, existing_cat, model_id, category, existing_cat,
                            )
                            continue
                        if kw_norm not in kw_to_cat:
                            kw_to_cat[kw_norm] = category
                            kw_to_model[kw_norm] = model_id
                            added.append(kw)
                    existing_set = set(merged[category])
                    new_kws = [kw for kw in added if kw not in existing_set]
                    merged[category] = merged[category] + new_kws
                    cat_sources.setdefault(category, []).append(model_id)
                else:
                    unique_kws: list[str] = []
                    for kw in keywords:
                        kw_norm = kw.lower().replace("_", " ")
                        if kw_norm in kw_to_cat:
                            existing_cat = kw_to_cat[kw_norm]
                            existing_model = kw_to_model.get(kw_norm, "")
                            self._logger.info(
                                "标签冲突: keyword='%s', 模型A(%s)映射到'%s', 模型B(%s)映射到'%s', 采用'%s'(优先级更高)",
                                kw, existing_model, existing_cat, model_id, category, existing_cat,
                            )
                        else:
                            kw_to_cat[kw_norm] = category
                            kw_to_model[kw_norm] = model_id
                            unique_kws.append(kw)
                    merged[category] = unique_kws
                    cat_sources[category] = [model_id]

        for cat, sources in cat_sources.items():
            if len(sources) > 1:
                self._logger.info("类别合并: '%s' 合并提示词, 来源模型: %s", cat, sources)
            else:
                self._logger.info("独有类别: '%s' 来自模型 %s", cat, sources[0])

        merged["其他"] = []
        self._mapping = merged
        self._keyword_index = kw_to_cat
        self._build_ac_automaton()
        _unified_cache[cache_key] = dict(merged)
        return dict(merged)

    def get_unified_mapping(self) -> dict[str, list[str]]:
        if self._mapping is None:
            self.build()
        return dict(self._mapping)

    def classify_with_unified_mapping(self, label: str, threshold: float = 0.2) -> str:
        if self._mapping is None:
            self.build()
        label_norm = label.lower().replace("_", " ")
        if label_norm in self._keyword_index:
            return self._keyword_index[label_norm]
        if self._ac_automaton:
            matches = self._ac_automaton.search(label_norm)
            for kw in matches:
                kw_norm = kw.lower().replace("_", " ")
                if kw_norm in self._keyword_index:
                    return self._keyword_index[kw_norm]
        return "其他"

    def _build_keyword_index(self) -> None:
        self._keyword_index = {}
        if self._mapping is None:
            return
        for category, keywords in self._mapping.items():
            if category == "其他" or not keywords:
                continue
            for kw in keywords:
                kw_norm = kw.lower().replace("_", " ")
                if kw_norm not in self._keyword_index:
                    self._keyword_index[kw_norm] = category

    def _build_ac_automaton(self) -> None:
        self._ac_automaton = _ACAutomaton()
        if self._mapping is None:
            return
        for category, keywords in self._mapping.items():
            if category == "其他" or not keywords:
                continue
            for kw in keywords:
                kw_norm = kw.lower().replace("_", " ")
                self._ac_automaton.add(kw_norm)
        self._ac_automaton.build()


def map_label_to_category(label: str, map_type: str = CATEGORY_MAP_IMAGENET) -> str:
    mapping = _get_mapping(map_type)
    if not mapping:
        return label
    label_norm = label.lower().replace("_", " ")

    if not hasattr(map_label_to_category, "_ac") or map_label_to_category._ac_type != map_type:
        ac = _ACAutomaton()
        kw_cat: dict[str, str] = {}
        for category, keywords in mapping.items():
            if category == "其他" or not keywords:
                continue
            for kw in keywords:
                kw_norm = kw.lower().replace("_", " ")
                kw_cat[kw_norm] = category
                ac.add(kw_norm)
        ac.build()
        map_label_to_category._ac = ac
        map_label_to_category._ac_kw_cat = kw_cat
        map_label_to_category._ac_type = map_type

    if label_norm in map_label_to_category._ac_kw_cat:
        return map_label_to_category._ac_kw_cat[label_norm]

    matches = map_label_to_category._ac.search(label_norm)
    for kw in matches:
        kw_norm = kw.lower().replace("_", " ")
        cat = map_label_to_category._ac_kw_cat.get(kw_norm, "")
        if cat:
            return cat
    return "其他"


def classify_top5(top5_labels: list[str], top5_confidences: list[float],
                  map_type: str = CATEGORY_MAP_IMAGENET) -> tuple[str, float]:
    for label, confidence in zip(top5_labels, top5_confidences):
        category = map_label_to_category(label, map_type)
        if category != "其他" and confidence >= CONFIDENCE_THRESHOLD:
            return (category, confidence)
    return ("其他", top5_confidences[0] if top5_confidences else 0.0)


def classify_multi_label_set(
    top_results: list[tuple[str, float]],
    map_type: str,
    threshold: float,
) -> tuple[str, float, str]:
    import logging
    _logger = logging.getLogger("classifier")

    mapping = _get_mapping(map_type)
    if not mapping:
        if top_results:
            label, conf = top_results[0]
            return label, conf, label
        return "其他", 0.0, ""

    label_set: set[str] = set()
    label_conf_map: dict[str, float] = {}
    all_label_confs: list[tuple[str, float]] = []

    for label, conf in top_results:
        norm = label.lower().replace("_", " ")
        all_label_confs.append((norm, conf))
        if conf >= threshold:
            label_set.add(norm)
            if norm not in label_conf_map or conf > label_conf_map[norm]:
                label_conf_map[norm] = conf

    _logger.info("=" * 40)
    _logger.info("set-based 多标签分类 (阈值=%.2f, 高于阈值标签数=%d/%d)",
                 threshold, len(label_set), len(top_results))
    _logger.info("高于阈值标签 (前30): %s",
                 ", ".join(f"{l}={c:.3f}" for l, c in all_label_confs[:30] if c >= threshold))
    _logger.info("Using main threshold %.2f, extracted tags count: %d", threshold, len(label_set))
    if not label_set:
        _logger.info("First 10 extracted tags: []")
    else:
        _logger.info("First 10 extracted tags: %s", list(label_set)[:10])
    if map_type == CATEGORY_MAP_WD14:
        onegirl_conf = label_conf_map.get("1girl")
        if onegirl_conf is None:
            _logger.info("1girl 主体标签: 未进入阈值集合")
        else:
            _logger.info("1girl 主体标签: 已过阈值 conf=%.3f", onegirl_conf)

    for category, keywords in mapping.items():
        if category == "其他" or not keywords:
            continue
        kw_norm_set = {kw.lower().replace("_", " ") for kw in keywords}
        hits = label_set & kw_norm_set
        strict_hits = hits
        if map_type == CATEGORY_MAP_WD14 and category in _STRICT_SUBJECT_KEYWORDS:
            strict_hits = hits & {kw.lower().replace("_", " ") for kw in _STRICT_SUBJECT_KEYWORDS[category]}
            _logger.info(
                "分类关键词交集: category=%s hits=%s strict_subject_hits=%s",
                category, sorted(hits)[:10], sorted(strict_hits)[:10],
            )
        else:
            _logger.info("分类关键词交集: category=%s hits=%s", category, sorted(hits)[:10])
        if strict_hits:
            best_hit = max(strict_hits, key=lambda h: label_conf_map.get(h, 0.0))
            best_conf = label_conf_map.get(best_hit, 0.0)
            _logger.info("命中分类: category=%s hits=%s best=%s conf=%.3f",
                         category, list(strict_hits)[:10], best_hit, best_conf)

            best_label = best_hit
            for label, conf in top_results:
                if label.lower().replace("_", " ") == best_hit:
                    best_label = label
                    best_conf = conf
                    break
            return category, best_conf, best_label

    if not hasattr(classify_multi_label_set, "_ac") or classify_multi_label_set._ac_type != map_type:
        ac = _ACAutomaton()
        kw_cat: dict[str, str] = {}
        for category, keywords in mapping.items():
            if category == "其他" or not keywords:
                continue
            if map_type == CATEGORY_MAP_WD14 and category in _STRICT_SUBJECT_KEYWORDS:
                continue
            for kw in keywords:
                kw_norm = kw.lower().replace("_", " ")
                kw_cat[kw_norm] = category
                ac.add(kw_norm)
        ac.build()
        classify_multi_label_set._ac = ac
        classify_multi_label_set._ac_kw_cat = kw_cat
        classify_multi_label_set._ac_type = map_type

    _ac = classify_multi_label_set._ac
    _kw_cat = classify_multi_label_set._ac_kw_cat

    for norm_label, conf in sorted(label_conf_map.items(), key=lambda x: -x[1]):
        if _ac:
            matches = _ac.search(norm_label)
            for kw in matches:
                kw_norm = kw.lower().replace("_", " ")
                cat = _kw_cat.get(kw_norm, "")
                if cat:
                    _logger.info("子串匹配命中(AC): category=%s keyword=%s label=%s conf=%.3f",
                                 cat, kw_norm, norm_label, conf)
                    best_label = norm_label
                    for label, c in top_results:
                        if label.lower().replace("_", " ") == norm_label:
                            best_label = label
                            break
                    return cat, conf, best_label
        for kw_norm, cat in _kw_cat.items():
            if norm_label in kw_norm:
                _logger.info("反向子串匹配命中: category=%s keyword=%s label=%s conf=%.3f",
                             cat, kw_norm, norm_label, conf)
                best_label = norm_label
                for label, c in top_results:
                    if label.lower().replace("_", " ") == norm_label:
                        best_label = label
                        break
                return cat, conf, best_label

    _logger.info("未命中任何分类，返回「其他」")
    _logger.info("全部标签(前30): %s",
                 ", ".join(f"{l}={c:.3f}" for l, c in all_label_confs[:30]))
    if top_results:
        label, conf = top_results[0]
        return "其他", conf, label
    return "其他", 0.0, ""


def get_all_categories(map_type: str = CATEGORY_MAP_IMAGENET) -> list[str]:
    mapping = _get_mapping(map_type)
    if mapping:
        return list(mapping.keys())
    return list(CATEGORY_MAPPING.keys())
