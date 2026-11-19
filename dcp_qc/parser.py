from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from .model import (
    AssetChunk,
    AssetKind,
    AssetMap,
    AssetMapEntry,
    CompositionPlaylist,
    CplReel,
    CplResource,
    Diagnostic,
    DiagnosticCode,
    EditRate,
    PackageDocuments,
    PackingAsset,
    PackingList,
)
from .xmlutil import (
    children,
    descendants,
    find_xml_by_root,
    first_child,
    first_text,
    local_name,
    parse_xml,
)


def normalize_id(value: str | None) -> str:
    return (value or "").strip().lower()


def _diagnostic(
    code: DiagnosticCode, message: str, path: Path | None = None
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message=message,
        asset_path=str(path) if path else None,
    )


def _integer(
    element: ElementTree.Element,
    name: str,
    errors: list[Diagnostic],
    context: str,
) -> int | None:
    text = first_text(element, name)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                f"{context}: {name} 不是整数: {text!r}",
            )
        )
        return None


def parse_asset_map(path: Path, errors: list[Diagnostic]) -> AssetMap:
    root = parse_xml(path)
    asset_map = AssetMap(path=path)
    asset_list = first_child(root, "AssetList")
    if asset_list is None:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                "ASSETMAP 缺少 AssetList",
                path,
            )
        )
        return asset_map

    for asset in children(asset_list, "Asset"):
        asset_id = normalize_id(first_text(asset, "Id"))
        if not asset_id:
            errors.append(
                _diagnostic(
                    DiagnosticCode.REFERENCE_MISMATCH,
                    "ASSETMAP 中存在缺少 Id 的资产",
                    path,
                )
            )
            continue
        if asset_id in asset_map.entries:
            errors.append(
                _diagnostic(
                    DiagnosticCode.REFERENCE_MISMATCH,
                    f"ASSETMAP 资产 Id 重复: {asset_id}",
                    path,
                )
            )
            continue

        chunk_nodes = descendants(asset, "Chunk")
        chunks: list[AssetChunk] = []
        for chunk in chunk_nodes:
            chunk_path = first_text(chunk, "Path")
            if not chunk_path:
                continue
            volume_index = _integer(
                chunk, "VolumeIndex", errors, f"ASSETMAP 资产 {asset_id}"
            )
            chunks.append(AssetChunk(chunk_path, volume_index))

        if not chunks:
            chunk_path = first_text(asset, "Path")
            if chunk_path:
                volume_index = _integer(
                    asset, "VolumeIndex", errors, f"ASSETMAP 资产 {asset_id}"
                )
                chunks.append(AssetChunk(chunk_path, volume_index))

        if not chunks:
            errors.append(
                _diagnostic(
                    DiagnosticCode.REFERENCE_MISMATCH,
                    f"ASSETMAP 资产 {asset_id} 没有可用 Path",
                    path,
                )
            )
            continue

        packing_text = (first_text(asset, "PackingList") or "").lower()
        asset_map.entries[asset_id] = AssetMapEntry(
            asset_id=asset_id,
            chunks=chunks,
            packing_list=packing_text in {"true", "1", "yes"},
        )
    return asset_map


def parse_packing_list(path: Path, errors: list[Diagnostic]) -> PackingList:
    root = parse_xml(path)
    pkl = PackingList(
        pkl_id=normalize_id(first_text(root, "Id")),
        path=path,
        annotation=first_text(root, "Annotation"),
        issuer=first_text(root, "Issuer"),
        issue_date=first_text(root, "IssueDate"),
    )
    if not pkl.pkl_id:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                "PKL 缺少 Id",
                path,
            )
        )

    asset_list = first_child(root, "AssetList")
    if asset_list is None:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                "PKL 缺少 AssetList",
                path,
            )
        )
        return pkl

    for asset in children(asset_list, "Asset"):
        asset_id = normalize_id(first_text(asset, "Id"))
        if not asset_id:
            errors.append(
                _diagnostic(
                    DiagnosticCode.REFERENCE_MISMATCH,
                    "PKL 中存在缺少 Id 的资产",
                    path,
                )
            )
            continue
        if asset_id in pkl.assets:
            errors.append(
                _diagnostic(
                    DiagnosticCode.REFERENCE_MISMATCH,
                    f"PKL 资产 Id 重复: {asset_id}",
                    path,
                )
            )
            continue
        size = _integer(asset, "Size", errors, f"PKL 资产 {asset_id}")
        pkl.assets[asset_id] = PackingAsset(
            asset_id=asset_id,
            path=(first_text(asset, "OriginalFileName") or "").strip(),
            size=size,
            hash_value=(first_text(asset, "Hash") or "").strip().lower() or None,
            hash_algorithm=(
                (first_text(asset, "HashAlgorithm") or "").strip().upper() or None
            ),
            mime_type=first_text(asset, "Type"),
            annotation=first_text(asset, "Annotation"),
        )
    return pkl


_RESOURCE_NAMES = {
    "MainPicture": AssetKind.PICTURE,
    "MainSound": AssetKind.SOUND,
    "MainSubtitle": AssetKind.SUBTITLE,
    "MainClosedCaption": AssetKind.CAPTION,
    "AuxData": AssetKind.OTHER,
}


def _parse_resource(
    element: ElementTree.Element,
    kind: AssetKind,
    errors: list[Diagnostic],
    path: Path,
) -> CplResource:
    asset_id = normalize_id(first_text(element, "Id"))
    if not asset_id:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                f"CPL {kind.value} 资源缺少 Id",
                path,
            )
        )
    rate_text = first_text(element, "EditRate")
    try:
        edit_rate = EditRate.parse(rate_text)
    except (TypeError, ValueError):
        edit_rate = None
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                f"CPL 资产 {asset_id or '(无 Id)'} 的 EditRate 无效: "
                f"{rate_text!r}",
                path,
            )
        )
    context = f"CPL 资产 {asset_id or '(无 Id)'}"
    return CplResource(
        kind=kind,
        asset_id=asset_id,
        edit_rate=edit_rate,
        intrinsic_duration=_integer(element, "IntrinsicDuration", errors, context),
        entry_point=_integer(element, "EntryPoint", errors, context),
        duration=_integer(element, "Duration", errors, context),
        annotation=first_text(element, "Annotation"),
    )


def parse_cpl(path: Path, errors: list[Diagnostic]) -> CompositionPlaylist:
    root = parse_xml(path)
    cpl = CompositionPlaylist(
        cpl_id=normalize_id(first_text(root, "Id")),
        path=path,
        title=first_text(root, "ContentTitleText") or path.stem,
        content_kind=first_text(root, "ContentKind"),
        annotation=first_text(root, "Annotation"),
    )
    if not cpl.cpl_id:
        errors.append(
            _diagnostic(DiagnosticCode.REFERENCE_MISMATCH, "CPL 缺少 Id", path)
        )

    reel_list = first_child(root, "ReelList")
    if reel_list is None:
        errors.append(
            _diagnostic(
                DiagnosticCode.REFERENCE_MISMATCH,
                "CPL 缺少 ReelList",
                path,
            )
        )
        return cpl

    for index, reel_element in enumerate(children(reel_list, "Reel"), start=1):
        reel_id = first_text(reel_element, "Id") or f"reel-{index}"
        resources: list[CplResource] = []
        for resource_name, kind in _RESOURCE_NAMES.items():
            candidates = descendants(reel_element, resource_name)
            for resource_element in candidates:
                resources.append(
                    _parse_resource(resource_element, kind, errors, path)
                )
        if not resources:
            errors.append(
                _diagnostic(
                    DiagnosticCode.MISSING_REEL,
                    f"CPL 卷 {reel_id} 没有画面、声音或字幕资产引用",
                    path,
                )
            )
        cpl.reels.append(CplReel(reel_id=reel_id, resources=resources))
    if not cpl.reels:
        errors.append(
            _diagnostic(
                DiagnosticCode.MISSING_REEL,
                "CPL 的 ReelList 为空",
                path,
            )
        )
    return cpl
