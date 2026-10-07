"""Program-owned source evidence: bounded normalization and stable document-local IDs."""

import unicodedata


_PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalized_map(text: str):
    chars, positions = [], []
    for position, char in enumerate(text):
        if char.isspace() or char in ("\u200b", "\ufeff"):
            continue
        normalized = unicodedata.normalize("NFKC", char).translate(_PUNCTUATION)
        for value in normalized:
            if not value.isspace():
                chars.append(value)
                positions.append(position)
    return "".join(chars), positions


def locate_quote(text: str, quote: str):
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("来源引句不能为空")
    offset = text.find(quote)
    if offset >= 0:
        return offset, offset + len(quote)
    source, positions = normalized_map(text)
    wanted, _ = normalized_map(quote)
    if not wanted:
        raise ValueError("来源引句没有有效文字")
    offset = source.find(wanted)
    if offset < 0:
        raise ValueError("来源文字无法定位；不会用语义近似伪造引用")
    return positions[offset], positions[offset + len(wanted) - 1] + 1


def source_page(data: dict, start: int, end: int):
    offset = 0
    for segment in data["document"]["segments"]:
        header = f"[第 {segment['page']} 页]\n" if segment.get("page") else ""
        lower = offset + len(header)
        upper = lower + len(segment["text"])
        if lower <= start and end <= upper:
            return segment.get("page")
        offset = upper + 2
    return None


def build_evidence_registry(data: dict):
    chunks = {chunk["id"]: chunk for chunk in data["chunks"]}
    records, references = {}, []
    for analysis in data["analyses"]:
        chunk = chunks[analysis["chunk_id"]]
        for topic in analysis["topics"]:
            keys = []
            for quote in topic["quotes"]:
                lower, upper = locate_quote(chunk["text"], quote)
                lower, upper = lower + chunk["start"], upper + chunk["start"]
                key = (lower, upper)
                records[key] = {"quote": data["content"][lower:upper], "start": lower, "end": upper,
                                "page": source_page(data, lower, upper)}
                keys.append(key)
            references.append((topic, keys))
    ids = {key: f"e{number:04}" for number, key in enumerate(sorted(records), start=1)}
    registry = {ids[key]: {"id": ids[key], **value} for key, value in records.items()}
    metadata_records = {}
    for value in data.get("document_metadata", {}).get("evidence", []):
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        metadata_records.setdefault(value, {"quote": value, "start": None, "end": None,
                                            "page": None, "kind": "metadata"})
    metadata_ids = {value: f"m{number:04}" for number, value in enumerate(sorted(metadata_records), start=1)}
    registry.update({metadata_ids[value]: {"id": metadata_ids[value], **record}
                     for value, record in metadata_records.items()})
    for topic, keys in references:
        topic["evidence_ids"] = list(dict.fromkeys(ids[key] for key in keys))
        topic["evidence_ids"].extend(metadata_ids[value] for value in topic.get("metadata_evidence", [])
                                      if value in metadata_ids)
        topic["quotes"] = [records[key]["quote"] for key in dict.fromkeys(keys)]
    data["evidence_registry"] = registry
    return registry


def resolve_evidence(item: dict, data: dict, source_path: str):
    registry = data["evidence_registry"]
    if "evidence_ids" in item:
        ids = item["evidence_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(id, str) for id in ids):
            raise ValueError("每页必须引用1至4个来源证据编号")
        ids = list(dict.fromkeys(id.strip().lower() for id in ids))
        if any(id not in registry or id not in data.get("active_evidence_ids", registry) for id in ids):
            raise ValueError("页面引用了未知证据编号，请从输入 evidence_registry 选择")
        return [{"source": source_path, **registry[id]} for id in ids]
    # Transitional support for existing proposals/tests; never fuzzy semantic matching.
    quotes = item.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 4:
        raise ValueError("页面缺少来源证据编号")
    result = []
    for quote in quotes:
        try:
            lower, upper = locate_quote(data["content"], quote)
        except ValueError:
            raise ValueError("生成页面的来源引句不存在，拒绝入库") from None
        record = next((value for value in registry.values() if value["start"] == lower and value["end"] == upper),
                      {"quote": data["content"][lower:upper], "start": lower, "end": upper, "page": source_page(data, lower, upper)})
        result.append({"source": source_path, **record})
    return result
