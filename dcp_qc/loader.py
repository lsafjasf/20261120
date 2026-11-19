from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from .model import (
    AssetChunk,
    Diagnostic,
    DiagnosticCode,
    PackageDocuments,
)
from .parser import normalize_id, parse_asset_map, parse_cpl, parse_packing_list
from .xmlutil import find_xml_by_root


def _find_asset_map(start: Path) -> Path | None:
    if start.is_file():
        return start if start.name.lower().startswith("assetmap") else None
    for name in ("ASSETMAP", "ASSETMAP.xml", "assetmap", "assetmap.xml"):
        candidate = start / name
        if candidate.is_file():
            return candidate
    matches = [
        path
        for path in start.iterdir()
        if path.is_file() and path.name.lower().startswith("assetmap")
    ]
    return sorted(matches)[0] if matches else None


def resolve_chunk(root: Path, chunk: AssetChunk) -> Path | None:
    raw = Path(chunk.path)
    candidates = [root / raw]
    if chunk.volume_index is not None:
        candidates.extend(
            [
                root / f"VOL{chunk.volume_index}" / raw.name,
                root / f"VOL{chunk.volume_index}" / raw,
            ]
        )
    if raw.is_absolute():
        candidates.insert(0, raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    if raw.name:
        matches = [
            path
            for path in root.rglob(raw.name)
            if path.is_file()
        ]
        if matches:
            return sorted(matches, key=lambda item: len(item.parts))[0].resolve()
    return None


def _discover_xml_paths(
    root: Path,
    asset_map,
    pkl_paths: set[Path],
    cpl_id: str | None,
) -> tuple[list[Path], list[Path]]:
    pkl_candidates = set(pkl_paths)
    for entry in asset_map.entries.values() if asset_map else []:
        if not entry.packing_list:
            continue
        resolved = resolve_chunk(root, entry.chunks[0])
        if resolved:
            pkl_candidates.add(resolved)

    if not pkl_candidates:
        pkl_candidates.update(find_xml_by_root(root, "PackingList"))

    cpl_candidates = find_xml_by_root(root, "CompositionPlaylist")
    if cpl_id:
        wanted = normalize_id(cpl_id)
        selected: list[Path] = []
        for path in cpl_candidates:
            try:
                from .xmlutil import first_text, parse_xml

                if normalize_id(first_text(parse_xml(path), "Id")) == wanted:
                    selected.append(path)
            except (ElementTree.ParseError, OSError):
                continue
        if selected:
            cpl_candidates = selected
    return sorted(pkl_candidates), sorted(cpl_candidates)


def load_package_documents(
    package: str | Path,
    *,
    cpl_id: str | None = None,
    base_packages: tuple[str | Path, ...] = (),
) -> PackageDocuments:
    start = Path(package).expanduser()
    if not start.exists():
        raise FileNotFoundError(f"package path does not exist: {start}")
    asset_map_path = _find_asset_map(start)
    if asset_map_path is None:
        root = start if start.is_dir() else start.parent
        return PackageDocuments(
            root=root.resolve(),
            asset_map=None,
            packing_lists=[],
            cpls=[],
            errors=[
                Diagnostic(
                    code=DiagnosticCode.MISSING_REEL,
                    message=f"未找到 ASSETMAP: {start}",
                    asset_path=str(start),
                )
            ],
        )

    root = asset_map_path.parent.resolve()
    errors: list[Diagnostic] = []
    try:
        asset_map = parse_asset_map(asset_map_path, errors)
    except (ElementTree.ParseError, OSError) as exc:
        return PackageDocuments(
            root=root,
            asset_map=None,
            packing_lists=[],
            cpls=[],
            errors=[
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=f"ASSETMAP 无法解析: {exc}",
                    asset_path=str(asset_map_path),
                )
            ],
        )

    pkl_paths, cpl_paths = _discover_xml_paths(root, asset_map, set(), cpl_id)
    packing_lists = []
    for path in pkl_paths:
        try:
            packing_lists.append(parse_packing_list(path, errors))
        except (ElementTree.ParseError, OSError) as exc:
            errors.append(
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=f"PKL 无法解析: {exc}",
                    asset_path=str(path),
                )
            )

    cpls = []
    for path in cpl_paths:
        try:
            cpls.append(parse_cpl(path, errors))
        except (ElementTree.ParseError, OSError) as exc:
            errors.append(
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=f"CPL 无法解析: {exc}",
                    asset_path=str(path),
                )
            )

    if not packing_lists:
        errors.append(
            Diagnostic(
                code=DiagnosticCode.MISSING_REEL,
                message="包内未找到可解析的 PKL",
                asset_path=str(root),
            )
        )
    if not cpls:
        errors.append(
            Diagnostic(
                code=DiagnosticCode.MISSING_REEL,
                message="包内未找到可解析的 CPL",
                asset_path=str(root),
            )
        )

    documents = PackageDocuments(
        root=root,
        asset_map=asset_map,
        packing_lists=packing_lists,
        cpls=cpls,
        errors=errors,
    )
    seen_roots = {root}
    for base in base_packages:
        base_root = Path(base).expanduser()
        if not base_root.exists():
            documents.errors.append(
                Diagnostic(
                    code=DiagnosticCode.MISSING_REEL,
                    message=f"继承包路径不存在: {base_root}",
                    asset_path=str(base_root),
                )
            )
            continue
        base_documents = load_package_documents(base_root)
        if base_documents.root in seen_roots:
            continue
        seen_roots.add(base_documents.root)
        documents.base_roots.append(base_documents.root)
        if base_documents.asset_map:
            documents.inherited_asset_maps.append(base_documents.asset_map)
        documents.inherited_packing_lists.extend(base_documents.packing_lists)
        documents.errors.extend(base_documents.errors)
    return documents


def choose_cpl(documents: PackageDocuments, cpl_id: str | None = None):
    if not documents.cpls:
        return None
    if cpl_id:
        wanted = normalize_id(cpl_id)
        for cpl in documents.cpls:
            if normalize_id(cpl.cpl_id) == wanted or cpl.path.stem.lower() == wanted:
                return cpl
    return sorted(documents.cpls, key=lambda item: str(item.path))[0]
