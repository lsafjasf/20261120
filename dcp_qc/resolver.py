from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .loader import resolve_chunk
from .model import (
    AssetChunk,
    AssetMap,
    AssetMapEntry,
    PackageDocuments,
    PackingAsset,
    PackingList,
)


@dataclass
class AssetLocation:
    asset_id: str
    map_entry: AssetMapEntry | None
    map_root: Path | None
    inherited: bool
    resolved_chunks: list[Path]
    missing_chunks: list[AssetChunk]
    volumes: list[str]

    @property
    def volume(self) -> str | None:
        return ", ".join(self.volumes) if self.volumes else None

    @property
    def path(self) -> str | None:
        if self.map_entry and self.map_entry.chunks:
            return " + ".join(chunk.path for chunk in self.map_entry.chunks)
        return None

    @property
    def complete(self) -> bool:
        return bool(self.resolved_chunks) and not self.missing_chunks


def volume_label(chunk: AssetChunk, path: Path | None = None) -> str:
    if chunk.volume_index is not None:
        return f"VOL{chunk.volume_index}"
    for part in Path(chunk.path).parts:
        if part.upper().startswith("VOL") and part[3:].isdigit():
            return part.upper()
    if path is not None and path.parent.name.upper().startswith("VOL"):
        return path.parent.name.upper()
    if path is not None:
        return path.parent.name or "."
    return "unknown"


class AssetResolver:
    def __init__(self, documents: PackageDocuments) -> None:
        self.documents = documents
        self._maps: list[tuple[AssetMap, Path, bool]] = []
        if documents.asset_map:
            self._maps.append((documents.asset_map, documents.root, False))
        for asset_map in documents.inherited_asset_maps:
            self._maps.append((asset_map, asset_map.path.parent, True))

        self._packing: list[tuple[PackingList, bool]] = [
            (pkl, False) for pkl in documents.packing_lists
        ] + [(pkl, True) for pkl in documents.inherited_packing_lists]

    def map_entry(self, asset_id: str) -> tuple[AssetMapEntry, Path, bool] | None:
        for asset_map, root, inherited in self._maps:
            entry = asset_map.entries.get(asset_id)
            if entry:
                return entry, root, inherited
        return None

    def packing_asset(self, asset_id: str) -> tuple[PackingAsset, bool] | None:
        for pkl, inherited in self._packing:
            asset = pkl.assets.get(asset_id)
            if asset:
                return asset, inherited
        return None

    def is_inherited(self, asset_id: str) -> bool:
        if self.documents.asset_map and asset_id in self.documents.asset_map.entries:
            return False
        for pkl in self.documents.packing_lists:
            if asset_id in pkl.assets:
                return False
        return self.map_entry(asset_id) is not None or self.packing_asset(asset_id) is not None

    def locate(self, asset_id: str) -> AssetLocation:
        mapped = self.map_entry(asset_id)
        inherited = self.is_inherited(asset_id)
        if mapped is None:
            return AssetLocation(
                asset_id=asset_id,
                map_entry=None,
                map_root=None,
                inherited=inherited,
                resolved_chunks=[],
                missing_chunks=[],
                volumes=[],
            )

        entry, root, _ = mapped
        resolved: list[Path] = []
        missing: list[AssetChunk] = []
        volumes: list[str] = []
        for chunk in entry.chunks:
            chunk_path = resolve_chunk(root, chunk)
            volumes.append(volume_label(chunk, chunk_path))
            if chunk_path is None:
                missing.append(chunk)
            else:
                resolved.append(chunk_path)
        return AssetLocation(
            asset_id=asset_id,
            map_entry=entry,
            map_root=root,
            inherited=inherited,
            resolved_chunks=resolved,
            missing_chunks=missing,
            volumes=volumes,
        )

    def all_primary_packing_assets(self) -> dict[str, PackingAsset]:
        result: dict[str, PackingAsset] = {}
        for pkl in self.documents.packing_lists:
            result.update(pkl.assets)
        return result

    def all_packing_assets(self) -> dict[str, PackingAsset]:
        result = self.all_primary_packing_assets()
        for pkl in self.documents.inherited_packing_lists:
            for asset_id, asset in pkl.assets.items():
                result.setdefault(asset_id, asset)
        return result
