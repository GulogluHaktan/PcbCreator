"""Build paper/yontem.pdf from paper/yontem.tex without a LaTeX installation.

The .tex file is the single source of the text. This script translates the small LaTeX subset it uses (sections,
lists, tables, figures, a few inline commands) to HTML and prints it to PDF with headless Chrome.
Run: python3 scripts/make_report.py   (needs google-chrome-stable)
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / "paper" / "yontem.tex"
HTML = ROOT / "paper" / "yontem.html"
PDF = ROOT / "paper" / "yontem.pdf"

CSS = """
@page { size: A4; margin: 2.0cm 1.9cm 2.0cm 1.9cm; }
body { font-family: 'Liberation Serif','DejaVu Serif',serif; font-size: 11pt; line-height: 1.38; color:#111; }
h1 { font-size: 20pt; text-align:center; margin: 0 0 2pt 0; }
.sub { text-align:center; font-size: 13pt; margin: 0 0 6pt 0; }
.meta { text-align:center; font-size: 10pt; color:#444; margin-bottom: 12pt; }
h2 { font-size: 14pt; margin: 16pt 0 4pt 0; border-bottom: 1px solid #999; padding-bottom: 2pt; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 11pt 0 3pt 0; page-break-after: avoid; }
p { margin: 4pt 0; text-align: justify; }
.abstract { margin: 6pt 18pt 10pt 18pt; font-size: 10.3pt; border-left: 3px solid #999; padding-left: 10pt; }
code { font-family: 'DejaVu Sans Mono',monospace; font-size: 8.7pt; background:#f1f1f1; padding: 0 2px; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt 0; font-size: 9.2pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background:#e8e8e8; text-align:left; }
th, td { border: 1px solid #aaa; padding: 3pt 5pt; vertical-align: top; }
figure { margin: 8pt 0; text-align:center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 10.5cm; object-fit: contain; }
figcaption { font-size: 9.5pt; color:#222; margin-top: 3pt; text-align:center; }
ul, ol { margin: 3pt 0 6pt 0; padding-left: 20pt; }
li { margin: 1.5pt 0; text-align: justify; }
.refs { font-size: 9.6pt; }
a { color:#1a3d7c; text-decoration:none; }
"""

MATH = {r"\rightarrow": "→", r"\approx": "≈", r"\Omega": "Ω", r"\mu": "µ", r"\emph{k}": "k"}


def read_arg(s: str, i: int) -> tuple[str, int]:
    """s[i] must be '{'; returns (content, index after the matching '}')."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced braces")


def inline(s: str, labels: dict[str, str]) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\":
            m = re.match(r"\\(textbf|emph|code|texttt|url|ref|label|textit)\b", s[i:])
            if m and i + m.end() < len(s) and s[i + m.end()] == "{":
                cmd = m.group(1)
                arg, j = read_arg(s, i + m.end())
                if cmd == "textbf":
                    out.append(f"<b>{inline(arg, labels)}</b>")
                elif cmd in ("emph", "textit"):
                    out.append(f"<i>{inline(arg, labels)}</i>")
                elif cmd in ("code", "texttt"):
                    out.append("<code>" + html.escape(arg.replace("\\_", "_").replace("\\", "")) + "</code>")
                elif cmd == "url":
                    out.append(f'<a href="{html.escape(arg)}">{html.escape(arg)}</a>')
                elif cmd == "ref":
                    out.append(labels.get(arg, "?"))
                i = j
                continue
            m = re.match(r'\\"([oOuUaA])', s[i:])
            if m:
                out.append({"o": "ö", "O": "Ö", "u": "ü", "U": "Ü", "a": "ä", "A": "Ä"}[m.group(1)])
                i += 3
                continue
            m = re.match(r"\\(%|_|&|\$|#)", s[i:])
            if m:
                out.append(m.group(1))
                i += 2
                continue
            m = re.match(r"\\[a-zA-Z]+", s[i:])
            if m and m.group(0) in MATH:
                out.append(MATH[m.group(0)])
                i += m.end()
                continue
            i += 1  # unknown command: drop the backslash
            continue
        c = s[i]
        out.append({"<": "&lt;", ">": "&gt;", "&": "&amp;"}.get(c, c))
        i += 1
    t = "".join(out)
    t = t.replace("$", "").replace("---", "—").replace("--", "–").replace("~", "&nbsp;")
    t = t.replace("``", "“").replace("''", "”")
    return t


def number_labels(body: str) -> dict[str, str]:
    labels, sec, fig = {}, 0, 0
    for m in re.finditer(r"\\section\{[^}]*\}\\label\{([^}]+)\}|\\section\{|\\begin\{figure\}(?:(?!\\end\{figure\}).)*?\\label\{([^}]+)\}", body, re.S):
        if m.group(0).startswith("\\section"):
            sec += 1
            if m.group(1):
                labels[m.group(1)] = str(sec)
        else:
            fig += 1
            labels[m.group(2)] = str(fig)
    for m in re.finditer(r"\\caption\{.*?\}\\label\{([^}]+)\}", body, re.S):
        labels.setdefault(m.group(1), labels.get(m.group(1), "?"))
    return labels


def table(spec_body: str, labels) -> str:
    rows = [r.strip() for r in re.split(r"\\\\", spec_body) if r.strip()]
    html_rows, header_done, head = [], False, []
    for r in rows:
        r = re.sub(r"\\(toprule|midrule|endhead|bottomrule)", "", r).strip()
        if not r:
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)&", r)]
        tag = "th" if not header_done else "td"
        row = "<tr>" + "".join(f"<{tag}>{inline(c, labels)}</{tag}>" for c in cells) + "</tr>"
        if not header_done:
            head.append(row)
            header_done = True
        else:
            html_rows.append(row)
    return "<table><thead>" + "".join(head) + "</thead><tbody>" + "".join(html_rows) + "</tbody></table>"


def convert(tex: str) -> str:
    title = re.search(r"\\title\{(.*?)\}\s*\\author", tex, re.S).group(1)
    author = re.search(r"\\author\{(.*?)\}\s*\\date", tex, re.S).group(1)
    date = re.search(r"\\date\{(.*?)\}", tex).group(1)
    body = tex.split("\\begin{document}")[1].split("\\end{document}")[0].replace("\\maketitle", "")
    labels = number_labels(body)
    main_title, _, subtitle = title.partition("\\\\")
    head = (f"<h1>{inline(main_title.replace(chr(10), ' '), labels)}</h1>"
            f"<div class='sub'>{inline(re.sub(r'^\[[^\]]*\]', '', subtitle.replace('\\large', '').strip()).strip(), labels)}</div>"
            f"<div class='meta'>{inline(author.replace('\\small', '').replace(chr(92) * 2, ' · ').strip(), labels)}<br>{inline(date, labels)}</div>")
    parts, pos = [head], 0
    token = re.compile(r"\\begin\{(abstract|itemize|enumerate|figure|longtable)\}(\{[^}]*\})?|\\section\*?\{|\\subsection\{",
                       re.S)
    sec = sub = 0
    while True:
        m = token.search(body, pos)
        text_end = m.start() if m else len(body)
        chunk = body[pos:text_end].strip()
        if chunk:
            for para in re.split(r"\n\s*\n", chunk):
                para = para.strip()
                if para and not para.startswith("\\label") and not para.startswith("\\begin{small}") \
                        and not para.startswith("\\end{small}"):
                    parts.append(f"<p>{inline(para.replace(chr(10), ' '), labels)}</p>")
        if not m:
            break
        kind = m.group(1)
        if m.group(0).startswith("\\section") or m.group(0).startswith("\\subsection"):
            star = m.group(0).startswith("\\section*")
            arg, end = read_arg(body, m.end() - 1)
            lab = re.match(r"\\label\{[^}]*\}", body[end:])
            end += lab.end() if lab else 0
            if m.group(0).startswith("\\subsection"):
                sub += 1
                parts.append(f"<h3>{sec}.{sub} {inline(arg, labels)}</h3>")
            else:
                if star:
                    parts.append(f"<h2>{inline(arg, labels)}</h2><div class='refs'>")
                else:
                    sec, sub = sec + 1, 0
                    parts.append(f"<h2>{sec}. {inline(arg, labels)}</h2>")
            pos = end
            continue
        env_end = body.index(f"\\end{{{kind}}}", m.end())
        inner = body[m.end():env_end]
        if kind == "abstract":
            parts.append(f"<div class='abstract'><b>Özet.</b> {inline(inner.strip().replace(chr(10), ' '), labels)}</div>")
        elif kind in ("itemize", "enumerate"):
            tag = "ul" if kind == "itemize" else "ol"
            items = [i.strip() for i in re.split(r"\\item(?:\[[^\]]*\])?", inner)[1:]]
            parts.append(f"<{tag}>" + "".join(f"<li>{inline(i.replace(chr(10), ' '), labels)}</li>" for i in items)
                         + f"</{tag}>")
        elif kind == "figure":
            img = re.search(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}", inner)
            cap, _ = read_arg(inner, inner.index("{", inner.index("\\caption")))
            width = re.search(r"width=([\d.]+)\\textwidth", img.group(1) or "")
            pct = int(float(width.group(1)) * 100) if width else 90
            n = next((v for k, v in labels.items() if k.startswith("fig:") and f"\\label{{{k}}}" in inner), "?")
            parts.append(f"<figure><img src='{img.group(2)}' style='width:{pct}%'><figcaption><b>Şekil {n}.</b> "
                         f"{inline(cap, labels)}</figcaption></figure>")
        elif kind == "longtable":
            parts.append(table(inner[inner.find("\\toprule"):], labels))
        pos = env_end + len(f"\\end{{{kind}}}")
    if "<div class='refs'>" in "".join(parts):
        parts.append("</div>")
    return f"<!doctype html><html lang='tr'><meta charset='utf-8'><title>PcbCreator yöntem raporu</title><style>{CSS}</style><body>" \
           + "\n".join(parts) + "</body></html>"


def main() -> int:
    HTML.write_text(convert(TEX.read_text(encoding="utf-8")), encoding="utf-8")
    cmd = ["google-chrome-stable", "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={PDF}", HTML.as_uri()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    ok = PDF.exists() and PDF.stat().st_size > 10_000
    print(f"wrote {PDF}" if ok else f"PDF failed: {res.stderr[-400:]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
