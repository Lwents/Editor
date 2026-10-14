from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher


CharacterNameRegistry = dict[str, str]

# Preferred spellings established by the series calibration clips. Apply
# only to complete source names supported by the context, not ordinary prose.
_CALIBRATED_SOURCE_NAMES = {'绾绾': 'Oản Oản', '箫池': 'Tiêu Trì', '萧池': 'Tiêu Trì'}


# Vietnamese audiences commonly know this character as "Chaien".  Models
# alternate between Japanese romanisations even inside one response, so keep a
# small franchise-specific preference while the rest of this module remains
# driven by each video's AI context/glossary.
_DORAEMON_NAME_GROUPS: dict[str, tuple[str, ...]] = {
    "Doraemon": (
        "Doraemon",
        "Doremon",
        "Dozemon",
    ),
    "Chaien": (
        "Chaien",
        "Gian",
        "Jaian",
        "Takeshi Gouda",
        "Gouda Takeshi",
    ),
    "Suneo": (
        "Suneo",
        "Xê-kô",
        "Xê kô",
        "Xeko",
        "Honekawa Suneo",
        "Suneo Honekawa",
    ),
}

_ALIAS_FIELDS = {
    "alias",
    "aliases",
    "source",
    "source_name",
    "original",
    "original_name",
    "romanized_name",
    "other_names",
    "also_known_as",
    "asr_variants",
    "ocr_variants",
}

# Character profiles also list forms of address. They identify the speaker's
# relationship, but must not be rewritten as the person's full name in dialogue.
_ADDRESS_ALIASES = {
    "王爷", "殿下", "陛下", "皇上", "母妃", "父皇", "公子", "大人",
    "vương gia", "điện hạ", "bệ hạ", "hoàng thượng", "mẫu phi", "phụ hoàng",
    "công tử", "đại nhân", "thuộc hạ", "mẹ", "cha", "con", "huynh", "tỷ",
    "娘娘", "太后", "皇后", "皇妃", "贵妃", "夫人", "小姐", "少爷", "将军",
    "殿下", "皇兄", "皇姐", "母后", "父王", "母亲", "父亲", "哥哥", "姐姐",
    "nương nương", "thái hậu", "hoàng hậu", "hoàng phi", "quý phi", "phu nhân",
    "tiểu thư", "thiếu gia", "tướng quân", "hoàng huynh", "hoàng tỷ", "mẫu hậu",
    "phụ vương", "mẫu thân", "phụ thân",
    "摄政王", "王妃", "主子", "主人", "太子", "皇叔", "皇子", "公主",
    "nhiếp chính vương", "vương phi", "chủ tử", "chủ nhân", "thái tử", "hoàng thúc",
    "hoàng tử", "công chúa", "npc",
    "ta", "tôi", "ngươi", "các ngươi", "hắn", "nàng", "chàng", "thiếp",
    "anh ta", "anh ấy", "cô ta", "cô ấy", "phu quân", "nương tử", "tướng công",
    "vợ", "chồng", "con trai", "con gái", "nữ phản diện số một", "nam phản diện số một",
    "diêm vương sống", "夫君", "娘子", "相公", "妻子", "丈夫", "儿子", "女儿",
    "thái y", "太医", "bác sĩ", "医生", "thích khách", "thị vệ", "tỳ nữ", "cấm quân", "禁军",
    "妖女", "妖后", "yêu nữ", "yêu hậu",
}


def _is_address_alias(value: str) -> bool:
    return (value.casefold() in _ADDRESS_ALIASES
            or value.endswith("儿") or value.casefold().endswith(" nhi")
            or value in {'女反一', '男反一', '女主', '男主', '反派', '刺客', '侍卫', '侍女'}
            or bool(re.fullmatch(r'[\u3400-\u9fff]+', value) and value.endswith(
                ('王', '帝', '皇', '太后', '皇后', '妃', '公主', '太子', '将军', '大人', '殿下', '宗主', '教主',
                 '小姐', '少爷', '夫人', '妖女', '妖后')))
            or '余孽' in value or value.casefold().startswith(('tàn dư ', 'dư nghiệt ')))


def _is_character_description(value: str) -> bool:
    """A neutral scene label is not a verified proper name or faction name."""
    return bool(re.match(
        r'^(?:nhóm|đám|nam tử|nữ tử|nam nhân|nữ nhân|người|cô gái|thiếu nữ|chàng trai|'
        r'thích khách|thị vệ|tỳ nữ|hộ vệ|tên lính|tên thích khách|tên sát thủ|nhân vật|unknown|unnamed|woman|man in)(?:\s|$)',
        value.casefold())) or value in {'黑衣人', '白衣女子', '黑衣男子', '刺客', '侍卫', '宫女', '太医'}

_GLOSSARY_SOURCE_FIELDS = (
    "source",
    "asr_error",
    "original_ocr_asr",
    "original",
    "term",
)
_GLOSSARY_TARGET_FIELDS = (
    "target",
    "correction",
    "corrected_term",
    "vietnamese_translation",
    "translation",
    "meaning",
)


def build_character_name_registry(
    context: Mapping[str, object] | None,
    observed_names: Iterable[str] = (),
) -> CharacterNameRegistry:
    """Build alias -> canonical-name mappings for one video.

    The context model is allowed to return several historical schemas, so the
    parser accepts both structured character objects and the older descriptive
    strings.  Explicit aliases/glossary entries win over conservative fuzzy
    matching of names emitted by independent scene-analysis batches.
    """

    context = context if isinstance(context, Mapping) else {}
    observed = [_clean_name(value) for value in observed_names if _clean_name(value)]
    records = list(_character_records(context.get("characters")))
    doraemon_context = _is_doraemon_context(context, records, observed)

    registry: CharacterNameRegistry = {}
    if doraemon_context:
        for canonical, aliases in _DORAEMON_NAME_GROUPS.items():
            _register_name(registry, canonical, aliases)

    for raw_canonical, aliases in records:
        canonical_parts = _canonical_parts(raw_canonical)
        if not canonical_parts:
            continue
        # A slash usually separates identities used at different points in a
        # story (for example Mosuke / Yuka-tan).  Preserve both instead of
        # rewriting every occurrence to a composite label.
        if len(canonical_parts) > 1:
            for part in canonical_parts:
                preferred = canonical_character_name(part, registry)
                _register_name(registry, preferred, (part,))
            continue
        preferred = canonical_character_name(canonical_parts[0], registry)
        _register_name(registry, preferred, (raw_canonical, *aliases))

    _register_glossary_aliases(registry, context.get("glossary"))

    # Scene batches can still produce spelling variants absent from the
    # context response.  Only merge a high-confidence, unambiguous fuzzy match;
    # otherwise retain it as a distinct name rather than risk merging people.
    for observed_name in observed:
        if observed_name.upper().startswith("UNKNOWN"):
            continue
        canonical = canonical_character_name(observed_name, registry)
        if canonical == observed_name:
            fuzzy = _unique_fuzzy_canonical(observed_name, canonical_names(registry))
            canonical = fuzzy or observed_name
        _register_name(registry, canonical, (observed_name,))

    return registry


def canonical_names(registry: Mapping[str, str] | None) -> list[str]:
    if not registry:
        return []
    values: list[str] = []
    for canonical in registry.values():
        if canonical and canonical not in values:
            values.append(canonical)
    return values


def canonical_character_name(
    value: str,
    registry: Mapping[str, str] | None,
) -> str:
    """Canonicalize a standalone name field, accent/case insensitively."""

    cleaned = _clean_name(value)
    if not cleaned or not registry:
        return cleaned
    key = _name_key(cleaned)
    for alias, canonical in registry.items():
        if _name_key(alias) == key:
            return canonical
    return canonicalize_character_text(cleaned, registry)


def canonicalize_character_text(
    value: str,
    registry: Mapping[str, str] | None,
) -> str:
    """Replace explicit aliases without translating ordinary prose.

    Text replacement is deliberately case-sensitive.  For example, the name
    ``Gian`` must become ``Chaien`` while the Vietnamese noun in ``thời gian``
    must remain untouched.  Standalone character arrays use the more lenient
    :func:`canonical_character_name` lookup above.
    """

    text = unicodedata.normalize("NFC", str(value or ""))
    if not text or not registry:
        return text
    # Protect complete canonical names from aliases contained within them.
    # Replace in one pass so successive cleanup calls are idempotent.
    replacements = {alias: canonical for alias, canonical in registry.items() if alias and canonical}
    replacements.update({canonical: canonical for canonical in registry.values() if canonical})
    names = sorted(replacements, key=len, reverse=True)
    patterns = []
    for name in names:
        escaped = re.escape(name).replace(r"\ ", r"\s+")
        left = r"(?<!\w)" if name[0].isalnum() else ""
        right = r"(?!\w)" if name[-1].isalnum() else ""
        patterns.append(left + escaped + right)
    if not patterns:
        return text
    return re.sub(
        "|".join(f"(?P<n{index}>{pattern})" for index, pattern in enumerate(patterns)),
        lambda match: replacements[names[int(match.lastgroup[1:])]],
        text,
    )


def character_name_contract(registry: Mapping[str, str] | None) -> dict[str, object]:
    """Compact prompt payload that tells AI which exact spellings are legal."""

    if not registry:
        return {"canonical_names": [], "alias_to_canonical": {}}
    aliases: dict[str, str] = {}
    for alias, canonical in registry.items():
        if alias != canonical and alias not in aliases:
            aliases[alias] = canonical
        if len(aliases) >= 80:
            break
    return {
        "canonical_names": canonical_names(registry)[:60],
        "alias_to_canonical": aliases,
    }


def canonicalize_nested_strings(
    value: object,
    registry: Mapping[str, str] | None,
    *,
    key: str = "",
) -> object:
    """Canonicalize display text in persisted API data while preserving IDs/paths."""

    normalized_key = key.casefold()
    if normalized_key in {"artifact_paths"}:
        return value
    if isinstance(value, str):
        if (
            normalized_key in _ALIAS_FIELDS
            or normalized_key.endswith(("_id", "_ids", "_path", "_url"))
            or normalized_key in {"path", "url"}
        ):
            return value
        return canonicalize_character_text(value, registry)
    if isinstance(value, list):
        return [canonicalize_nested_strings(item, registry, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(canonicalize_nested_strings(item, registry, key=key) for item in value)
    if isinstance(value, Mapping):
        return {
            item_key: canonicalize_nested_strings(item_value, registry, key=str(item_key))
            for item_key, item_value in value.items()
        }
    return value


def _register_name(
    registry: CharacterNameRegistry,
    canonical: str,
    aliases: Iterable[str],
) -> None:
    canonical = _clean_name(canonical)
    aliases = tuple(aliases)
    if _is_character_description(canonical) or canonical.casefold() in _ADDRESS_ALIASES:
        return
    preferred = next((_CALIBRATED_SOURCE_NAMES[name] for raw in (canonical, *aliases)
                      for name in _alias_values(raw) if name in _CALIBRATED_SOURCE_NAMES), None)
    han_length = len(re.findall(r'[\u3400-\u9fff]', canonical))
    name_length = han_length or len(canonical.split())
    if preferred and (canonical in _CALIBRATED_SOURCE_NAMES or name_length <= len(preferred.split())):
        aliases = (canonical, *aliases)
        canonical = preferred
    # Some context responses put the source spelling in `name` and the
    # Vietnamese spelling in aliases. Never turn a translated name back into
    # Chinese during deterministic cleanup.
    if re.search(r'[\u3400-\u9fff]', canonical):
        source_name = canonical
        canonical = next((name for raw in aliases for name in _alias_values(raw)
                          if not re.search(r'[\u3400-\u9fff]', name)
                          and not _is_address_alias(name)
                          and not _is_character_description(name)
                          and (not preferred or len(name.split()) >= han_length)
                          and re.fullmatch(r'[\w\s\-]+', name)), '')
        aliases = (source_name, *aliases)
    if not canonical or _is_character_description(canonical) or canonical.casefold() in _ADDRESS_ALIASES:
        return
    registry.setdefault(canonical, canonical)
    for raw_alias in aliases:
        for alias in _alias_values(raw_alias):
            if alias != canonical and _is_address_alias(alias):
                continue
            if len(_name_key(alias).replace(" ", "")) < 2:
                continue
            # A source nickname is a valid shorter form; do not erase the
            # surname of the full name or expand a nickname in every line.
            target = _CALIBRATED_SOURCE_NAMES.get(alias, canonical)
            registry.setdefault(target, target)
            registry.setdefault(alias, target)
            folded = _ascii_name(alias)
            if folded != alias and len(folded.replace(" ", "")) >= 4:
                registry.setdefault(folded, target)


def _register_glossary_aliases(
    registry: CharacterNameRegistry,
    glossary: object,
) -> None:
    if not registry or not glossary:
        return
    entries: list[tuple[object, object]] = []
    if isinstance(glossary, Mapping):
        entries.extend(glossary.items())
    elif isinstance(glossary, list):
        for item in glossary:
            if not isinstance(item, Mapping):
                continue
            source = next((item.get(key) for key in _GLOSSARY_SOURCE_FIELDS if item.get(key)), None)
            target = next((item.get(key) for key in _GLOSSARY_TARGET_FIELDS if item.get(key)), None)
            if source is not None and target is not None:
                entries.append((source, target))

    names = canonical_names(registry)
    for source, target in entries:
        canonical = _canonical_from_glossary_target(str(target or ""), names)
        if canonical:
            _register_name(registry, canonical, _alias_values(source))


def _canonical_from_glossary_target(target: str, names: list[str]) -> str:
    target_key = _name_key(target)
    if not target_key:
        return ""
    matches = [
        name
        for name in names
        if _name_key(name) == target_key
        or target_key.startswith(_name_key(name) + " ")
        or (" " + _name_key(name) + " ") in (" " + target_key + " ")
    ]
    return max(matches, key=len, default="")


def _character_records(value: object) -> Iterable[tuple[str, tuple[str, ...]]]:
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, list):
        return []

    records: list[tuple[str, tuple[str, ...]]] = []
    for item in value:
        if isinstance(item, Mapping):
            canonical = _clean_name(item.get("name") or item.get("canonical_name") or "")
            if not canonical:
                continue
            aliases: list[str] = []
            for field in _ALIAS_FIELDS:
                if field in item:
                    aliases.extend(_alias_values(item.get(field)))
            records.append((canonical, tuple(aliases)))
            continue
        if not isinstance(item, str):
            continue
        canonical, aliases = _parse_character_string(item)
        if canonical:
            records.append((canonical, tuple(aliases)))
    return records


def _parse_character_string(value: str) -> tuple[str, list[str]]:
    text = unicodedata.normalize("NFC", value).strip()
    aliases: list[str] = []
    for match in re.finditer(
        r"\((?:ASR|OCR|alias|còn gọi|tên khác|nhận nhầm)[^:)]*:\s*([^)]+)\)",
        text,
        flags=re.IGNORECASE,
    ):
        aliases.extend(_alias_values(match.group(1)))
    canonical = re.split(
        r"\s*\((?=(?:ASR|OCR|alias|còn gọi|tên khác|nhận nhầm)\b)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    canonical = canonical.split(":", 1)[0].strip()
    return canonical, aliases


def _canonical_parts(value: str) -> list[str]:
    cleaned = _clean_name(value)
    if not cleaned:
        return []
    return [part.strip() for part in re.split(r"\s*/\s*", cleaned) if part.strip()]


def _alias_values(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_alias_values(item))
        return result
    text = _clean_name(value)
    if not text:
        return []
    return [
        part.strip(" \t\r\n\"'")
        for part in re.split(r"\s*(?:/|;|\||,)\s*", text)
        if part.strip(" \t\r\n\"'")
    ]


def _is_doraemon_context(
    context: Mapping[str, object],
    records: list[tuple[str, tuple[str, ...]]],
    observed: list[str],
) -> bool:
    title = _name_key(str(context.get("film_title") or ""))
    if "doraemon" in title or "doremon" in title:
        return True
    names = {_name_key(name) for name, _ in records} | {_name_key(name) for name in observed}
    return bool({"doraemon", "doremon", "dozemon"} & names) or ({"nobita", "shizuka"} <= names)


def _unique_fuzzy_canonical(value: str, names: list[str]) -> str:
    source = _name_key(value).replace(" ", "")
    if len(source) < 4 or not names:
        return ""
    scored = sorted(
        (
            SequenceMatcher(None, source, _name_key(name).replace(" ", "")).ratio(),
            name,
        )
        for name in names
        if len(_name_key(name).replace(" ", "")) >= 4
    )
    if not scored:
        return ""
    best_score, best_name = scored[-1]
    second_score = scored[-2][0] if len(scored) > 1 else 0.0
    if best_score >= 0.88 and best_score - second_score >= 0.06:
        return best_name
    return ""


def _clean_name(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()


def _name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _ascii_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))
