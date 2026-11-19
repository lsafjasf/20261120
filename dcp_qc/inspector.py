from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from .loader import choose_cpl, load_package_documents
from .model import (
    CompositionPlaylist,
    Diagnostic,
    DiagnosticCode,
    InspectionReport,
    PackageDocuments,
    PackingAsset,
    TimelineSegment,
    format_time,
)
from .parser import normalize_id
from .resolver import AssetLocation, AssetResolver, volume_label
from .timeline import build_timeline

_HASH_ALGORITHMS = {
    "MD5": "md5",
    "SHA1": "sha1",
    "SHA-1": "sha1",
    "SHA256": "sha256",
    "SHA-256": "sha256",
    "SHA384": "sha384",
    "SHA-384": "sha384",
    "SHA512": "sha512",
    "SHA-512": "sha512",
}


def inspect_package(
    package: str | Path,
    *,
    cpl_id: str | None = None,
    base_packages: tuple[str | Path, ...] = (),
    verify_hashes: bool = True,
    chunk_size: int = 1024 * 1024,
) -> InspectionReport:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    documents = load_package_documents(
        package, cpl_id=cpl_id, base_packages=base_packages
    )
    cpl = choose_cpl(documents, cpl_id)
    diagnostics = list(documents.errors)
    if cpl_id and (cpl is None or normalize_id(cpl.cpl_id) != normalize_id(cpl_id)):
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.REFERENCE_MISMATCH,
                message=f"未找到请求的 CPL: {cpl_id}",
            )
        )

    resolver = AssetResolver(documents)
    if cpl is None:
        return InspectionReport(
            root=documents.root,
            cpl=None,
            diagnostics=diagnostics,
        )

    _check_references(documents, cpl, resolver, diagnostics)
    timeline, total_duration = build_timeline(cpl, resolver, diagnostics)
    contexts = _timeline_contexts(timeline)

    checked_assets = 0
    checked_bytes = 0
    if verify_hashes:
        checked_assets, checked_bytes = _verify_assets(
            documents,
            cpl,
            resolver,
            contexts,
            diagnostics,
            chunk_size,
        )
    else:
        _check_asset_presence(documents, cpl, resolver, contexts, diagnostics)

    edit_rates = sorted(
        {
            str(resource.edit_rate)
            for reel in cpl.reels
            for resource in reel.resources
            if resource.edit_rate is not None
        }
    )
    _enrich_diagnostics(diagnostics, resolver, contexts)
    return InspectionReport(
        root=documents.root,
        cpl=cpl,
        diagnostics=diagnostics,
        timeline=timeline,
        total_duration=total_duration,
        checked_assets=checked_assets,
        checked_bytes=checked_bytes,
        edit_rates=edit_rates,
    )


def _enrich_diagnostics(
    diagnostics: list[Diagnostic],
    resolver: AssetResolver,
    contexts: dict[str, list[TimelineSegment]],
) -> None:
    for diagnostic in diagnostics:
        if not diagnostic.asset_id:
            continue
        segments = contexts.get(diagnostic.asset_id, [])
        if segments:
            first = segments[0]
            if diagnostic.reel is None:
                diagnostic.reel = first.reel_id
            if diagnostic.screening_time is None:
                diagnostic.screening_time = format_time(first.start)
        if diagnostic.volume is None:
            diagnostic.volume = resolver.locate(diagnostic.asset_id).volume


def _timeline_contexts(
    timeline: list[TimelineSegment],
) -> dict[str, list[TimelineSegment]]:
    contexts: dict[str, list[TimelineSegment]] = defaultdict(list)
    for segment in timeline:
        contexts[segment.asset_id].append(segment)
    return dict(contexts)


def _context_details(
    asset_id: str, contexts: dict[str, list[TimelineSegment]]
) -> dict:
    segments = contexts.get(asset_id, [])
    return {
        "reels": [
            {
                "reel": segment.reel_id,
                "screening_start": format_time(segment.start),
                "screening_end": format_time(segment.end),
            }
            for segment in segments
        ]
    }


def _check_references(
    documents: PackageDocuments,
    cpl: CompositionPlaylist,
    resolver: AssetResolver,
    diagnostics: list[Diagnostic],
) -> None:
    asset_map = documents.asset_map
    if asset_map is None:
        return

    packing_ids = {
        asset_id
        for pkl in documents.packing_lists
        for asset_id in pkl.assets
    }
    map_packing_ids = {
        asset_id
        for asset_id, entry in asset_map.entries.items()
        if entry.packing_list
    }
    for pkl in documents.packing_lists:
        if pkl.pkl_id and pkl.pkl_id not in map_packing_ids:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=(
                        f"PKL {pkl.pkl_id} 未以 PackingList=true 登记在 ASSETMAP"
                    ),
                    asset_id=pkl.pkl_id,
                    asset_path=str(pkl.path),
                )
            )
    for pkl_id in sorted(map_packing_ids - {pkl.pkl_id for pkl in documents.packing_lists}):
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.REFERENCE_MISMATCH,
                message=f"ASSETMAP 声明的 PKL {pkl_id} 未找到",
                asset_id=pkl_id,
            )
        )

    for asset_id in sorted(packing_ids):
        if asset_id not in asset_map.entries:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=f"PKL 资产 {asset_id} 未登记在 ASSETMAP",
                    asset_id=asset_id,
                )
            )

    for pkl in documents.packing_lists:
        for asset_id, packed in pkl.assets.items():
            entry = asset_map.entries.get(asset_id)
            if (
                entry is None
                or len(entry.chunks) != 1
                or not packed.path
            ):
                continue
            packed_name = _basename(packed.path)
            chunk_name = _basename(entry.chunks[0].path)
            if packed_name and chunk_name and packed_name != chunk_name:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.REFERENCE_MISMATCH,
                        message=(
                            f"资产 {asset_id} 的 PKL OriginalFileName "
                            f"{packed_name!r} 与 ASSETMAP 路径 "
                            f"{chunk_name!r} 不一致"
                        ),
                        asset_id=asset_id,
                        asset_path=entry.path,
                    )
                )

    if cpl.cpl_id and cpl.cpl_id not in asset_map.entries:
        diagnostics.append(
            Diagnostic(
                code=DiagnosticCode.REFERENCE_MISMATCH,
                message=f"CPL {cpl.cpl_id} 未登记在 ASSETMAP",
                asset_id=cpl.cpl_id,
                asset_path=str(cpl.path),
            )
        )

    known_xml_ids = (
        packing_ids
        | map_packing_ids
        | {item.cpl_id for item in documents.cpls}
        | {item.pkl_id for item in documents.packing_lists}
    )
    for asset_id, entry in sorted(asset_map.entries.items()):
        if asset_id in known_xml_ids or entry.packing_list:
            continue
        if resolver.packing_asset(asset_id) is None:
            diagnostics.append(
                Diagnostic(
                    code=DiagnosticCode.REFERENCE_MISMATCH,
                    message=f"ASSETMAP 资产 {asset_id} 未被任何 PKL 声明",
                    asset_id=asset_id,
                    asset_path=entry.path,
                )
            )

    for reel in cpl.reels:
        for resource in reel.resources:
            mapped = resolver.map_entry(resource.asset_id)
            packed = resolver.packing_asset(resource.asset_id)
            if mapped is None and packed is None:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.REFERENCE_MISMATCH,
                        message=(
                            f"卷 {reel.reel_id} 的 {resource.kind.value} 引用 "
                            f"{resource.asset_id} 在 ASSETMAP/PKL 中均不存在"
                        ),
                        reel=reel.reel_id,
                        asset_id=resource.asset_id,
                    )
                )
            elif mapped is None:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.REFERENCE_MISMATCH,
                        message=(
                            f"卷 {reel.reel_id} 的 {resource.kind.value} 引用 "
                            f"{resource.asset_id} 在 ASSETMAP 中不存在"
                        ),
                        reel=reel.reel_id,
                        asset_id=resource.asset_id,
                    )
                )
            elif packed is None:
                diagnostics.append(
                    Diagnostic(
                        code=DiagnosticCode.REFERENCE_MISMATCH,
                        message=(
                            f"卷 {reel.reel_id} 的 {resource.kind.value} 引用 "
                            f"{resource.asset_id} 在 PKL 中不存在"
                        ),
                        reel=reel.reel_id,
                        asset_id=resource.asset_id,
                        asset_path=mapped[0].path,
                    )
                )


def _assets_to_check(
    documents: PackageDocuments,
    cpl: CompositionPlaylist,
    resolver: AssetResolver,
) -> dict[str, tuple[PackingAsset, bool]]:
    result: dict[str, tuple[PackingAsset, bool]] = {
        asset_id: (asset, False)
        for pkl in documents.packing_lists
        for asset_id, asset in pkl.assets.items()
    }
    referenced = {
        resource.asset_id for reel in cpl.reels for resource in reel.resources
    }
    for asset_id in referenced:
        packed = resolver.packing_asset(asset_id)
        if packed is not None and asset_id not in result:
            result[asset_id] = packed
    return result


def _basename(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _check_asset_presence(
    documents: PackageDocuments,
    cpl: CompositionPlaylist,
    resolver: AssetResolver,
    contexts: dict[str, list[TimelineSegment]],
    diagnostics: list[Diagnostic],
) -> None:
    for asset_id in sorted(_assets_to_check(documents, cpl, resolver)):
        location = resolver.locate(asset_id)
        _report_missing_chunks(asset_id, location, contexts, diagnostics)


def _verify_assets(
    documents: PackageDocuments,
    cpl: CompositionPlaylist,
    resolver: AssetResolver,
    contexts: dict[str, list[TimelineSegment]],
    diagnostics: list[Diagnostic],
    chunk_size: int,
) -> tuple[int, int]:
    checked_assets = 0
    checked_bytes = 0
    for asset_id, (asset, inherited) in sorted(
        _assets_to_check(documents, cpl, resolver).items()
    ):
        location = resolver.locate(asset_id)
        if not _report_missing_chunks(asset_id, location, contexts, diagnostics):
            continue
        checked_assets += 1
        algorithm = asset.hash_algorithm
        if algorithm is None and asset.hash_value:
            algorithm = "SHA1"
        canonical = _HASH_ALGORITHMS.get((algorithm or "").upper())
        if not asset.hash_value or canonical is None:
            diagnostics.append(
                _asset_diagnostic(
                    DiagnosticCode.CHECKSUM_FAILURE,
                    (
                        f"资产 {asset_id} 的清单校验方法不可用: "
                        f"{algorithm or '(未声明)'}"
                    ),
                    asset_id,
                    location,
                    contexts,
                    {
                        "inherited": inherited,
                        "hash_algorithm": algorithm,
                    },
                )
            )
            continue

        actual_size, actual_hash, error = _stream_hash(
            location.resolved_chunks, chunk_size, canonical
        )
        if error is not None:
            diagnostics.append(
                _asset_diagnostic(
                    DiagnosticCode.MISSING_REEL,
                    f"资产 {asset_id} 无法从卷读取: {error}",
                    asset_id,
                    location,
                    contexts,
                    {"inherited": inherited},
                )
            )
            continue
        checked_bytes += actual_size

        expected_hash = asset.hash_value.lower()
        expected_size = asset.size
        size_ok = expected_size is None or expected_size == actual_size
        hash_ok = actual_hash == expected_hash
        if size_ok and hash_ok:
            continue
        reasons = []
        if not size_ok:
            reasons.append(f"大小 {actual_size} != {expected_size}")
        if not hash_ok:
            reasons.append(f"{canonical.upper()} {actual_hash} != {expected_hash}")
        diagnostics.append(
            _asset_diagnostic(
                DiagnosticCode.CHECKSUM_FAILURE,
                f"资产 {asset_id} 校验失败: " + "; ".join(reasons),
                asset_id,
                location,
                contexts,
                {
                    "inherited": inherited,
                    "hash_algorithm": canonical.upper(),
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                },
            )
        )
    return checked_assets, checked_bytes


def _report_missing_chunks(
    asset_id: str,
    location: AssetLocation,
    contexts: dict[str, list[TimelineSegment]],
    diagnostics: list[Diagnostic],
) -> bool:
    if location.map_entry is None:
        return False
    if not location.missing_chunks:
        return True
    missing_volumes = [
        volume_label(chunk) for chunk in location.missing_chunks
    ]
    paths = [chunk.path for chunk in location.missing_chunks]
    diagnostics.append(
        _asset_diagnostic(
            DiagnosticCode.MISSING_REEL,
            (
                f"资产 {asset_id} 缺卷 {', '.join(missing_volumes)}: "
                f"{', '.join(paths)}"
            ),
            asset_id,
            location,
            contexts,
            {
                "inherited": location.inherited,
                "missing_volumes": missing_volumes,
                "missing_chunks": paths,
            },
            volume=", ".join(missing_volumes),
        )
    )
    return False


def _stream_hash(
    chunks: list[Path], chunk_size: int, algorithm: str
) -> tuple[int, str, str | None]:
    digest = hashlib.new(algorithm)
    total = 0
    try:
        for path in chunks:
            with path.open("rb") as handle:
                while block := handle.read(chunk_size):
                    total += len(block)
                    digest.update(block)
    except OSError as exc:
        return total, digest.hexdigest(), str(exc)
    return total, digest.hexdigest(), None


def _asset_diagnostic(
    code: DiagnosticCode,
    message: str,
    asset_id: str,
    location: AssetLocation,
    contexts: dict[str, list[TimelineSegment]],
    details: dict,
    *,
    volume: str | None = None,
) -> Diagnostic:
    segments = contexts.get(asset_id, [])
    first = segments[0] if segments else None
    merged_details = _context_details(asset_id, contexts)
    merged_details.update(details)
    return Diagnostic(
        code=code,
        message=message,
        reel=first.reel_id if first else None,
        asset_id=asset_id,
        asset_path=location.path,
        volume=volume if volume is not None else location.volume,
        screening_time=format_time(first.start) if first else None,
        details=merged_details,
    )
