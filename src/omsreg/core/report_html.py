"""Общий каркас самодостаточного HTML-отчёта: тема оформления и скелет страницы.

  * BASE_CSS — тема, общая для отчётов: цветовые переменные светлой/тёмной схемы,
    базовая типографика, карточки, таблицы, полосы величины. Отличия конкретного
    отчёта (ширина колонки, отступы, свои классы вроде .controls/.note) он добавляет
    через extra_css у page().
  * page() — скелет документа (DOCTYPE/head/style/body/wrap/закрытие).
  * tile() — карточка-показатель (крупное значение над подписью).
  * bar() — полоса величины с подписью (разметка под .barwrap/.bar из BASE_CSS).

Модуль доменно-нейтрален и не импортирует tkinter, поэтому живёт в core.
"""

from __future__ import annotations

# Тема, общая для всех отчётов (значения совпадают во всех потребителях).
# Отличающиеся правила отчёт добавляет через extra_css у page().
BASE_CSS = (
    ":root { color-scheme: light; --surface:#fcfcfb; --card:#f4f4f2; --ink:#0b0b0b; --ink2:#52514e;\n"
    "  --border:#e3e2de; --accent:#2a78d6; --bar:#2a78d680; --hover:#eef3fa; }\n"
    "@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --surface:#1a1a19; --card:#242423;\n"
    "  --ink2:#c3c2b7; --border:#3a3a38; --accent:#3987e5; --bar:#3987e580; --hover:#24303f; } }\n"
    "* { box-sizing:border-box; }\n"
    "body { margin:0; padding:24px; background:var(--surface); color:var(--ink); }\n"
    ".wrap { margin:0 auto; }\n"
    "h1 { font-size:22px; margin:0 0 4px; }\n"
    ".meta { color:var(--ink2); font-size:13.5px; }\n"
    ".tiles { display:flex; flex-wrap:wrap; gap:12px; }\n"
    ".tile { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 18px; min-width:150px; }\n"
    ".tile .v { font-size:22px; font-weight:700; white-space:nowrap; } .tile .l { font-size:12.5px; color:var(--ink2); margin-top:2px; }\n"
    "table { border-collapse:collapse; width:100%; font-size:14px; font-variant-numeric:tabular-nums; }\n"
    "th { text-align:left; color:var(--ink2); font-weight:600; font-size:12.5px; "
    "border-bottom:2px solid var(--border); padding:6px 10px; white-space:nowrap; }\n"
    "td { border-bottom:1px solid var(--border); padding:5px 10px; }\n"
    "td.num,th.num { text-align:right; white-space:nowrap; }\n"
    "tr.total td { font-weight:700; border-top:2px solid var(--border); }\n"
    ".barwrap { position:relative; } .barwrap .bar { position:absolute; left:0; top:50%;\n"
    "  transform:translateY(-50%); height:10px; background:var(--bar); border-radius:0 4px 4px 0; }"
    " .barwrap span { position:relative; padding-left:4px; }\n"
    "@media print { body { padding:0; } }\n"
)


def page(title: str, body: str, *, extra_css: str = "", script: str = "", lang: str = "ru") -> str:
    """Собирает самодостаточную HTML-страницу вокруг готового тела body.

    title      — уже экранированный заголовок (идёт в <title>);
    body        — HTML тела (между <div class="wrap"> и его закрытием);
    extra_css   — специфичные для отчёта правила CSS (добавляются к BASE_CSS);
    script      — необязательный <script>…</script>, вставляется в конце тела.
    """
    return (
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title><style>{BASE_CSS}{extra_css}</style></head>'
        f'<body><div class="wrap">\n{body}\n{script}</div></body></html>'
    )


def tile(value, label, cls: str = "") -> str:
    """Карточка-показатель: крупное значение над подписью.

    cls — доп. класс значения ('bad'/'good'/'warn' для цветовой пометки).
    """
    v_cls = f"v {cls}" if cls else "v"
    return f'<div class="tile"><div class="{v_cls}">{value}</div><div class="l">{label}</div></div>'


def bar(share, text, *, digits: int = 1) -> str:
    """Полоса величины с подписью: ширина — доля share (0..1) от строки-лидера.

    Оформление полосы задаёт BASE_CSS (.barwrap/.bar), поэтому и разметка живёт
    здесь: отчёты статистики и экономики писали её каждый свою. digits — знаков
    после запятой в ширине полосы (в статистике 1, в экономике 0).
    """
    w = max(0.0, min(1.0, share)) * 100
    return (f'<div class="barwrap"><div class="bar" style="width:{w:.{digits}f}%"></div>'
            f'<span>{text}</span></div>')
