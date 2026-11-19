# -*- coding: utf-8 -*-
"""Three built-in demo scenarios."""

from __future__ import annotations

from .models import DescriptionClip, Interval, Timeline


def sample_contention() -> Timeline:
    """样例1: 两条解说争抢同一空档, 重要度高者胜出."""
    return Timeline(
        duration=30.0,
        dialogue=[Interval(0, 10, "对白A"), Interval(14, 30, "对白B")],
        sfx_zones=[],
        clips=[
            DescriptionClip("AD1", 9, 15, 5.0, 3.5, importance=5,
                            text="主角拔枪指向门口"),
            DescriptionClip("AD2", 9, 15, 5.0, 3.2, importance=3,
                            text="窗外天色骤变"),
        ],
    )


def sample_short_fits() -> Timeline:
    """样例2: 长版放不下, 短版能放下; 含锁定的人工安排."""
    return Timeline(
        duration=40.0,
        dialogue=[Interval(0, 10, "开场白"), Interval(18, 40, "对白")],
        sfx_zones=[Interval(12, 14, "爆炸音效")],
        clips=[
            DescriptionClip("AD1", 10, 18, 2.0, 2.0, importance=5,
                            text="(人工)字幕: 三天后",
                            locked_start=10.0, locked_version="short"),
            DescriptionClip("AD2", 10, 18, 8.0, 3.5, importance=4,
                            text="房间一片狼藉, 桌上留着一封信"),
        ],
    )


def sample_no_gap() -> Timeline:
    """样例3: 完全无可用空档, 全部落选并给出原因."""
    return Timeline(
        duration=20.0,
        dialogue=[Interval(0, 9, "密集对白"), Interval(11, 20, "密集对白")],
        sfx_zones=[Interval(9, 11, "音乐高潮")],
        clips=[
            DescriptionClip("AD1", 4, 12, 6.0, 4.0, importance=5,
                            text="反派从暗处现身"),
            DescriptionClip("AD2", 2, 4, 3.0, 2.5, importance=2,
                            text="墙上挂钟指向午夜"),
        ],
    )


SAMPLES = {
    "contention": ("两条解说争抢同一空档", sample_contention),
    "short_fits": ("长版放不下, 短版补位", sample_short_fits),
    "no_gap": ("完全无可用空档", sample_no_gap),
}
