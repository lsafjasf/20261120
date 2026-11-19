"""Normative MPEG-1 Audio Layer II tables used by the inspector."""

BITRATES_KBPS = (
    None,
    32,
    48,
    56,
    64,
    80,
    96,
    112,
    128,
    160,
    192,
    224,
    256,
    320,
    384,
    None,
)

SAMPLE_RATES = (44100, 48000, 32000)

# Number of allocation bits per subband. The four MPEG-1 allocation tables
# collapse to three width sequences because tables 0 and 1 share table 1.
ALLOC_WIDTHS = {
    0: (4,) * 11 + (3,) * 12 + (2,) * 7,
    1: (4,) * 11 + (3,) * 12 + (2,) * 7,
    2: (4, 4) + (3,) * 10,
    3: (4, 4) + (3,) * 10,
    4: (4,) * 4 + (3,) * 8 + (2,) * 18,
}

SBLIMIT = {0: 27, 1: 30, 2: 8, 3: 12, 4: 30}

MODE_NAMES = ("stereo", "joint_stereo", "dual_channel", "mono")

SCALE_FACTOR_BITS = (18, 12, 6, 12)


def allocation_table_index(
    bitrate_kbps: int, channels: int, sample_rate: int, lsf: bool = False
) -> int:
    """Select the Layer II allocation table exactly as the ISO rules define."""
    if lsf:
        return 4
    channel_bitrate = bitrate_kbps // channels
    if (sample_rate == 48000 and channel_bitrate >= 56) or (
        56 <= channel_bitrate <= 80
    ):
        return 0
    if sample_rate != 48000 and channel_bitrate >= 96:
        return 1
    if sample_rate != 32000 and channel_bitrate <= 48:
        return 2
    return 3
