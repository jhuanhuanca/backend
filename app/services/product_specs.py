from __future__ import annotations


def parse_spec_lines(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = label.strip()[:80]
        value = value.strip()[:240]
        if label and value:
            rows.append({"label": label, "value": value})
    return rows


def format_spec_lines(specs) -> str:
    rows = normalize_specs(specs)
    return "\n".join(f"{row['label']}: {row['value']}" for row in rows)


def parse_options(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = label.strip()[:80]
        values = [part.strip()[:80] for part in value.split(",") if part.strip()]
        if label and values:
            out[label] = values
    return out


def format_options(options) -> str:
    data = options if isinstance(options, dict) else {}
    return "\n".join(
        f"{key}: {', '.join(str(v) for v in values)}"
        for key, values in data.items()
        if values
    )


def parse_gallery(text: str) -> list[str]:
    urls: list[str] = []
    for line in (text or "").splitlines():
        url = line.strip()
        if url.startswith("http://") or url.startswith("https://"):
            urls.append(url[:500])
    return urls[:8]


def format_gallery(gallery) -> str:
    if not gallery:
        return ""
    if isinstance(gallery, str):
        return gallery
    return "\n".join(str(url) for url in gallery if url)


def normalize_specs(raw) -> list[dict[str, str]]:
    if not raw:
        return []
    if isinstance(raw, str):
        return parse_spec_lines(raw)
    if isinstance(raw, dict):
        return [{"label": str(k), "value": str(v)} for k, v in raw.items() if k]
    rows: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if label and value:
                    rows.append({"label": label, "value": value})
    return rows


def normalize_gallery(raw, cover: str = "") -> list[str]:
    urls: list[str] = []
    cover = (cover or "").strip()
    if cover:
        urls.append(cover)
    extra = raw if isinstance(raw, list) else parse_gallery(str(raw or ""))
    for url in extra:
        url = str(url).strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def normalize_options(raw) -> dict[str, list[str]]:
    if not raw:
        return {}
    if isinstance(raw, str):
        return parse_options(raw)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, values in raw.items():
        if isinstance(values, str):
            parts = [p.strip() for p in values.split(",") if p.strip()]
        elif isinstance(values, list):
            parts = [str(v).strip() for v in values if str(v).strip()]
        else:
            parts = []
        if key and parts:
            out[str(key)] = parts
    return out
