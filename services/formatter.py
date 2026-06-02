from services.telegram import strip_html


def format_post(title: str, content: str | None, link: str | None = None) -> str:
    lines = [f"<b>{strip_html(title)}</b>", ""]
    if content:
        stripped = strip_html(content)
        for p in stripped.split("\n\n"):
            p = p.strip()
            if p:
                lines.append(p)
                lines.append("")
    if link:
        lines.append(f"<a href='{link}'>Источник</a>")
    return "\n".join(lines).strip()
