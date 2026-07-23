"""选择器文本的结构化判断。

判断一个 CSS selector 是否"过于宽泛"、是否指向表格容器等，纯字符串启发式，
不依赖流程结构，故独立于 lint 规则本身。
"""
from __future__ import annotations

import re

# Structural selector analysis helpers

# Known component-library class prefixes used in Chinese/international enterprise
# frontends. A class that starts with one of these is a "framework class" and
# does NOT provide a business-domain scope on its own.
_FRAMEWORK_CLASS_PREFIXES: tuple[str, ...] = (
    "el-",        # Element UI / Element Plus
    "ant-",       # Ant Design (React / Vue)
    "arco-",      # Arco Design (ByteDance)
    "vxe-",       # Vxe Table / Vxe UI
    "n-",         # Naive UI
    "van-",       # Vant (mobile)
    "ivu-",       # iView / View UI
    "layui-",     # LayUI
    "semi-",      # Semi Design (ByteDance)
    "tdesign-",   # TDesign (Tencent)
    "varlet-",    # Varlet (mobile)
    "vc-",        # Vue Component generic
    "v-",         # Generic Vue directive/component prefix
)

# HTML structural elements that cannot serve as a business-domain scope.
_HTML_STRUCTURAL_TAGS: frozenset[str] = frozenset(
    ["html", "body", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
     "div", "span", "ul", "ol", "li", "section", "article", "main", "aside",
     "header", "footer", "nav", "form", "fieldset"]
)

# Generic layout/structural vocabulary.  A CSS class whose every hyphen/underscore-
# separated word belongs to this set has no business-domain meaning and CANNOT act
# as a scoping ancestor for table selectors (e.g. app-main-container, layout-wrapper).
_LAYOUT_WORDS: frozenset[str] = frozenset([
    "app", "page", "main", "layout", "content", "wrapper", "container",
    "inner", "outer", "shell", "frame", "view", "root", "section",
    "area", "panel", "box", "wrap", "base", "center", "body",
    "fluid", "fixed", "scroll",
])


def _is_layout_only_class(cls: str) -> bool:
    """Return True when a CSS class name is composed entirely of generic layout words."""
    words = [w for w in re.split(r"[-_]", cls.lower()) if w]
    return bool(words) and all(w in _LAYOUT_WORDS for w in words)


def _extract_classes_from_token(token: str) -> list[str]:
    """Return all CSS class names from a compound selector token (e.g. 'tr.foo.bar' → ['foo','bar'])."""
    return re.findall(r'\.([a-zA-Z][a-zA-Z0-9_-]*)', token)


def _is_framework_class(cls: str) -> bool:
    return any(cls.lower().startswith(p) for p in _FRAMEWORK_CLASS_PREFIXES)


def _is_table_container_token(token: str) -> bool:
    """True if token targets a table-level container (not a row): contains 'table' but no -row/__row/-tr/__tr suffix."""
    t = token.strip().lower()
    if t == "table":
        return True
    if re.match(r'^\[role=[\'"]?(grid|table)[\'"]?\]$', t):
        return True
    for cls in _extract_classes_from_token(t):
        if "table" in cls and not re.search(r'[-_](row|tr)$|__row$|__tr$', cls):
            return True
    return False


def _is_table_container_selector(selector: str) -> bool:
    """True if the selector's rightmost (most-specific) token targets a table container."""
    s = re.sub(r":has-text\([^)]*\)", "", selector).strip().lower()
    s = re.sub(r"\s+", " ", s)
    last_token = s.split()[-1] if s.split() else s
    return _is_table_container_token(last_token)


def _is_broad_table_row_selector(selector: str) -> bool:
    """True when the selector's leftmost token is a bare tag, ARIA role, or all-framework-class — i.e. no business-domain parent to scope it to one table."""
    s = re.sub(r":has-text\([^)]*\)", "", selector)
    s = re.sub(r"\s*:scope\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    for prefix in ("html ", "body "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    if not s:
        return False

    leftmost = s.split()[0]

    # ID selector → unique business scope → not broad
    if "#" in leftmost:
        return False

    classes = _extract_classes_from_token(leftmost)
    if not classes:
        # bare tag or [attr] — no business scope
        return True

    # broad if every class is framework-prefixed or layout-only (no business class present)
    return all(_is_framework_class(cls) or _is_layout_only_class(cls) for cls in classes)


def _detect_unsupported_css_selector_syntax(selector: str) -> str | None:
    """识别不能放入 CSS selector 字段的 Playwright 定位语法。

    插件执行、DOM 探测和部分校验链路会使用 querySelectorAll。这里不做隐式转换，
    只提前阻断 AI 常生成且会在运行时爆炸的 selector 语法。
    """
    stripped = selector.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    unsupported_prefixes = (
        "text=",
        "role=",
        "xpath=",
        "id=",
        "data-testid=",
        "data-test-id=",
    )
    for prefix in unsupported_prefixes:
        if lower.startswith(prefix):
            return prefix.rstrip("=")
    if re.search(r'(^|,\s*)(?:text|role|xpath|id|data-testid|data-test-id)\s*=', lower):
        return "mixed-playwright-selector"
    return None
