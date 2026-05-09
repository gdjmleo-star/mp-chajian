"""
CustomMediaCat v7.9
自定义媒体分类插件

变更（v7.8 → v7.9）：
  - 修复 DEFAULT_4K 映射：「国产电影」→「华语电影」，与 category.yaml 对齐
  - 修复 DEFAULT_DOLBY_ELIGIBLE：同步替换「国产电影」→「华语电影」
  - 产品化：发布至 GitHub 插件仓库，支持 MP 插件市场一键安装
"""

import re
import shutil
import time
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple, Set

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.log import logger


DEFAULT_TV = [
    (r"\bNF\b|Netflix",     "奈飞专区"),
    (r"\bHBO\b",            "HBO专区"),
    (r"\bHMAX\b|\bMAX\b",   "Max"),
    (r"\bDSNP\b|Disney\+", "迪士尼专区"),
    (r"\bATVP\b|AppleTV",   "AppleTv"),
    (r"\bHULU\b",           "Hulu"),
    (r"\bAMZN\b|Amazon",    "欧美剧"),
    ("", ""), ("", ""), ("", ""),
]
DEFAULT_4K = [
    ("华语电影", "4K华语电影"),
    ("外语电影", "4K外语电影"),
    ("动画电影", "4K动画电影"),
    ("演唱会",   "4K演唱会"),
    ("", ""),
]
DEFAULT_DOLBY_KW       = r"\bDV\b|\bDoVi\b|\bDOVI\b|Dolby[\._]?Vision"
DEFAULT_4K_KW          = r"2160p|4K|UHD"
DEFAULT_DOLBY_ELIGIBLE = "华语电影,外语电影,动画电影,演唱会,4K华语电影,4K外语电影,4K动画电影,4K演唱会"
TV_SLOTS    = 10
MOVIE_SLOTS = 5


def _compile_kw(pattern: str, name: str) -> re.Pattern:
    try:
        return re.compile(pattern.strip(), re.IGNORECASE)
    except re.error as e:
        logger.warning(f"[CustomMediaCat] '{name}' 编译失败：{e}")
        return re.compile(r"(?!x)x")


class CustomMediaCat(_PluginBase):
    plugin_name    = "自定义媒体分类"
    plugin_desc    = "电影按4K/杜比视界二次归档，电视剧按QB源文件名关键词分类。"
    plugin_icon    = "category.png"
    plugin_version = "7.9"
    plugin_author  = "Isaac/叶语"
    plugin_config_prefix = "custommediacat_"
    plugin_order   = 5
    auth_level     = 1

    def init_plugin(self, config: dict = None):
        cfg = config or {}
        self._enabled = bool(cfg.get("enabled", False))

        self._tv_rules: List[Tuple[re.Pattern, str]] = []
        for i in range(TV_SLOTS):
            kw  = (cfg.get(f"tv_kw_{i}")  or "").strip()
            cat = (cfg.get(f"tv_cat_{i}") or "").strip()
            if kw and cat:
                try:
                    self._tv_rules.append((re.compile(kw, re.IGNORECASE), cat))
                except re.error as e:
                    logger.warning(f"[CustomMediaCat] TV规则[{i}] [{kw!r}] 编译失败：{e}")

        self._4k_map: Dict[str, str] = {}
        for i in range(MOVIE_SLOTS):
            src = (cfg.get(f"map_src_{i}") or "").strip()
            dst = (cfg.get(f"map_dst_{i}") or "").strip()
            if src and dst:
                self._4k_map[src] = dst

        self._dolby_kw_str = cfg.get("dolby_keywords", DEFAULT_DOLBY_KW).strip() or DEFAULT_DOLBY_KW
        self._4k_kw_str    = cfg.get("kw_4k",          DEFAULT_4K_KW).strip()    or DEFAULT_4K_KW
        dolby_elig_raw     = cfg.get("dolby_eligible",  DEFAULT_DOLBY_ELIGIBLE)
        self._dolby_elig: Set[str] = {s.strip() for s in dolby_elig_raw.split(",") if s.strip()}
        self._re_dolby = _compile_kw(self._dolby_kw_str, "dolby_keywords")
        self._re_4k    = _compile_kw(self._4k_kw_str,    "kw_4k")
        # [v7.6] 动态绑定事件
        eventmanager.add_event_listener(EventType.TransferComplete, self.on_transfer_complete)
        logger.warning("[CustomMediaCat] 事件监听器已手动绑定: EventType.TransferComplete")


        # 测试触发：结果存入 save_data，详情页读取展示
        test_fn   = (cfg.get("_test_filename")    or "").strip()
        test_cat  = (cfg.get("_test_current_cat") or "").strip()
        test_type = cfg.get("_test_media_type", "电影")
        if test_fn:
            result = self._run_test(test_fn, test_cat, is_movie=(test_type == "电影"))
            self.save_data("test_result", {
                "filename": test_fn,
                "category": test_cat,
                "type": test_type,
                "output": result,
            })

        logger.info(
            f"[CustomMediaCat] v7.9 就绪 enabled={self._enabled} | "
            f"TV规则 {len(self._tv_rules)} 条 | 4K映射 {len(self._4k_map)} 条"
        )

    def get_state(self) -> bool:
        return self._enabled

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        tv_rows = []
        for i in range(TV_SLOTS):
            tv_rows.append({
                "component": "VRow",
                "props": {"class": "ma-0 pa-0", "align": "center"},
                "content": [
                    {"component": "VCol", "props": {"cols": 1, "class": "pa-1 text-center text-caption text-medium-emphasis"}, "text": str(i + 1)},
                    {"component": "VCol", "props": {"cols": 6, "class": "pa-1"}, "content": [{"component": "VTextField", "props": {
                        "model": f"tv_kw_{i}", "label": "关键词",
                        "placeholder": r"例：\bNF\b|Netflix",
                        "variant": "outlined", "density": "compact", "hide-details": True,
                        "style": "font-family: monospace; font-size: 13px;"
                    }}]},
                    {"component": "VCol", "props": {"cols": 5, "class": "pa-1"}, "content": [{"component": "VTextField", "props": {
                        "model": f"tv_cat_{i}", "label": "目标分类目录",
                        "placeholder": "例：奈飞专区",
                        "variant": "outlined", "density": "compact", "hide-details": True,
                    }}]},
                ]
            })

        map_rows = []
        for i in range(MOVIE_SLOTS):
            map_rows.append({
                "component": "VRow",
                "props": {"class": "ma-0 pa-0", "align": "center"},
                "content": [
                    {"component": "VCol", "props": {"cols": 1, "class": "pa-1 text-center text-caption text-medium-emphasis"}, "text": str(i + 1)},
                    {"component": "VCol", "props": {"cols": 5, "class": "pa-1"}, "content": [{"component": "VTextField", "props": {
                        "model": f"map_src_{i}", "label": "MP 基础分类", "placeholder": "例：外语电影",
                        "variant": "outlined", "density": "compact", "hide-details": True,
                    }}]},
                    {"component": "VCol", "props": {"cols": 1, "class": "pa-1 text-center"}, "text": "→"},
                    {"component": "VCol", "props": {"cols": 5, "class": "pa-1"}, "content": [{"component": "VTextField", "props": {
                        "model": f"map_dst_{i}", "label": "4K 目标分类", "placeholder": "例：4K外语电影",
                        "variant": "outlined", "density": "compact", "hide-details": True,
                    }}]},
                ]
            })

        form = [{"component": "VForm", "content": [
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                    {"component": "VSwitch", "props": {
                        "model": "enabled", "label": "启用自动归档",
                        "hint": "关闭后插件不处理任何整理事件", "persistent-hint": True
                    }}
                ]}
            ]},
            {"component": "VDivider", "props": {"class": "my-2"}},
            {"component": "VTabs", "props": {"model": "_tab", "grow": True}, "content": [
                {"component": "VTab", "props": {"value": "tv"},    "text": "📺 电视剧规则"},
                {"component": "VTab", "props": {"value": "movie"}, "text": "🎬 电影规则"},
                {"component": "VTab", "props": {"value": "test"},  "text": "🔍 测试工具"},
            ]},
            {"component": "VWindow", "props": {"model": "_tab"}, "content": [

                # Tab1 电视剧
                {"component": "VWindowItem", "props": {"value": "tv"}, "content": [
                    {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "density": "compact", "class": "mt-2 mb-2"},
                     "text": "匹配 QB 下载文件名，从第1行到第10行依次检查，第一条命中即生效。留空的行自动跳过。"},
                    {"component": "VRow", "props": {"class": "ma-0 pa-0"}, "content": [
                        {"component": "VCol", "props": {"cols": 1, "class": "pa-1"}, "text": ""},
                        {"component": "VCol", "props": {"cols": 6, "class": "pa-1 text-caption font-weight-bold"}, "text": "关键词（支持正则，忽略大小写）"},
                        {"component": "VCol", "props": {"cols": 5, "class": "pa-1 text-caption font-weight-bold"}, "text": "目标分类目录名"},
                    ]},
                    *tv_rows
                ]},

                # Tab2 电影
                {"component": "VWindowItem", "props": {"value": "movie"}, "content": [
                    {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "density": "compact", "class": "mt-2 mb-3"},
                     "text": "处理顺序：① 杜比视界检测（命中→直接入「杜比视界电影」，流程终止）→ ② 4K升级（命中→移入对应4K目录）"},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {
                            "model": "dolby_keywords", "label": "🔵 杜比视界关键词（正则，| 分隔）",
                            "hint": r"例：\bDV\b|\bDoVi\b|Dolby[\._]?Vision", "persistent-hint": True,
                            "variant": "outlined", "density": "compact", "style": "font-family: monospace;"
                        }}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {
                            "model": "kw_4k", "label": "🟡 4K 关键词（正则，| 分隔）",
                            "hint": "例：2160p|4K|UHD", "persistent-hint": True,
                            "variant": "outlined", "density": "compact", "style": "font-family: monospace;"
                        }}]},
                    ]},
                    {"component": "VDivider", "props": {"class": "my-3"}},
                    {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "4K 细分映射（含4K关键词 → 升级到对应4K分类）"},
                    {"component": "VRow", "props": {"class": "ma-0 pa-0"}, "content": [
                        {"component": "VCol", "props": {"cols": 1, "class": "pa-1"}, "text": ""},
                        {"component": "VCol", "props": {"cols": 5, "class": "pa-1 text-caption font-weight-bold"}, "text": "MP 基础分类"},
                        {"component": "VCol", "props": {"cols": 1, "class": "pa-1"}, "text": ""},
                        {"component": "VCol", "props": {"cols": 5, "class": "pa-1 text-caption font-weight-bold"}, "text": "4K 目标分类"},
                    ]},
                    *map_rows,
                    {"component": "VDivider", "props": {"class": "my-3"}},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {
                            "model": "dolby_eligible", "label": "杜比视界覆盖的电影分类（逗号分隔）",
                            "hint": "只有属于这些分类的电影才会被移入「杜比视界电影」", "persistent-hint": True,
                            "variant": "outlined", "density": "compact",
                        }}]}
                    ]}
                ]},

                # Tab3 测试工具
                {"component": "VWindowItem", "props": {"value": "test"}, "content": [
                    {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "density": "compact", "class": "mt-2 mb-3"},
                     "text": "① 在下方填写文件名和分类 → ② 点「保存」→ ③ 关闭设置后点插件卡片的「详情」按钮 → 查看测试结果"},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 5}, "content": [{"component": "VTextField", "props": {
                            "model": "_test_filename", "label": "QB 原始文件名",
                            "placeholder": "例：The.Wire.S01E01.NF.2160p.DV.mkv",
                            "variant": "outlined", "density": "compact",
                        }}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {
                            "model": "_test_current_cat", "label": "MP 已分类为",
                            "placeholder": "例：外语电影（手填分类名）",
                            "hint": "填写 MP 会给该资源打的基础分类名",
                            "persistent-hint": True,
                            "variant": "outlined", "density": "compact",
                        }}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSelect", "props": {
                            "model": "_test_media_type", "label": "媒体类型",
                            "items": ["电影", "电视剧"],
                            "variant": "outlined", "density": "compact",
                        }}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 2, "class": "d-flex align-center"}, "content": [
                            {"component": "VAlert", "props": {"type": "warning", "variant": "tonal", "density": "compact"},
                             "text": "保存后看详情"}
                        ]},
                    ]},
                ]},
            ]},
        ]}]

        defaults: Dict[str, Any] = {
            "enabled": False, "_tab": "tv",
            "dolby_keywords": DEFAULT_DOLBY_KW,
            "kw_4k": DEFAULT_4K_KW,
            "dolby_eligible": DEFAULT_DOLBY_ELIGIBLE,
            "_test_filename": "", "_test_current_cat": "", "_test_media_type": "电影",
        }
        for i, (kw, cat) in enumerate(DEFAULT_TV):
            defaults[f"tv_kw_{i}"] = kw; defaults[f"tv_cat_{i}"] = cat
        for i in range(len(DEFAULT_TV), TV_SLOTS):
            defaults[f"tv_kw_{i}"] = ""; defaults[f"tv_cat_{i}"] = ""
        for i, (src, dst) in enumerate(DEFAULT_4K):
            defaults[f"map_src_{i}"] = src; defaults[f"map_dst_{i}"] = dst
        for i in range(len(DEFAULT_4K), MOVIE_SLOTS):
            defaults[f"map_src_{i}"] = ""; defaults[f"map_dst_{i}"] = ""
        return form, defaults

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return []

    def get_page(self) -> List[dict]:
        """详情页：展示上次测试结果 + 当前规则摘要。"""
        test_data = self.get_data("test_result") or {}
        output    = test_data.get("output", "")
        t_file    = test_data.get("filename", "")
        t_cat     = test_data.get("category", "")
        t_type    = test_data.get("type", "")

        # 规则摘要
        tv_summary  = "\n".join(f"  {p.pattern}  →  {c}" for p, c in self._tv_rules) if self._tv_rules else "（未配置）"
        map_summary = "\n".join(f"  {s}  →  {d}" for s, d in self._4k_map.items()) if self._4k_map else "（未配置）"

        result_block = []
        if output:
            result_block = [
                {"component": "VRow", "props": {"class": "mt-1"}, "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [
                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis mb-1"},
                         "text": f"测试输入：文件名「{t_file}」 / 分类「{t_cat}」 / 类型「{t_type}」"},
                        {"component": "VSheet", "props": {
                            "class": "pa-3 rounded",
                            "style": "font-family: monospace; font-size: 13px; white-space: pre-wrap; background: rgba(0,0,0,0.04);"
                        }, "text": output},
                    ]}
                ]}
            ]
        else:
            result_block = [
                {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "density": "compact", "class": "mt-2"},
                 "text": "暂无测试结果。请在「设置 → 测试工具」填写文件名后保存，然后刷新此页面。"}
            ]

        return [
            {"component": "VRow", "content": [
                # 左：测试结果
                {"component": "VCol", "props": {"cols": 12, "md": 7}, "content": [
                    {"component": "VCard", "props": {"variant": "outlined", "class": "pa-3"}, "content": [
                        {"component": "div", "props": {"class": "text-subtitle-1 font-weight-bold mb-2"}, "text": "🔍 最近一次测试结果"},
                        *result_block,
                    ]}
                ]},
                # 右：规则摘要
                {"component": "VCol", "props": {"cols": 12, "md": 5}, "content": [
                    {"component": "VCard", "props": {"variant": "outlined", "class": "pa-3 mb-3"}, "content": [
                        {"component": "div", "props": {"class": "text-subtitle-1 font-weight-bold mb-2"},
                         "text": f"📺 电视剧规则（{len(self._tv_rules)} 条）"},
                        {"component": "VSheet", "props": {
                            "class": "pa-2 rounded",
                            "style": "font-family: monospace; font-size: 12px; white-space: pre-wrap; background: rgba(0,0,0,0.04);"
                        }, "text": tv_summary},
                    ]},
                    {"component": "VCard", "props": {"variant": "outlined", "class": "pa-3"}, "content": [
                        {"component": "div", "props": {"class": "text-subtitle-1 font-weight-bold mb-2"},
                         "text": f"🎬 4K映射（{len(self._4k_map)} 条）"},
                        {"component": "VSheet", "props": {
                            "class": "pa-2 rounded",
                            "style": "font-family: monospace; font-size: 12px; white-space: pre-wrap; background: rgba(0,0,0,0.04);"
                        }, "text": map_summary},
                    ]},
                ]},
            ]}
        ]

    def _run_test(self, filename: str, current_cat: str, is_movie: bool) -> str:
        mtype = "电影" if is_movie else "电视剧"
        lines: List[str] = []

        def log(msg: str):
            lines.append(msg)
            logger.info(f"[CustomMediaCat TEST] {msg}")

        log("════ 模拟测试 ════")
        log(f"文件名：{filename}")
        log(f"当前分类：{current_cat}   媒体类型：{mtype}")
        log("──────────────────")

        effective = current_cat

        if is_movie:
            if self._re_dolby.search(filename):
                if effective in self._dolby_elig:
                    log("✅ 杜比视界关键词命中")
                    log("🎯 最终分类：「杜比视界电影」（优先级最高，流程终止）")
                    log("════ 结束 ════")
                    return "\n".join(lines)
                else:
                    log(f"⚠️  杜比视界关键词命中，但「{effective}」不在覆盖列表，跳过")
            else:
                log("—   杜比视界：未命中")

            if self._re_4k.search(filename):
                target = self._4k_map.get(effective)
                if target:
                    log(f"✅ 4K关键词命中 → 「{effective}」升级为「{target}」")
                    effective = target
                else:
                    log(f"—   4K关键词命中，但「{effective}」不在4K映射内，保持原分类")
            else:
                log("—   4K：未命中")
        else:
            hit = False
            for pattern, cat in self._tv_rules:
                if pattern.search(filename):
                    log(f"✅ 规则命中：[{pattern.pattern}] → 「{cat}」")
                    effective = cat
                    hit = True
                    break
            if not hit:
                log(f"—   所有规则未命中，保持原分类「{effective}」")

        log("──────────────────")
        log(f"🎯 最终分类：「{effective}」")
        log("════ 结束 ════")
        return "\n".join(lines)

    def _move(self, dest_path: Path, old_cat: str, new_cat: str) -> Optional[Path]:
        if old_cat == new_cat:
            return None
        try:
            parts = list(dest_path.parts)
            idx = next(i for i, p in enumerate(parts) if p == old_cat)
        except StopIteration:
            logger.warning(f"[CustomMediaCat] 路径中未找到「{old_cat}」：{dest_path}")
            return None
        try:
            # 移动整个剧集/电影目录（old_cat 下一级），而不是单个文件
            # 例：/data/media/日韩剧/猎犬 (2023) → /data/media/奈飞专区/猎犬 (2023)
            show_dir = Path(*parts[:idx + 2])  # 取到 old_cat/show_name 这一级
            new_parts = parts[:idx] + [new_cat] + parts[idx + 1:idx + 2]
            new_show_dir = Path(*new_parts)

            if new_show_dir.exists():
                # 目标目录已存在，逐文件合并（避免覆盖已有文件）
                for src_file in show_dir.rglob("*"):
                    if src_file.is_file():
                        rel = src_file.relative_to(show_dir)
                        dst_file = new_show_dir / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        if not dst_file.exists():
                            shutil.move(str(src_file), str(dst_file))
                # 清理空目录
                for d in sorted(show_dir.rglob("*"), reverse=True):
                    if d.is_dir():
                        try:
                            d.rmdir()
                        except OSError:
                            pass
                try:
                    show_dir.rmdir()
                except OSError:
                    pass
                logger.info(f"[CustomMediaCat] ✅ 合并移动：{show_dir} → {new_show_dir}")
            else:
                new_show_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(show_dir), str(new_show_dir))
                logger.info(f"[CustomMediaCat] ✅ 移动：{show_dir} → {new_show_dir}")
            return new_show_dir
        except Exception as e:
            logger.error(f"[CustomMediaCat] 移动失败：{e}")
        return None
    def on_transfer_complete(self, event: Event):
        logger.warning(f"[CustomMediaCat DEBUG] 收到事件: {event.event_type} | event_data keys: {list(event.event_data.keys() if event.event_data else [])}")
        logger.warning(f"[CustomMediaCat] ★ TransferComplete 事件触发！enabled={self._enabled}")
        if not self._enabled:
            logger.warning("[CustomMediaCat] ★ 插件未启用，跳过")
            return
        data         = event.event_data or {}
        mediainfo    = data.get("mediainfo")
        transferinfo = data.get("transferinfo")
        if not mediainfo or not transferinfo:
            return
        raw_type = getattr(mediainfo, "type", None)
        # MediaType 是枚举，str() 返回 "MediaType.MOVIE" 而非 "电影"
        mtype    = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
        is_movie = mtype in ("电影", "Movie")
        is_tv    = mtype in ("电视剧", "TV")
        logger.info(f"[CustomMediaCat] raw_type={raw_type!r} mtype={mtype!r} is_movie={is_movie} is_tv={is_tv}")
        if not is_movie and not is_tv:
            return
        category: str = getattr(mediainfo, "category", "") or ""
        tgt_item     = getattr(transferinfo, "target_item", None)
        tgt_path_str = getattr(tgt_item, "path", None) if tgt_item else None
        if not tgt_path_str:
            file_list_new = getattr(transferinfo, "file_list_new", []) or []
            tgt_path_str  = file_list_new[0] if file_list_new else None
        if not tgt_path_str:
            logger.warning("[CustomMediaCat] 无法获取目标路径，跳过")
            return
        dest_path = Path(tgt_path_str)
        src_item  = getattr(transferinfo, "fileitem", None)
        org_name: str = (
            (getattr(src_item, "name", "") or "")
            or (getattr(tgt_item, "name", "") or "")
            or str(dest_path.name)
        )
        logger.info(f"[CustomMediaCat] 处理：{org_name} | 分类：{category} | 类型：{'电影' if is_movie else '电视剧'}")
        # ── 等待 MP 把海报/NFO 写完，再移动目录（防止目录被移走时MP仍在写元数据）──
        logger.info(f"[CustomMediaCat] 等待 MP 元数据写入完成（8s）...")
        time.sleep(8)

        if is_movie:
            if self._re_dolby.search(org_name) and category in self._dolby_elig:
                logger.info(f"[CustomMediaCat] 杜比视界 → 「杜比视界电影」")
                self._move(dest_path, category, "杜比视界电影")
                return
            if self._re_4k.search(org_name):
                target_4k = self._4k_map.get(category)
                if target_4k:
                    logger.info(f"[CustomMediaCat] 4K → 「{category}」升级为「{target_4k}」")
                    self._move(dest_path, category, target_4k)
            return
        if is_tv:
            for pattern, cat in self._tv_rules:
                if pattern.search(org_name):
                    if cat != category:
                        logger.info(f"[CustomMediaCat] 电视剧命中 [{pattern.pattern}] → 移入「{cat}」")
                        self._move(dest_path, category, cat)
                    return
            logger.debug(f"[CustomMediaCat] 电视剧规则未命中，保持「{category}」")

    def stop_service(self):
        pass
