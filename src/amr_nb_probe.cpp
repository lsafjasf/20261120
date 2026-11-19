// OpenCore AMR-NB encode/decode probe with decoder-parameter tracing.

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#define AMRNB_WRAPPER_INTERNAL
#include "interf_dec.h"
#include "sp_dec.h"
#include "int_lpc.h"
#include "interf_enc.h"

static const int kFrameSamples = 160;
static const int kLspOrder = 10;

static bool g_trace = false;
static short g_excitation[kFrameSamples];
static int g_excitation_count = 0;
static short g_lsp_old[kLspOrder];
static short g_lsp_mid[kLspOrder];
static short g_lsp_new[kLspOrder];
static bool g_have_lsp_mid = false;
static bool g_have_lsp = false;

extern "C" void __real_Syn_filt(
    short a[], short x[], short y[], short lg, short mem[], short update);

extern "C" void __wrap_Syn_filt(
    short a[], short x[], short y[], short lg, short mem[], short update) {
    if (g_trace && lg == 40 && g_excitation_count + lg <= kFrameSamples) {
        std::memcpy(
            g_excitation + g_excitation_count, x,
            static_cast<size_t>(lg) * sizeof(short));
        g_excitation_count += lg;
    }
    __real_Syn_filt(a, x, y, lg, mem, update);
}

extern "C" void __real_Int_lpc_1to3(
    short lsp_old[], short lsp_new[], short lpc[], Flag *overflow);

extern "C" void __wrap_Int_lpc_1to3(
    short lsp_old[], short lsp_new[], short lpc[], Flag *overflow) {
    if (g_trace) {
        std::memcpy(g_lsp_old, lsp_old, sizeof(g_lsp_old));
        std::memcpy(g_lsp_new, lsp_new, sizeof(g_lsp_new));
        g_have_lsp_mid = false;
        g_have_lsp = true;
    }
    __real_Int_lpc_1to3(lsp_old, lsp_new, lpc, overflow);
}

extern "C" void __real_Int_lpc_1and3(
    short lsp_old[], short lsp_mid[], short lsp_new[], short lpc[],
    Flag *overflow);

extern "C" void __wrap_Int_lpc_1and3(
    short lsp_old[], short lsp_mid[], short lsp_new[], short lpc[],
    Flag *overflow) {
    if (g_trace) {
        std::memcpy(g_lsp_old, lsp_old, sizeof(g_lsp_old));
        std::memcpy(g_lsp_mid, lsp_mid, sizeof(g_lsp_mid));
        std::memcpy(g_lsp_new, lsp_new, sizeof(g_lsp_new));
        g_have_lsp_mid = true;
        g_have_lsp = true;
    }
    __real_Int_lpc_1and3(lsp_old, lsp_mid, lsp_new, lpc, overflow);
}

static void reset_trace() {
    g_excitation_count = 0;
    std::memset(g_excitation, 0, sizeof(g_excitation));
    std::memset(g_lsp_old, 0, sizeof(g_lsp_old));
    std::memset(g_lsp_mid, 0, sizeof(g_lsp_mid));
    std::memset(g_lsp_new, 0, sizeof(g_lsp_new));
    g_have_lsp_mid = false;
    g_have_lsp = false;
}

static int payload_size(int ft) {
    static const int sizes[16] = {
        12, 13, 15, 17, 19, 20, 26, 31,
        5, 5, 5, 5, 0, 0, 0, 0,
    };
    return sizes[ft & 0x0f];
}

static int run_encode(const char *raw_path, const char *amr_path,
                      const char *schedule_path) {
    FILE *raw = std::fopen(raw_path, "rb");
    FILE *schedule = std::fopen(schedule_path, "r");
    FILE *output = std::fopen(amr_path, "wb");
    if (!raw || !schedule || !output) {
        std::perror("encode input/output");
        if (raw) std::fclose(raw);
        if (schedule) std::fclose(schedule);
        if (output) std::fclose(output);
        return 1;
    }

    std::fwrite("#!AMR\n", 1, 6, output);
    void *encoder = Encoder_Interface_init(0);
    int mode_value = 0;
    int frame_count = 0;
    short pcm[kFrameSamples];
    unsigned char encoded[64];
    while (std::fscanf(schedule, "%d", &mode_value) == 1) {
        if (mode_value < 0 || mode_value > 7) {
            std::fprintf(stderr, "invalid FT/mode value: %d\n", mode_value);
            break;
        }
        if (std::fread(pcm, sizeof(short), kFrameSamples, raw) != kFrameSamples) {
            std::fprintf(stderr, "raw input ended early at frame %d\n", frame_count);
            break;
        }
        int size = Encoder_Interface_Encode(
            encoder, static_cast<enum Mode>(mode_value), pcm, encoded, 1);
        if (size <= 0) {
            std::fprintf(stderr, "encoder failed at frame %d\n", frame_count);
            break;
        }
        std::fwrite(encoded, 1, static_cast<size_t>(size), output);
        frame_count++;
    }

    Encoder_Interface_exit(encoder);
    std::fclose(raw);
    std::fclose(schedule);
    std::fclose(output);
    std::printf("{\"frames\":%d}\n", frame_count);
    return frame_count > 0 ? 0 : 1;
}

static int run_decode(const char *amr_path, const char *raw_path,
                      const char *params_path, bool reset_on_switch) {
    FILE *input = std::fopen(amr_path, "rb");
    FILE *output = std::fopen(raw_path, "wb");
    FILE *params = std::fopen(params_path, "w");
    if (!input || !output || !params) {
        std::perror("decode input/output");
        if (input) std::fclose(input);
        if (output) std::fclose(output);
        if (params) std::fclose(params);
        return 1;
    }

    char magic[6];
    if (std::fread(magic, 1, 6, input) != 6 ||
        std::memcmp(magic, "#!AMR\n", 6) != 0) {
        std::fprintf(stderr, "bad AMR magic\n");
        std::fclose(input);
        std::fclose(output);
        std::fclose(params);
        return 1;
    }

    std::fputs(
        "frame,ft,quality,bfi,switched,conceal_state,exc_count,lsp_old_raw,"
        "lsp_mid_raw,lsp_new_raw,exc_raw\n",
        params);

    void *decoder = Decoder_Interface_init();
    int previous_ft = -1;
    int frame_index = 0;
    int resets = 0;
    while (true) {
        int toc = std::fgetc(input);
        if (toc == EOF) {
            break;
        }
        int ft = (toc >> 3) & 0x0f;
        int quality = (toc >> 2) & 0x01;
        int size = payload_size(ft);
        if (size < 0) {
            std::fprintf(stderr, "unsupported FT %d at frame %d\n", ft, frame_index);
            break;
        }

        unsigned char frame[64];
        frame[0] = static_cast<unsigned char>(toc);
        if (size > 0 &&
            std::fread(frame + 1, 1, static_cast<size_t>(size), input) !=
                static_cast<size_t>(size)) {
            std::fprintf(stderr, "truncated frame %d\n", frame_index);
            break;
        }

        int switched = previous_ft >= 0 && previous_ft != ft ? 1 : 0;
        if (reset_on_switch && switched) {
            Decoder_Interface_exit(decoder);
            decoder = Decoder_Interface_init();
            resets++;
        }

        short pcm[kFrameSamples];
        int bfi = quality == 0 ? 1 : 0;
        reset_trace();
        g_trace = true;
        Decoder_Interface_Decode(decoder, frame, pcm, bfi);
        g_trace = false;

        Speech_Decode_FrameState *state =
            reinterpret_cast<Speech_Decode_FrameState *>(decoder);
        if (!g_have_lsp) {
            std::memcpy(
                g_lsp_new, state->decoder_amrState.lsp_old,
                sizeof(g_lsp_new));
        }
        std::fwrite(pcm, sizeof(short), kFrameSamples, output);
        std::fprintf(
            params, "%d,%d,%d,%d,%d,%d,%d,", frame_index, ft, quality, bfi,
            switched, state->decoder_amrState.state, g_excitation_count);
        std::fputc('"', params);
        for (int i = 0; i < kLspOrder; i++) {
            std::fprintf(params, "%d,", g_lsp_old[i]);
        }
        std::fputs("\",\"", params);
        for (int i = 0; i < kLspOrder; i++) {
            std::fprintf(params, "%d,", g_lsp_mid[i]);
        }
        std::fputs("\",\"", params);
        for (int i = 0; i < kLspOrder; i++) {
            std::fprintf(params, "%d,", g_lsp_new[i]);
        }
        std::fputs("\",\"", params);
        for (int i = 0; i < kFrameSamples; i++) {
            std::fprintf(params, "%d%s", g_excitation[i],
                         i + 1 == kFrameSamples ? "" : ",");
        }
        std::fputs("\"\n", params);
        previous_ft = ft;
        frame_index++;
    }

    Decoder_Interface_exit(decoder);
    std::fclose(input);
    std::fclose(output);
    std::fclose(params);
    std::printf(
        "{\"frames\":%d,\"resets\":%d,\"lsp_subframes\":%d}\n",
        frame_index, resets, g_have_lsp_mid ? 4 : 4);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(
            stderr,
            "usage: %s encode <raw> <amr> <schedule> | "
            "decode <amr> <raw> <params.csv> [--reset-on-mode-change]\n",
            argv[0]);
        return 2;
    }
    if (std::strcmp(argv[1], "encode") == 0 && argc == 5) {
        return run_encode(argv[2], argv[3], argv[4]);
    }
    if (std::strcmp(argv[1], "decode") == 0 &&
        (argc == 5 || argc == 6)) {
        bool reset = argc == 6 &&
            std::strcmp(argv[5], "--reset-on-mode-change") == 0;
        return run_decode(argv[2], argv[3], argv[4], reset);
    }
    std::fprintf(stderr, "invalid arguments\n");
    return 2;
}
