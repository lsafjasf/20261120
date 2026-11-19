from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if local_name(child.tag) == name]


def first_child(
    element: ElementTree.Element | None, name: str
) -> ElementTree.Element | None:
    if element is None:
        return None
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def first_text(element: ElementTree.Element | None, name: str) -> str | None:
    child = first_child(element, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def descendants(
    element: ElementTree.Element, name: str
) -> list[ElementTree.Element]:
    return [item for item in element.iter() if local_name(item.tag) == name]


def parse_xml(path: Path) -> ElementTree.Element:
    return ElementTree.parse(path).getroot()


def xml_root_name(path: Path) -> str:
    return local_name(parse_xml(path).tag)


def is_xml_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".xml"


def find_xml_by_root(root: Path, expected: str) -> list[Path]:
    matches: list[Path] = []
    for path in sorted(root.rglob("*.xml")):
        try:
            if xml_root_name(path).lower() == expected.lower():
                matches.append(path)
        except (ElementTree.ParseError, OSError):
            continue
    return matches
