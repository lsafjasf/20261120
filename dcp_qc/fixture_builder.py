from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from .model import AssetKind

ASSETMAP_NS = "http://www.smpte-ra.org/schemas/429-9/2007/AM"
PKL_NS = "http://www.smpte-ra.org/schemas/429-8/2007/PKL"
CPL_NS = "http://www.smpte-ra.org/schemas/429-7/2006/CPL"

PKL_ID = "urn:uuid:00000000-0000-4000-8000-000000000001"
CPL_ID = "urn:uuid:00000000-0000-4000-8000-000000000002"
SUPP_PKL_ID = "urn:uuid:00000000-0000-4000-8000-000000000011"
SUPP_CPL_ID = "urn:uuid:00000000-0000-4000-8000-000000000012"

PIC_R1 = "urn:uuid:00000000-0000-4000-8000-000000000101"
SND_R1 = "urn:uuid:00000000-0000-4000-8000-000000000102"
SUB_R1 = "urn:uuid:00000000-0000-4000-8000-000000000103"
PIC_R2 = "urn:uuid:00000000-0000-4000-8000-000000000201"
SND_R2 = "urn:uuid:00000000-0000-4000-8000-000000000202"
SUB_R2 = "urn:uuid:00000000-0000-4000-8000-000000000203"
SUPP_SUB_R1 = "urn:uuid:00000000-0000-4000-8000-000000000301"
SUPP_SUB_R2 = "urn:uuid:00000000-0000-4000-8000-000000000302"


@dataclass
class ChunkDef:
    path: str
    content: bytes
    volume_index: int


@dataclass
class AssetDef:
    asset_id: str
    mime_type: str
    chunks: list[ChunkDef]

    @property
    def data(self) -> bytes:
        return b"".join(chunk.content for chunk in self.chunks)


@dataclass
class ResourceDef:
    kind: AssetKind
    asset_id: str
    edit_rate: str
    intrinsic_duration: int
    entry_point: int
    duration: int


@dataclass
class ReelDef:
    reel_id: str
    resources: list[ResourceDef] = field(default_factory=list)


def _text(parent: ElementTree.Element, name: str, value: object) -> None:
    child = ElementTree.SubElement(parent, name)
    child.text = str(value)


def _write_xml(path: Path, root: ElementTree.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if root.tag.startswith("{"):
        namespace = root.tag[1:].split("}", 1)[0]
        ElementTree.register_namespace("", namespace)
    ElementTree.indent(root, space="  ")
    ElementTree.ElementTree(root).write(
        path, encoding="utf-8", xml_declaration=True
    )


def _asset_map_xml(
    pkl_path: str,
    pkl_id: str,
    cpl_path: str,
    cpl_id: str,
    assets: list[AssetDef],
) -> ElementTree.Element:
    root = ElementTree.Element(f"{{{ASSETMAP_NS}}}AssetMap")
    _text(root, "Id", f"urn:uuid:{hashlib.md5(str(assets).encode()).hexdigest()[:8]}"
          "-0000-4000-8000-000000000000")
    _text(root, "Creator", "dcp-qc fixture builder")
    _text(root, "VolumeCount", "2")
    asset_list = ElementTree.SubElement(root, "AssetList")

    pkl = ElementTree.SubElement(asset_list, "Asset")
    _text(pkl, "Id", pkl_id)
    _text(pkl, "PackingList", "true")
    chunks = ElementTree.SubElement(pkl, "ChunkList")
    chunk = ElementTree.SubElement(chunks, "Chunk")
    _text(chunk, "Path", pkl_path)
    _text(chunk, "VolumeIndex", "1")

    cpl = ElementTree.SubElement(asset_list, "Asset")
    _text(cpl, "Id", cpl_id)
    chunks = ElementTree.SubElement(cpl, "ChunkList")
    chunk = ElementTree.SubElement(chunks, "Chunk")
    _text(chunk, "Path", cpl_path)
    _text(chunk, "VolumeIndex", "1")

    for asset in assets:
        item = ElementTree.SubElement(asset_list, "Asset")
        _text(item, "Id", asset.asset_id)
        chunks = ElementTree.SubElement(item, "ChunkList")
        for chunk_def in asset.chunks:
            chunk = ElementTree.SubElement(chunks, "Chunk")
            _text(chunk, "Path", chunk_def.path)
            _text(chunk, "VolumeIndex", chunk_def.volume_index)
    return root


def _pkl_xml(pkl_id: str, assets: list[AssetDef]) -> ElementTree.Element:
    root = ElementTree.Element(f"{{{PKL_NS}}}PackingList")
    _text(root, "Id", pkl_id)
    _text(root, "Annotation", "dcp-qc placeholders")
    _text(root, "IssueDate", "2026-11-20T00:00:00+00:00")
    _text(root, "Issuer", "dcp-qc")
    _text(root, "Creator", "dcp-qc fixture builder")
    asset_list = ElementTree.SubElement(root, "AssetList")
    for asset in assets:
        data = asset.data
        item = ElementTree.SubElement(asset_list, "Asset")
        _text(item, "Id", asset.asset_id)
        _text(item, "Annotation", "test placeholder")
        _text(item, "Hash", hashlib.sha256(data).hexdigest())
        _text(item, "HashAlgorithm", "SHA256")
        _text(item, "Size", len(data))
        _text(item, "Type", asset.mime_type)
        _text(item, "OriginalFileName", Path(asset.chunks[0].path).name)
    return root


_RESOURCE_TAGS = {
    AssetKind.PICTURE: "MainPicture",
    AssetKind.SOUND: "MainSound",
    AssetKind.SUBTITLE: "MainSubtitle",
    AssetKind.CAPTION: "MainClosedCaption",
}


def _cpl_xml(cpl_id: str, title: str, reels: list[ReelDef]) -> ElementTree.Element:
    root = ElementTree.Element(f"{{{CPL_NS}}}CompositionPlaylist")
    _text(root, "Id", cpl_id)
    _text(root, "Annotation", title)
    _text(root, "IssueDate", "2026-11-20T00:00:00+00:00")
    _text(root, "Issuer", "dcp-qc")
    _text(root, "Creator", "dcp-qc fixture builder")
    _text(root, "ContentTitleText", title)
    _text(root, "ContentKind", "feature")
    reel_list = ElementTree.SubElement(root, "ReelList")
    for reel_def in reels:
        reel = ElementTree.SubElement(reel_list, "Reel")
        _text(reel, "Id", reel_def.reel_id)
        asset_list = ElementTree.SubElement(reel, "AssetList")
        for resource in reel_def.resources:
            item = ElementTree.SubElement(asset_list, _RESOURCE_TAGS[resource.kind])
            _text(item, "Id", resource.asset_id)
            _text(item, "EditRate", resource.edit_rate)
            _text(item, "IntrinsicDuration", resource.intrinsic_duration)
            _text(item, "EntryPoint", resource.entry_point)
            _text(item, "Duration", resource.duration)
    return root


def _write_package(
    root: Path,
    *,
    pkl_id: str,
    cpl_id: str,
    title: str,
    assets: list[AssetDef],
    reels: list[ReelDef],
    pkl_path: str = "VOL1/PKL.xml",
    cpl_path: str = "VOL1/CPL.xml",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        for chunk in asset.chunks:
            path = root / chunk.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(chunk.content)
    _write_xml(
        root / pkl_path,
        _pkl_xml(pkl_id, assets),
    )
    _write_xml(
        root / cpl_path,
        _cpl_xml(cpl_id, title, reels),
    )
    _write_xml(
        root / "ASSETMAP.xml",
        _asset_map_xml(pkl_path, pkl_id, cpl_path, cpl_id, assets),
    )


def _placeholder(label: str, size: int = 96) -> bytes:
    unit = f"DCP-QC-PLACEHOLDER:{label}\n".encode("ascii")
    return (unit * (size // len(unit) + 1))[:size]


def _base_assets() -> list[AssetDef]:
    return [
        AssetDef(PIC_R1, "application/mxf", [ChunkDef(
            "VOL1/picture_r1.mxf", _placeholder("picture-r1", 160), 1
        )]),
        AssetDef(SND_R1, "audio/mxf", [ChunkDef(
            "VOL1/sound_r1.mxf", _placeholder("sound-r1", 160), 1
        )]),
        AssetDef(SUB_R1, "application/xml", [ChunkDef(
            "VOL1/subtitle_r1.xml", _placeholder("subtitle-r1", 120), 1
        )]),
        AssetDef(PIC_R2, "application/mxf", [ChunkDef(
            "VOL2/picture_r2.mxf", _placeholder("picture-r2", 160), 2
        )]),
        AssetDef(SND_R2, "audio/mxf", [
            ChunkDef("VOL1/sound_r2.part1.mxf", _placeholder("sound-r2-a", 80), 1),
            ChunkDef("VOL2/sound_r2.part2.mxf", _placeholder("sound-r2-b", 80), 2),
        ]),
        AssetDef(SUB_R2, "application/xml", [ChunkDef(
            "VOL2/subtitle_r2.xml", _placeholder("subtitle-r2", 120), 2
        )]),
    ]


def _base_reels(
    *, r2_sound_duration: int = 480_000, r2_sound_intrinsic: int = 480_000
) -> list[ReelDef]:
    def resources(
        picture: str,
        sound: str,
        subtitle: str,
        sound_duration: int,
        sound_intrinsic: int,
    ) -> list[ResourceDef]:
        return [
            ResourceDef(AssetKind.PICTURE, picture, "24 1", 240, 0, 240),
            ResourceDef(
                AssetKind.SOUND,
                sound,
                "48000 1",
                sound_intrinsic,
                0,
                sound_duration,
            ),
            ResourceDef(AssetKind.SUBTITLE, subtitle, "24 1", 240, 0, 240),
        ]

    return [
        ReelDef(
            "reel-1",
            resources(PIC_R1, SND_R1, SUB_R1, 480_000, 480_000),
        ),
        ReelDef(
            "reel-2",
            resources(
                PIC_R2,
                SND_R2,
                SUB_R2,
                r2_sound_duration,
                r2_sound_intrinsic,
            ),
        ),
    ]


def build_two_reel(
    root: Path,
    *,
    title: str = "DCP QC two-reel placeholder",
    r2_sound_duration: int = 480_000,
    r2_sound_intrinsic: int = 480_000,
) -> Path:
    root = Path(root)
    _write_package(
        root,
        pkl_id=PKL_ID,
        cpl_id=CPL_ID,
        title=title,
        assets=_base_assets(),
        reels=_base_reels(
            r2_sound_duration=r2_sound_duration,
            r2_sound_intrinsic=r2_sound_intrinsic,
        ),
    )
    return root


def build_supplemental(root: Path) -> Path:
    root = Path(root)
    assets = [
        AssetDef(SUPP_SUB_R1, "application/xml", [ChunkDef(
            "VOL1/subtitle_r1_v2.xml", _placeholder("subtitle-r1-v2", 120), 1
        )]),
        AssetDef(SUPP_SUB_R2, "application/xml", [ChunkDef(
            "VOL1/subtitle_r2_v2.xml", _placeholder("subtitle-r2-v2", 120), 1
        )]),
    ]
    reels = [
        ReelDef("reel-1", [
            ResourceDef(AssetKind.PICTURE, PIC_R1, "24 1", 240, 0, 240),
            ResourceDef(AssetKind.SOUND, SND_R1, "48000 1", 480_000, 0, 480_000),
            ResourceDef(AssetKind.SUBTITLE, SUPP_SUB_R1, "24 1", 240, 0, 240),
        ]),
        ReelDef("reel-2", [
            ResourceDef(AssetKind.PICTURE, PIC_R2, "24 1", 240, 0, 240),
            ResourceDef(AssetKind.SOUND, SND_R2, "48000 1", 480_000, 0, 480_000),
            ResourceDef(AssetKind.SUBTITLE, SUPP_SUB_R2, "24 1", 240, 0, 240),
        ]),
    ]
    _write_package(
        root,
        pkl_id=SUPP_PKL_ID,
        cpl_id=SUPP_CPL_ID,
        title="DCP QC supplemental subtitle placeholder",
        assets=assets,
        reels=reels,
    )
    return root


def build_rate_overflow(root: Path) -> Path:
    return build_two_reel(
        root,
        title="DCP QC different-edit-rate overflow placeholder",
        r2_sound_duration=490_000,
        r2_sound_intrinsic=960_000,
    )


def build_inherited_missing(root: Path) -> tuple[Path, Path]:
    root = Path(root)
    base = build_two_reel(root / "base")
    (base / "VOL2" / "picture_r2.mxf").unlink()
    supplement = build_supplemental(root / "supplemental")
    return supplement, base
