#ifdef HAVE_CONFIG_H
#include "config.h"
#endif

#include "speex/speex.h"
#include "speex/speex_bits.h"
#include "modes.h"
#include "nb_celp.h"
#include "sb_celp.h"
#include "cb_search.h"
#include "filters.h"
#include "lsp.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FRAME_SECONDS 0.020f
#define MAX_FRAMES 128
#define MAX_PACKET_BYTES 2048
#define MAX_TRACE 128
#define MAX_SUBFRAMES 4
#define MAX_SUBVECTORS 16

typedef struct {
   int valid;
   int bit_start;
   int bit_end;
   int bits;
   int nb_subvect;
   int subvect_size;
   int shape_bits;
   int have_sign;
   int gain_bits;
   int gain_index;
   int prior_value;
   int prior_bits;
   int sign[MAX_SUBVECTORS];
   int index[MAX_SUBVECTORS];
} CodebookTrace;

typedef struct {
   int valid;
   int len;
   int order;
   float lpc[NB_ORDER];
} LpcTrace;

typedef struct {
   int valid;
   int len;
   float x[80];
} SignalTrace;

typedef struct {
   int valid;
   int len;
   int order;
   float x[80];
   float lpc[NB_ORDER];
} IirTrace;

typedef struct {
   int submode;
   int lpc_order;
   float lpc[MAX_SUBFRAMES][NB_ORDER];
   float pi_gain[MAX_SUBFRAMES];
   float residual_rms[MAX_SUBFRAMES];
   float residual_mse[MAX_SUBFRAMES];
   SignalTrace excitation[MAX_SUBFRAMES];
   CodebookTrace codebook[MAX_SUBFRAMES][2];
} LayerTrace;

typedef struct {
   int mode;
   int sample_rate;
   int frame_size;
   int nframes;
   int packet_bits[MAX_FRAMES];
   int inferred_mode[MAX_FRAMES];
   int submode[MAX_FRAMES];
   int low_submode[MAX_FRAMES];
   LayerTrace layer[MAX_FRAMES][3];
   float *input;
   float *output;
} ModeRun;

static int g_capture;
static CodebookTrace g_codebook[MAX_TRACE];
static int g_ncodebook;
static LpcTrace g_lpc[MAX_TRACE];
static int g_nlpc;
static SignalTrace g_signal[MAX_TRACE];
static int g_nsignal;
static IirTrace g_iir[MAX_TRACE];
static int g_niir;

static int bit_position(const SpeexBits *bits)
{
   return bits->charPtr * 8 + bits->bitPtr;
}

static int read_bit(const char *buf, int pos)
{
   return (buf[pos >> 3] >> (7 - (pos & 7))) & 1;
}

static unsigned int read_bits(const char *buf, int pos, int count)
{
   unsigned int value = 0;
   int i;
   for (i = 0; i < count; ++i)
      value = (value << 1) | (unsigned int)read_bit(buf, pos + i);
   return value;
}

void __real_lsp_to_lpc(const spx_lsp_t *freq, spx_coef_t *ak, int lpcrdr,
                       char *stack);
void __wrap_lsp_to_lpc(const spx_lsp_t *freq, spx_coef_t *ak, int lpcrdr,
                       char *stack)
{
   int i;
   __real_lsp_to_lpc(freq, ak, lpcrdr, stack);
   if (g_capture && g_nlpc < MAX_TRACE) {
      LpcTrace *trace = &g_lpc[g_nlpc++];
      trace->valid = 1;
      trace->order = lpcrdr;
      for (i = 0; i < lpcrdr && i < NB_ORDER; ++i)
         trace->lpc[i] = ak[i];
   }
}

void __real_signal_mul(const spx_sig_t *x, spx_sig_t *y,
                       spx_word32_t scale, int len);
void __wrap_signal_mul(const spx_sig_t *x, spx_sig_t *y,
                       spx_word32_t scale, int len)
{
   int i;
   __real_signal_mul(x, y, scale, len);
   if (g_capture && g_nsignal < MAX_TRACE && len <= 80) {
      SignalTrace *trace = &g_signal[g_nsignal++];
      trace->valid = 1;
      trace->len = len;
      for (i = 0; i < len; ++i)
         trace->x[i] = y[i];
   }
}

void __real_iir_mem16(const spx_word16_t *x, const spx_coef_t *den,
                      spx_word16_t *y, int n, int ord, spx_mem_t *mem,
                      char *stack);
void __wrap_iir_mem16(const spx_word16_t *x, const spx_coef_t *den,
                      spx_word16_t *y, int n, int ord, spx_mem_t *mem,
                      char *stack)
{
   int i;
   __real_iir_mem16(x, den, y, n, ord, mem, stack);
   if (g_capture && g_niir < MAX_TRACE && n <= 80) {
      IirTrace *trace = &g_iir[g_niir++];
      trace->valid = 1;
      trace->len = n;
      trace->order = ord;
      for (i = 0; i < n; ++i)
         trace->x[i] = x[i];
      for (i = 0; i < ord && i < NB_ORDER; ++i)
         trace->lpc[i] = den[i];
   }
}

void __real_split_cb_shape_sign_unquant(spx_sig_t *exc, const void *par,
                                        int nsf, SpeexBits *bits, char *stack,
                                        spx_uint32_t *seed);
void __wrap_split_cb_shape_sign_unquant(spx_sig_t *exc, const void *par,
                                        int nsf, SpeexBits *bits, char *stack,
                                        spx_uint32_t *seed)
{
   const split_cb_params *params = (const split_cb_params *)par;
   int start = bit_position(bits);
   int end;
   int i;

   __real_split_cb_shape_sign_unquant(exc, par, nsf, bits, stack, seed);
   end = bit_position(bits);
   if (!g_capture || g_ncodebook >= MAX_TRACE)
      return;

   {
      CodebookTrace *trace = &g_codebook[g_ncodebook++];
      int pos = start;
      trace->valid = 1;
      trace->bit_start = start;
      trace->bit_end = end;
      trace->bits = end - start;
      trace->nb_subvect = params->nb_subvect;
      trace->subvect_size = params->subvect_size;
      trace->shape_bits = params->shape_bits;
      trace->have_sign = params->have_sign;
      trace->prior_bits = start < 12 ? start : 12;
      trace->prior_value = trace->prior_bits > 0
         ? (int)read_bits(bits->chars, start - trace->prior_bits,
                          trace->prior_bits) : 0;
      for (i = 0; i < params->nb_subvect && i < MAX_SUBVECTORS; ++i) {
         trace->sign[i] = params->have_sign ? read_bit(bits->chars, pos++) : 0;
         trace->index[i] = (int)read_bits(bits->chars, pos,
                                          params->shape_bits);
         pos += params->shape_bits;
      }
   }
}

static void reset_capture(void)
{
   g_ncodebook = 0;
   g_nlpc = 0;
   g_nsignal = 0;
   g_niir = 0;
}

static void begin_capture(void)
{
   reset_capture();
   g_capture = 1;
}

static void end_capture(void)
{
   g_capture = 0;
}

static int classify_packet(const unsigned char *packet, int nbits)
{
   const char *buf = (const char *)packet;
   int pos = 0;
   const SpeexNBMode *nb_mode = (const SpeexNBMode *)speex_nb_mode.mode;
   const SpeexSBMode *wb_mode = (const SpeexSBMode *)speex_wb_mode.mode;
   const SpeexSBMode *uwb_mode = (const SpeexSBMode *)speex_uwb_mode.mode;
   int submode;
   int layer_start;

   if (nbits < 5 || read_bit(buf, pos++))
      return -1;
   submode = (int)read_bits(buf, pos, 4);
   pos += 4;
   if (submode >= NB_SUBMODES || !nb_mode->submodes[submode])
      return -1;
   pos = nb_mode->submodes[submode]->bits_per_frame;
   if (pos == nbits)
      return SPEEX_MODEID_NB;
   layer_start = pos;
   if (pos + 4 > nbits || !read_bit(buf, pos++))
      return -1;
   submode = (int)read_bits(buf, pos, 3);
   pos += 3;
   if (submode >= SB_SUBMODES || !wb_mode->submodes[submode])
      return -1;
   pos = layer_start + wb_mode->submodes[submode]->bits_per_frame;
   if (pos == nbits)
      return SPEEX_MODEID_WB;
   layer_start = pos;
   if (pos + 4 > nbits || !read_bit(buf, pos++))
      return -1;
   submode = (int)read_bits(buf, pos, 3);
   pos += 3;
   if (submode >= SB_SUBMODES || !uwb_mode->submodes[submode])
      return -1;
   pos = layer_start + uwb_mode->submodes[submode]->bits_per_frame;
   return pos == nbits ? SPEEX_MODEID_UWB : -1;
}

static float generate_sample(int mode, float time)
{
   static const float low_formants[3] = {520.0f, 1480.0f, 2520.0f};
   float envelope = 0.08f + 0.92f * powf(fmaxf(0.0f, sinf(2.0f * 3.14159265f * 1.7f * time)), 0.65f);
   float signal = 0.0f;
   int i;

   if (time < 0.06f)
      envelope *= time / 0.06f;
   for (i = 0; i < 3; ++i) {
      float amplitude = 0.24f / (1.0f + 0.35f * i);
      signal += amplitude * sinf(2.0f * 3.14159265f * low_formants[i] * time + 0.17f * i);
   }
   signal += 0.12f * sinf(2.0f * 3.14159265f * 132.0f * time);
   signal += 0.05f * sinf(2.0f * 3.14159265f * 264.0f * time + 0.3f);

   if (mode >= SPEEX_MODEID_WB) {
      signal += 0.07f * sinf(2.0f * 3.14159265f * 5480.0f * time);
      signal += 0.04f * sinf(2.0f * 3.14159265f * 6910.0f * time + 0.6f);
   }
   if (mode >= SPEEX_MODEID_UWB) {
      signal += 0.05f * sinf(2.0f * 3.14159265f * 9300.0f * time + 0.9f);
      signal += 0.025f * sinf(2.0f * 3.14159265f * 13200.0f * time + 0.25f);
   }
   return 0.72f * envelope * signal;
}

static int mode_frame_size(int mode)
{
   return mode == SPEEX_MODEID_NB ? 160 :
          mode == SPEEX_MODEID_WB ? 320 : 640;
}

static int mode_rate(int mode)
{
   return mode == SPEEX_MODEID_NB ? 8000 :
          mode == SPEEX_MODEID_WB ? 16000 : 32000;
}

static void fill_mode_input(ModeRun *run)
{
   int i;
   run->sample_rate = mode_rate(run->mode);
   run->frame_size = mode_frame_size(run->mode);
   run->input = (float *)calloc((size_t)run->nframes * run->frame_size,
                                sizeof(float));
   run->output = (float *)calloc((size_t)run->nframes * run->frame_size,
                                 sizeof(float));
   if (!run->input || !run->output) {
      fprintf(stderr, "allocation failure\n");
      exit(1);
   }
   for (i = 0; i < run->nframes * run->frame_size; ++i) {
      float time = (float)i / (float)run->sample_rate;
      run->input[i] = 12000.0f * generate_sample(run->mode, time);
   }
}

static void map_layers(ModeRun *run, int frame, int mode, void *decoder)
{
   DecState *nb = NULL;
   SBDecState *wb = NULL;
   SBDecState *uwb = NULL;
   int layer_count;
   int layer;
   int trace_pos = 0;
   int cb_pos = 0;
   int iir_pos = 0;

   if (mode == SPEEX_MODEID_NB) {
      nb = (DecState *)decoder;
      layer_count = 1;
   } else if (mode == SPEEX_MODEID_WB) {
      wb = (SBDecState *)decoder;
      nb = (DecState *)wb->st_low;
      layer_count = 2;
   } else {
      uwb = (SBDecState *)decoder;
      wb = (SBDecState *)uwb->st_low;
      nb = (DecState *)wb->st_low;
      layer_count = 3;
   }

   for (layer = 0; layer < layer_count; ++layer) {
      LayerTrace *lt = &run->layer[frame][layer];
      SBDecState *sbd = NULL;
      int submode;
      int double_codebook;
      int gain_bits;
      int sub;

      if (layer == 0) {
         submode = nb->submodeID;
         run->low_submode[frame] = submode;
      } else if (layer == 1) {
         sbd = wb;
         submode = wb->submodeID;
      } else {
         sbd = uwb;
         submode = uwb->submodeID;
      }
      lt->submode = submode;
      lt->lpc_order = layer == 0 ? NB_ORDER : sbd->lpcSize;
      if (layer == 0) {
         double_codebook = nb->submodes[submode]->double_codebook;
         gain_bits = nb->submodes[submode]->have_subframe_gain;
      } else {
         double_codebook = sbd->submodes[submode]->double_codebook;
         gain_bits = 4;
      }

      for (sub = 0; sub < MAX_SUBFRAMES; ++sub) {
         int pass;
         if (trace_pos < g_nlpc) {
            int j;
            for (j = 0; j < NB_ORDER; ++j)
               lt->lpc[sub][j] = g_lpc[trace_pos].lpc[j];
            ++trace_pos;
         }
         if (layer == 0) {
            lt->pi_gain[sub] = nb->pi_gain[sub];
            lt->residual_rms[sub] = compute_rms16(nb->exc + sub * 40, 40);
         } else {
            lt->pi_gain[sub] = sbd->pi_gain[sub];
            lt->residual_rms[sub] = sbd->exc_rms[sub];
         }
         lt->residual_mse[sub] = lt->residual_rms[sub] * lt->residual_rms[sub];
         if (iir_pos < g_niir) {
            int j;
            lt->excitation[sub].valid = 1;
            lt->excitation[sub].len = g_iir[iir_pos].len;
            for (j = 0; j < g_iir[iir_pos].len; ++j)
               lt->excitation[sub].x[j] = g_iir[iir_pos].x[j];
            ++iir_pos;
         }
         for (pass = 0; pass <= double_codebook; ++pass) {
            if (cb_pos < g_ncodebook) {
               CodebookTrace *cb = &g_codebook[cb_pos++];
               if (pass == 0) {
                  int gain_start = cb->bit_start - gain_bits;
                  lt->codebook[sub][pass] = *cb;
                  if (gain_bits > 0 && gain_start >= 0) {
                     int mask = (1 << gain_bits) - 1;
                     lt->codebook[sub][pass].gain_bits = gain_bits;
                     lt->codebook[sub][pass].gain_index =
                        (cb->prior_value >> (cb->prior_bits - gain_bits)) & mask;
                  }
               } else {
                  lt->codebook[sub][pass] = *cb;
                  lt->codebook[sub][pass].gain_bits = 0;
                  lt->codebook[sub][pass].gain_index = -1;
               }
            }
         }
      }
   }
}

static int encode_decode_mode(ModeRun *run)
{
   void *enc;
   void *dec;
   SpeexBits bits;
   int quality = 8;
   int vbr = 0;
   int vad = 0;
   int dtx = 0;
   int highpass = 0;
   int enhance = 0;
   int frame;

   enc = speex_encoder_init(speex_lib_get_mode(run->mode));
   dec = speex_decoder_init(speex_lib_get_mode(run->mode));
   if (!enc || !dec)
      return -1;
   speex_encoder_ctl(enc, SPEEX_SET_QUALITY, &quality);
   speex_encoder_ctl(enc, SPEEX_SET_VBR, &vbr);
   speex_encoder_ctl(enc, SPEEX_SET_VAD, &vad);
   speex_encoder_ctl(enc, SPEEX_SET_DTX, &dtx);
   speex_encoder_ctl(enc, SPEEX_SET_HIGHPASS, &highpass);
   speex_decoder_ctl(dec, SPEEX_SET_HIGHPASS, &highpass);
   speex_decoder_ctl(dec, SPEEX_SET_ENH, &enhance);
   speex_bits_init(&bits);

   for (frame = 0; frame < run->nframes; ++frame) {
      unsigned char packet[MAX_PACKET_BYTES];
      spx_int16_t frame_in[640];
      spx_int16_t frame_out[640];
      float *in = run->input + (size_t)frame * run->frame_size;
      float *out = run->output + (size_t)frame * run->frame_size;
      int i;
      for (i = 0; i < run->frame_size; ++i)
         frame_in[i] = (spx_int16_t)floor(0.5 + in[i]);
      speex_bits_reset(&bits);
      speex_encode_int(enc, frame_in, &bits);
      run->packet_bits[frame] = bits.nbBits;
      speex_bits_rewind(&bits);
      begin_capture();
      if (speex_decode_int(dec, &bits, frame_out) != 0)
         return -2;
      end_capture();
      for (i = 0; i < run->frame_size; ++i)
         out[i] = frame_out[i];
      speex_bits_write(&bits, (char *)packet, MAX_PACKET_BYTES);
      run->inferred_mode[frame] = classify_packet(packet, bits.nbBits);
      if (run->inferred_mode[frame] != run->mode)
         fprintf(stderr, "mode inference mismatch frame=%d expected=%d got=%d\n",
                 frame, run->mode, run->inferred_mode[frame]);
      {
         DecState *nb = NULL;
         if (run->mode == SPEEX_MODEID_NB)
            nb = (DecState *)dec;
         else if (run->mode == SPEEX_MODEID_WB)
            nb = (DecState *)((SBDecState *)dec)->st_low;
         else
            nb = (DecState *)((SBDecState *)((SBDecState *)dec)->st_low)->st_low;
         run->submode[frame] = run->mode == SPEEX_MODEID_NB ? nb->submodeID :
            run->mode == SPEEX_MODEID_WB ? ((SBDecState *)dec)->submodeID :
            ((SBDecState *)dec)->submodeID;
      }
      map_layers(run, frame, run->mode, dec);
   }

   speex_bits_destroy(&bits);
   speex_decoder_destroy(dec);
   speex_encoder_destroy(enc);
   return 0;
}

static void frame_rms(const float *signal, int frame_size, int nframes,
                      float *rms)
{
   int frame;
   for (frame = 0; frame < nframes; ++frame) {
      double sum = 0.0;
      int i;
      for (i = 0; i < frame_size; ++i) {
         double value = signal[(size_t)frame * frame_size + i];
         sum += value * value;
      }
      rms[frame] = (float)sqrt(sum / frame_size);
   }
}

static void envelope_metrics(const ModeRun *run, float *correlation,
                             float *nrmse, int *lag_frames)
{
   float *in_rms = (float *)calloc((size_t)run->nframes, sizeof(float));
   float *out_rms = (float *)calloc((size_t)run->nframes, sizeof(float));
   int lag;
   int best_lag = 0;
   double best_corr = -2.0;

   frame_rms(run->input, run->frame_size, run->nframes, in_rms);
   frame_rms(run->output, run->frame_size, run->nframes, out_rms);
   for (lag = -3; lag <= 3; ++lag) {
      double sx = 0.0, sy = 0.0, sxx = 0.0, syy = 0.0, sxy = 0.0;
      int count = 0;
      int frame;
      for (frame = 3; frame < run->nframes - 3; ++frame) {
         int j = frame - lag;
         double x, y;
         if (j < 3 || j >= run->nframes - 3)
            continue;
         x = in_rms[j];
         y = out_rms[frame];
         sx += x;
         sy += y;
         sxx += x * x;
         syy += y * y;
         sxy += x * y;
         ++count;
      }
      if (count > 0) {
         double denom = sqrt((count * sxx - sx * sx) * (count * syy - sy * sy));
         double corr = denom > 1e-20 ? (count * sxy - sx * sy) / denom : 0.0;
         if (corr > best_corr) {
            best_corr = corr;
            best_lag = lag;
         }
      }
   }
   {
      double err = 0.0, sig = 0.0;
      int frame;
      for (frame = 3; frame < run->nframes - 3; ++frame) {
         int j = frame - best_lag;
         double x = in_rms[j];
         double y = out_rms[frame];
         err += (y - x) * (y - x);
         sig += x * x;
      }
      *nrmse = (float)sqrt(err / fmax(sig, 1e-20));
   }
   *correlation = (float)best_corr;
   *lag_frames = best_lag;
   free(in_rms);
   free(out_rms);
}

static float residual_envelope_correlation(const ModeRun *run, int lag)
{
   float *in_rms = (float *)calloc((size_t)run->nframes, sizeof(float));
   float *residual_rms = (float *)calloc((size_t)run->nframes, sizeof(float));
   double sx = 0.0, sy = 0.0, sxx = 0.0, syy = 0.0, sxy = 0.0;
   int count = 0;
   int frame;

   frame_rms(run->input, run->frame_size, run->nframes, in_rms);
   for (frame = 0; frame < run->nframes; ++frame) {
      double sum = 0.0;
      int layer;
      int sub;
      int n = 0;
      for (layer = 0; layer <= run->mode; ++layer) {
         for (sub = 0; sub < MAX_SUBFRAMES; ++sub) {
            sum += run->layer[frame][layer].residual_mse[sub];
            ++n;
         }
      }
      residual_rms[frame] = (float)sqrt(sum / n);
   }
   for (frame = 3; frame < run->nframes - 3; ++frame) {
      int j = frame - lag;
      double x, y;
      if (j < 3 || j >= run->nframes - 3)
         continue;
      x = in_rms[j];
      y = residual_rms[frame];
      sx += x;
      sy += y;
      sxx += x * x;
      syy += y * y;
      sxy += x * y;
      ++count;
   }
   free(in_rms);
   free(residual_rms);
   if (count == 0)
      return 0.0f;
   {
      double denom = sqrt((count * sxx - sx * sx) * (count * syy - sy * sy));
      return (float)(denom > 1e-20 ? (count * sxy - sx * sy) / denom : 0.0);
   }
}

static int compare_float(const void *a, const void *b)
{
   float x = *(const float *)a;
   float y = *(const float *)b;
   return x < y ? -1 : x > y ? 1 : 0;
}

static void spectral_bands(const float *frame, int n, int rate,
                           float *band_db, float *high_ratio)
{
   static const float edges[17] = {
      80.0f, 150.0f, 250.0f, 380.0f, 550.0f, 750.0f, 1000.0f, 1300.0f,
      1700.0f, 2200.0f, 2800.0f, 3400.0f, 4100.0f, 5000.0f, 6000.0f,
      6900.0f, 7900.0f
   };
   float power[321];
   double total = 0.0;
   double high = 0.0;
   int k;
   int band;

   for (k = 0; k <= n / 2; ++k) {
      double real = 0.0;
      double imag = 0.0;
      int t;
      for (t = 0; t < n; ++t) {
         double window = 0.5 - 0.5 * cos(2.0 * 3.141592653589793 * t /
                                        (double)(n - 1));
         double angle = 2.0 * 3.141592653589793 * k * t / (double)n;
         real += window * frame[t] * cos(angle);
         imag -= window * frame[t] * sin(angle);
      }
      power[k] = (float)(real * real + imag * imag);
      total += power[k];
      if ((double)k * rate / n >= 4000.0)
         high += power[k];
   }
   *high_ratio = total > 0.0 ? (float)(high / total) : 0.0f;
   for (band = 0; band < 16; ++band) {
      double sum = 0.0;
      int first = (int)ceil(edges[band] * n / rate);
      int last = (int)floor(edges[band + 1] * n / rate);
      for (k = first; k <= last && k <= n / 2; ++k)
         sum += power[k];
      band_db[band] = (float)(10.0 * log10(sum + 1e-12));
   }
}

static float spectral_distance(const float *a, const float *b)
{
   float aa[16];
   float bb[16];
   double mean_a = 0.0, mean_b = 0.0;
   double sum = 0.0;
   int i;
   for (i = 0; i < 16; ++i) {
      mean_a += a[i];
      mean_b += b[i];
   }
   mean_a /= 16.0;
   mean_b /= 16.0;
   for (i = 0; i < 16; ++i) {
      aa[i] = (float)(a[i] - mean_a);
      bb[i] = (float)(b[i] - mean_b);
      sum += (aa[i] - bb[i]) * (aa[i] - bb[i]);
   }
   return (float)sqrt(sum / 16.0);
}

static int generate_stationary(float time)
{
   double value = 0.0;
   value += 0.24 * sin(2.0 * 3.141592653589793 * 170.0 * time);
   value += 0.20 * sin(2.0 * 3.141592653589793 * 430.0 * time + 0.2);
   value += 0.17 * sin(2.0 * 3.141592653589793 * 980.0 * time + 0.7);
   value += 0.14 * sin(2.0 * 3.141592653589793 * 1760.0 * time + 1.2);
   value += 0.11 * sin(2.0 * 3.141592653589793 * 2750.0 * time + 0.4);
   value += 0.09 * sin(2.0 * 3.141592653589793 * 3890.0 * time + 1.6);
   value += 0.07 * sin(2.0 * 3.141592653589793 * 5230.0 * time + 0.9);
   value += 0.05 * sin(2.0 * 3.141592653589793 * 6710.0 * time + 2.0);
   value += 0.03 * sin(2.0 * 3.141592653589793 * 7420.0 * time + 0.1);
   return (int)floor(0.5 + 12000.0 * value);
}

static int run_switch_test(int nframes, float *lsd, float *high_ratio,
                           int *packet_bits)
{
   const int frame_size = 320;
   const int rate = 16000;
   void *enc = speex_encoder_init(speex_lib_get_mode(SPEEX_MODEID_WB));
   void *dec = speex_decoder_init(speex_lib_get_mode(SPEEX_MODEID_WB));
   SpeexBits bits;
   float previous_bands[16] = {0};
   int have_previous = 0;
   int vbr = 0, vad = 0, dtx = 0, highpass = 0, enhance = 0;
   int frame;
   int result = 0;

   if (!enc || !dec)
      return -1;
   speex_encoder_ctl(enc, SPEEX_SET_VBR, &vbr);
   speex_encoder_ctl(enc, SPEEX_SET_VAD, &vad);
   speex_encoder_ctl(enc, SPEEX_SET_DTX, &dtx);
   speex_encoder_ctl(enc, SPEEX_SET_HIGHPASS, &highpass);
   speex_decoder_ctl(dec, SPEEX_SET_HIGHPASS, &highpass);
   speex_decoder_ctl(dec, SPEEX_SET_ENH, &enhance);
   speex_bits_init(&bits);

   for (frame = 0; frame < nframes; ++frame) {
      spx_int16_t input[320];
      spx_int16_t output[320];
      float frame_float[320];
      float bands[16];
      int quality = (frame >= 20 && frame < 40) ? 10 : 8;
      int i;
      for (i = 0; i < frame_size; ++i)
         input[i] = (spx_int16_t)generate_stationary(
            (double)(frame * frame_size + i) / rate);
      speex_encoder_ctl(enc, SPEEX_SET_QUALITY, &quality);
      speex_bits_reset(&bits);
      speex_encode_int(enc, input, &bits);
      packet_bits[frame] = bits.nbBits;
      speex_bits_rewind(&bits);
      if (speex_decode_int(dec, &bits, output) != 0) {
         result = -2;
         break;
      }
      for (i = 0; i < frame_size; ++i)
         frame_float[i] = output[i];
      spectral_bands(frame_float, frame_size, rate, bands, &high_ratio[frame]);
      lsd[frame] = have_previous
         ? spectral_distance(previous_bands, bands) : 0.0f;
      memcpy(previous_bands, bands, sizeof(previous_bands));
      have_previous = 1;
   }
   speex_bits_destroy(&bits);
   speex_decoder_destroy(dec);
   speex_encoder_destroy(enc);
   return result;
}

static void write_switch_summary(FILE *json, float *lsd, float *high_ratio,
                                 const int *packet_bits, int nframes)
{
   float baseline[64];
   int nbase = 0;
   int frame;
   float median;
   float boundary[2];
   float ratio_around[2][2];
   int no_cliff;

   for (frame = 1; frame < nframes; ++frame) {
      if (frame == 20 || frame == 40)
         continue;
      baseline[nbase++] = lsd[frame];
   }
   qsort(baseline, (size_t)nbase, sizeof(float), compare_float);
   median = nbase ? baseline[nbase / 2] : 0.0f;
   boundary[0] = lsd[20];
   boundary[1] = lsd[40];
   ratio_around[0][0] = high_ratio[19];
   ratio_around[0][1] = high_ratio[20];
   ratio_around[1][0] = high_ratio[39];
   ratio_around[1][1] = high_ratio[40];
   no_cliff = boundary[0] <= 2.0f * median + 3.0f &&
              boundary[1] <= 2.0f * median + 3.0f;

   fprintf(json, "{\"description\":\"same WB encoder/decoder; quality 8 until "
           "frame 19, quality 10 for frames 20-39, quality 8 after frame 40\","
           "\"frames\":%d,\"switch_frames\":[20,40],"
           "\"median_baseline_lsd_db\":%.9g,\"boundary_lsd_db\":[%.9g,%.9g],"
           "\"highband_ratio_4k_8k\":[[%.9g,%.9g],[%.9g,%.9g]],"
           "\"no_spectral_cliff\":%s,\"packet_bits_first\":%d,"
           "\"packet_bits_switch_a\":%d,\"packet_bits_switch_b\":%d}",
           nframes, median, boundary[0], boundary[1],
           ratio_around[0][0], ratio_around[0][1],
           ratio_around[1][0], ratio_around[1][1],
           no_cliff ? "true" : "false", packet_bits[0],
           packet_bits[20], packet_bits[40]);
}

static int values_are_zero(const float *values, int count)
{
   int i;
   for (i = 0; i < count; ++i) {
      if (values[i] != 0.0f)
         return 0;
   }
   return 1;
}

static float max_abs_value(const float *values, int count)
{
   float maximum = 0.0f;
   int i;
   for (i = 0; i < count; ++i) {
      float value = fabsf(values[i]);
      if (value > maximum)
         maximum = value;
   }
   return maximum;
}

static void write_switch_state(FILE *json)
{
   void *enc = speex_encoder_init(speex_lib_get_mode(SPEEX_MODEID_NB));
   void *old_dec = speex_decoder_init(speex_lib_get_mode(SPEEX_MODEID_NB));
   void *new_dec;
   SpeexBits bits;
   spx_int16_t input[160];
   spx_int16_t output[160];
   DecState *old_nb = (DecState *)old_dec;
   SBDecState *new_wb;
   DecState *new_low;
   int vbr = 0;
   int i;
   int old_hp_nonzero;
   int new_hp_zero;
   int new_lpc_zero;
   int new_qmf_zero;

   speex_encoder_ctl(enc, SPEEX_SET_VBR, &vbr);
   speex_bits_init(&bits);
   for (i = 0; i < 160; ++i)
      input[i] = (spx_int16_t)floor(0.5 + 12000.0f * generate_sample(0, i / 8000.0f));
   speex_encode_int(enc, input, &bits);
   speex_bits_rewind(&bits);
   speex_decode_int(old_dec, &bits, output);
   old_hp_nonzero = max_abs_value(old_nb->mem_hp, 2) > 0.0f;

   new_dec = speex_decoder_init(speex_lib_get_mode(SPEEX_MODEID_WB));
   new_wb = (SBDecState *)new_dec;
   new_low = (DecState *)new_wb->st_low;
   new_hp_zero = values_are_zero(new_low->mem_hp, 2);
   new_lpc_zero = values_are_zero(new_low->mem_sp, NB_ORDER) &&
                  values_are_zero(new_wb->mem_sp, 2 * new_wb->lpcSize);
   new_qmf_zero = values_are_zero(new_wb->g0_mem, 64) &&
                  values_are_zero(new_wb->g1_mem, 64);

   fprintf(json, "{\"preemphasis_filter_present_in_speex\":false,"
           "\"equivalent_state_note\":\"Speex uses high-pass memory and LPC "
           "synthesis memory; a decoder instance is recreated on mode change\","
           "\"old_narrowband_highpass_nonzero\":%s,"
           "\"new_wideband_low_highpass_zero\":%s,"
           "\"new_wideband_lpc_memory_zero\":%s,"
           "\"new_wideband_qmf_memory_zero\":%s,"
           "\"equivalent_state_reset_confirmed\":%s}",
           old_hp_nonzero ? "true" : "false",
           new_hp_zero ? "true" : "false",
           new_lpc_zero ? "true" : "false",
           new_qmf_zero ? "true" : "false",
           old_hp_nonzero && new_hp_zero && new_lpc_zero && new_qmf_zero
              ? "true" : "false");

   speex_decoder_destroy(new_dec);
   speex_bits_destroy(&bits);
   speex_decoder_destroy(old_dec);
   speex_encoder_destroy(enc);
}

static void write_mode_summary(FILE *json, int first, const char *name,
                               const ModeRun *run)
{
   float correlation;
   float nrmse;
   int lag;
   int frame;
   int all_inferred = 1;
   double mean_pi = 0.0;
   double mean_residual = 0.0;
   double mean_packet_bits = 0.0;
   int count = 0;

   envelope_metrics(run, &correlation, &nrmse, &lag);
   for (frame = 0; frame < run->nframes; ++frame) {
      if (run->inferred_mode[frame] != run->mode)
         all_inferred = 0;
      mean_pi += run->layer[frame][0].pi_gain[0];
      mean_residual += run->layer[frame][0].residual_mse[0];
      mean_packet_bits += run->packet_bits[frame];
      ++count;
   }
   fprintf(json, "%s{\"name\":\"%s\",\"mode\":%d,\"rate\":%d,"
           "\"frame_size\":%d,\"frames\":%d,\"quality\":8,"
           "\"vad_flag\":0,\"vad_required\":false,"
           "\"mode_inferred_all\":%s,\"envelope_corr\":%.9g,"
           "\"envelope_nrmse\":%.9g,\"envelope_lag_frames\":%d,"
           "\"residual_envelope_corr\":%.9g,\"mean_packet_bits\":%.9g,"
           "\"mean_pi_gain_sub0\":%.9g,\"mean_residual_mse_sub0\":%.9g}",
           first ? "" : ",", name, run->mode, run->sample_rate,
           run->frame_size, run->nframes, all_inferred ? "true" : "false",
           correlation, nrmse, lag,
           residual_envelope_correlation(run, lag),
           mean_packet_bits / count, mean_pi / count,
           mean_residual / count);
}

static void write_frame_details(FILE *json, const ModeRun *run,
                                int representative_frame)
{
   int layer;
   int sub;
   const LayerTrace *lt;
   fprintf(json, "{\"frame\":%d,\"packet_bits\":%d,\"inferred_mode\":%d,"
           "\"layers\":[", representative_frame,
           run->packet_bits[representative_frame],
           run->inferred_mode[representative_frame]);
   for (layer = 0; layer < 3; ++layer) {
      if (layer > 0 && run->mode < layer)
         break;
      lt = &run->layer[representative_frame][layer];
      fprintf(json, "%s{\"layer\":%d,\"submode\":%d,\"lpc_order\":%d,"
              "\"excitation_delay_subframes\":%d,\"subframes\":[",
              layer ? "," : "", layer, lt->submode, lt->lpc_order,
              layer == 0 ? 0 : 1);
      for (sub = 0; sub < MAX_SUBFRAMES; ++sub) {
         int j;
         fprintf(json, "%s{\"subframe\":%d,\"lpc\":[", sub ? "," : "", sub);
         for (j = 0; j < lt->lpc_order; ++j)
            fprintf(json, "%s%.9g", j ? "," : "", lt->lpc[sub][j]);
         fprintf(json, "],\"pi_gain\":%.9g,\"residual_rms\":%.9g,"
                 "\"residual_energy\":%.9g,\"excitation\":[",
                 lt->pi_gain[sub], lt->residual_rms[sub],
                 lt->residual_mse[sub]);
         for (j = 0; j < lt->excitation[sub].len; ++j)
            fprintf(json, "%s%.9g", j ? "," : "",
                    lt->excitation[sub].x[j]);
         fprintf(json, "],\"codebooks\":[");
         {
            int pass;
            for (pass = 0; pass < 2; ++pass) {
               const CodebookTrace *cb = &lt->codebook[sub][pass];
               int k;
               if (!cb->valid)
                  continue;
               fprintf(json, "%s{\"pass\":%d,\"bit_start\":%d,"
                       "\"bits\":%d,\"subvect_size\":%d,"
                       "\"shape_bits\":%d,\"gain_bits\":%d,"
                       "\"gain_index\":%d,\"indices\":[",
                       pass ? "," : "", pass, cb->bit_start, cb->bits,
                       cb->subvect_size, cb->shape_bits, cb->gain_bits,
                       cb->gain_index);
               for (k = 0; k < cb->nb_subvect; ++k)
                  fprintf(json, "%s%d", k ? "," : "", cb->index[k]);
               fprintf(json, "],\"signs\":[");
               for (k = 0; k < cb->nb_subvect; ++k)
                  fprintf(json, "%s%d", k ? "," : "", cb->sign[k]);
               fprintf(json, "]}");
            }
         }
         fprintf(json, "]}");
      }
      fprintf(json, "]}");
   }
   fprintf(json, "]}");
}

static void mode_run_free(ModeRun *run)
{
   free(run->input);
   free(run->output);
   run->input = NULL;
   run->output = NULL;
}

int main(int argc, char **argv)
{
   const char *out_dir = argc > 1 ? argv[1] : "artifacts";
   char json_path[1024];
   FILE *json;
   ModeRun runs[3];
   float switch_lsd[MAX_FRAMES] = {0};
   float switch_high_ratio[MAX_FRAMES] = {0};
   int switch_packet_bits[MAX_FRAMES] = {0};
   int i;

   memset(runs, 0, sizeof(runs));
   for (i = 0; i < 3; ++i) {
      runs[i].mode = i;
      runs[i].nframes = 48;
      fill_mode_input(&runs[i]);
      if (encode_decode_mode(&runs[i]) != 0) {
         fprintf(stderr, "mode run failed: %d\n", i);
         return 1;
      }
   }
   if (run_switch_test(64, switch_lsd, switch_high_ratio,
                       switch_packet_bits) != 0) {
      fprintf(stderr, "wideband switch test failed\n");
      return 1;
   }

   snprintf(json_path, sizeof(json_path), "%s/speex_probe.json", out_dir);
   json = fopen(json_path, "w");
   if (!json) {
      perror(json_path);
      return 1;
   }
   fprintf(json, "{\"speex_revision\":\"09b569d1b437e5edccf3406a38714ad795e46d38\","
           "\"sample\":\"built-in deterministic speech-like envelope\","
           "\"modes\":[");
   write_mode_summary(json, 1, "narrowband", &runs[0]);
   write_mode_summary(json, 0, "wideband", &runs[1]);
   write_mode_summary(json, 0, "ultra-wideband", &runs[2]);
   fprintf(json, "],\"representative\":{");
   fprintf(json, "\"narrowband\":");
   write_frame_details(json, &runs[0], 12);
   fprintf(json, ",\"wideband\":");
   write_frame_details(json, &runs[1], 12);
   fprintf(json, ",\"ultra-wideband\":");
   write_frame_details(json, &runs[2], 12);
   fprintf(json, "},\"wideband_switch\":");
   write_switch_summary(json, switch_lsd, switch_high_ratio,
                        switch_packet_bits, 64);
   fprintf(json, ",\"narrowband_to_wideband_state\":");
   write_switch_state(json);
   fprintf(json, "}");
   fclose(json);

   for (i = 0; i < 3; ++i)
      mode_run_free(&runs[i]);
   printf("wrote %s\n", json_path);
   return 0;
}
