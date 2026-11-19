#define _POSIX_C_SOURCE 200809L

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define WIDTH 1920
#define HEIGHT 1080
#define C_WIDTH (WIDTH / 2)
#define C_HEIGHT (HEIGHT / 2)
#define EDGE_X 961
#define PIXELS ((size_t)WIDTH * HEIGHT)
#define CHROMA_PIXELS ((size_t)C_WIDTH * C_HEIGHT)

typedef struct {
    uint8_t *y;
    uint8_t *cb;
    uint8_t *cr;
} Frame420;

typedef struct {
    Frame420 source;
    float *cb;
    float *cr;
    uint8_t *rgb;
    float *horizontal;
} Buffers;

typedef void (*MethodFn)(const Frame420 *, float *, float *, uint8_t *);

typedef struct {
    double *samples;
    size_t count;
} Samples;

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1000000.0;
}

static uint8_t quantize(double value) {
    double rounded = floor(value + 0.5);
    if (rounded < 0.0) {
        return 0;
    }
    if (rounded > 255.0) {
        return 255;
    }
    return (uint8_t)rounded;
}

static void rgb_to_ycbcr(
    const double rgb[3], double *y, double *cb, double *cr
) {
    *y = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2];
    *cb = 128.0 - 0.168736 * rgb[0] - 0.331264 * rgb[1] + 0.5 * rgb[2];
    *cr = 128.0 + 0.5 * rgb[0] - 0.418688 * rgb[1] - 0.081312 * rgb[2];
}

static void build_source(Frame420 *frame) {
    uint8_t *cb444 = malloc(PIXELS);
    uint8_t *cr444 = malloc(PIXELS);
    if (cb444 == NULL || cr444 == NULL) {
        fprintf(stderr, "allocation failed\n");
        exit(2);
    }

    const double red[3] = {220.0, 32.0, 32.0};
    const double blue[3] = {32.0, 32.0, 220.0};
    for (int y = 0; y < HEIGHT; ++y) {
        for (int x = 0; x < WIDTH; ++x) {
            double yy, cb, cr;
            rgb_to_ycbcr(x < EDGE_X ? red : blue, &yy, &cb, &cr);
            size_t pixel = (size_t)y * WIDTH + x;
            frame->y[pixel] = quantize(yy);
            cb444[pixel] = quantize(cb);
            cr444[pixel] = quantize(cr);
        }
    }

    for (int y = 0; y < C_HEIGHT; ++y) {
        for (int x = 0; x < C_WIDTH; ++x) {
            unsigned cb_sum = 0;
            unsigned cr_sum = 0;
            for (int dy = 0; dy < 2; ++dy) {
                for (int dx = 0; dx < 2; ++dx) {
                    size_t pixel =
                        (size_t)(2 * y + dy) * WIDTH + (size_t)(2 * x + dx);
                    cb_sum += cb444[pixel];
                    cr_sum += cr444[pixel];
                }
            }
            size_t chroma = (size_t)y * C_WIDTH + x;
            frame->cb[chroma] = quantize(cb_sum / 4.0);
            frame->cr[chroma] = quantize(cr_sum / 4.0);
        }
    }
    free(cb444);
    free(cr444);
}

static int allocate_all(Buffers *buffers) {
    buffers->source.y = malloc(PIXELS);
    buffers->source.cb = malloc(CHROMA_PIXELS);
    buffers->source.cr = malloc(CHROMA_PIXELS);
    buffers->cb = malloc(PIXELS * sizeof(float));
    buffers->cr = malloc(PIXELS * sizeof(float));
    buffers->rgb = malloc(PIXELS * 3);
    buffers->horizontal = malloc((size_t)C_HEIGHT * WIDTH * sizeof(float));
    return buffers->source.y && buffers->source.cb && buffers->source.cr &&
           buffers->cb && buffers->cr && buffers->rgb && buffers->horizontal;
}

static void free_all(Buffers *buffers) {
    free(buffers->source.y);
    free(buffers->source.cb);
    free(buffers->source.cr);
    free(buffers->cb);
    free(buffers->cr);
    free(buffers->rgb);
    free(buffers->horizontal);
}

static int compare_double(const void *left, const void *right) {
    double a = *(const double *)left;
    double b = *(const double *)right;
    return (a > b) - (a < b);
}

static void print_stats(const char *name, Samples samples, int last) {
    qsort(samples.samples, samples.count, sizeof(double), compare_double);
    size_t middle = samples.count / 2;
    double median = samples.count % 2
                        ? samples.samples[middle]
                        : 0.5 * (samples.samples[middle - 1] + samples.samples[middle]);
    size_t p95_index = (size_t)ceil(0.95 * (double)samples.count) - 1;
    if (p95_index >= samples.count) {
        p95_index = samples.count - 1;
    }
    printf(
        "    \"%s\": {\"median_ms\": %.6f, \"p95_ms\": %.6f, "
        "\"fps_from_median\": %.3f, \"meets_30fps\": %s, "
        "\"meets_60fps\": %s}%s\n",
        name,
        median,
        samples.samples[p95_index],
        1000.0 / median,
        median <= 1000.0 / 30.0 ? "true" : "false",
        median <= 1000.0 / 60.0 ? "true" : "false",
        last ? "" : ",");
}

static double lanczos3_kernel(double t) {
    double absolute = fabs(t);
    if (absolute < 1e-12) {
        return 1.0;
    }
    if (absolute >= 3.0) {
        return 0.0;
    }
    double x = 3.14159265358979323846 * t;
    return 3.0 * sin(x) * sin(x / 3.0) / (x * x);
}

static void odd_lanczos_taps(
    int source_length, int *indices, double weights[][6]
) {
    for (int j = 0; j < source_length; ++j) {
        double coordinate = (double)j + 0.5;
        int base = j;
        double sum = 0.0;
        for (int offset = -2; offset <= 3; ++offset) {
            int slot = offset + 2;
            int index = base + offset;
            if (index < 0) {
                index = 0;
            } else if (index >= source_length) {
                index = source_length - 1;
            }
            indices[(size_t)j * 6 + slot] = index;
            weights[j][slot] = lanczos3_kernel(coordinate - (base + offset));
            sum += weights[j][slot];
        }
        for (int slot = 0; slot < 6; ++slot) {
            weights[j][slot] /= sum;
        }
    }
}

static void nearest_upsample(
    const Frame420 *source, float *dst_cb, float *dst_cr
) {
    for (int y = 0; y < HEIGHT; ++y) {
        int sy = y / 2;
        for (int x = 0; x < WIDTH; ++x) {
            size_t src_index = (size_t)sy * C_WIDTH + x / 2;
            size_t dst_index = (size_t)y * WIDTH + x;
            dst_cb[dst_index] = source->cb[src_index];
            dst_cr[dst_index] = source->cr[src_index];
        }
    }
}

static void bilinear_plane(
    const uint8_t *source, float *horizontal, float *destination
) {
    for (int y = 0; y < C_HEIGHT; ++y) {
        for (int x = 0; x < C_WIDTH; ++x) {
            horizontal[(size_t)y * WIDTH + 2 * x] = source[(size_t)y * C_WIDTH + x];
            int next = x + 1 < C_WIDTH ? x + 1 : x;
            horizontal[(size_t)y * WIDTH + 2 * x + 1] =
                0.5f * (source[(size_t)y * C_WIDTH + x] +
                        source[(size_t)y * C_WIDTH + next]);
        }
    }
    for (int y = 0; y < C_HEIGHT; ++y) {
        int next_y = y + 1 < C_HEIGHT ? y + 1 : y;
        for (int x = 0; x < WIDTH; ++x) {
            destination[(size_t)(2 * y) * WIDTH + x] =
                horizontal[(size_t)y * WIDTH + x];
            destination[(size_t)(2 * y + 1) * WIDTH + x] =
                0.5f * (horizontal[(size_t)y * WIDTH + x] +
                        horizontal[(size_t)next_y * WIDTH + x]);
        }
    }
}

static void lanczos_plane(
    const uint8_t *source,
    float *horizontal,
    float *destination,
    const int *horizontal_indices,
    double horizontal_weights[][6],
    const int *vertical_indices,
    double vertical_weights[][6]
) {
    for (int y = 0; y < C_HEIGHT; ++y) {
        for (int x = 0; x < C_WIDTH; ++x) {
            horizontal[(size_t)y * WIDTH + 2 * x] =
                source[(size_t)y * C_WIDTH + x];
        }
        for (int x = 0; x < C_WIDTH; ++x) {
            double value = 0.0;
            for (int slot = 0; slot < 6; ++slot) {
                int source_x = horizontal_indices[(size_t)x * 6 + slot];
                value += horizontal_weights[x][slot] *
                         source[(size_t)y * C_WIDTH + source_x];
            }
            horizontal[(size_t)y * WIDTH + 2 * x + 1] = (float)value;
        }
    }
    for (int y = 0; y < C_HEIGHT; ++y) {
        memcpy(
            &destination[(size_t)(2 * y) * WIDTH],
            &horizontal[(size_t)y * WIDTH],
            WIDTH * sizeof(float)
        );
        for (int x = 0; x < WIDTH; ++x) {
            double value = 0.0;
            for (int slot = 0; slot < 6; ++slot) {
                int source_y = vertical_indices[(size_t)y * 6 + slot];
                value += vertical_weights[y][slot] *
                         horizontal[(size_t)source_y * WIDTH + x];
            }
            destination[(size_t)(2 * y + 1) * WIDTH + x] = (float)value;
        }
    }
}

static void ycbcr_to_rgb(
    const Frame420 *source, const float *cb, const float *cr, uint8_t *rgb
) {
    for (size_t i = 0; i < PIXELS; ++i) {
        float y = source->y[i];
        float b_delta = cb[i] - 128.0f;
        float r_delta = cr[i] - 128.0f;
        float red = y + 1.402f * r_delta;
        float green = y - 0.344136f * b_delta - 0.714136f * r_delta;
        float blue = y + 1.772f * b_delta;
        rgb[3 * i] = (uint8_t)(red < 0.0f ? 0.0f : red > 255.0f ? 255.0f : red + 0.5f);
        rgb[3 * i + 1] =
            (uint8_t)(green < 0.0f ? 0.0f : green > 255.0f ? 255.0f : green + 0.5f);
        rgb[3 * i + 2] =
            (uint8_t)(blue < 0.0f ? 0.0f : blue > 255.0f ? 255.0f : blue + 0.5f);
    }
}

static void method_nearest(
    const Frame420 *source, float *cb, float *cr, uint8_t *rgb
) {
    nearest_upsample(source, cb, cr);
    ycbcr_to_rgb(source, cb, cr, rgb);
}

static void method_bilinear(
    const Frame420 *source,
    float *cb,
    float *cr,
    float *horizontal,
    uint8_t *rgb
) {
    bilinear_plane(source->cb, horizontal, cb);
    bilinear_plane(source->cr, horizontal, cr);
    ycbcr_to_rgb(source, cb, cr, rgb);
}

static void method_lanczos(
    const Frame420 *source,
    float *cb,
    float *cr,
    float *horizontal,
    const int *horizontal_indices,
    double horizontal_weights[][6],
    const int *vertical_indices,
    double vertical_weights[][6],
    uint8_t *rgb
) {
    lanczos_plane(
        source->cb,
        horizontal,
        cb,
        horizontal_indices,
        horizontal_weights,
        vertical_indices,
        vertical_weights
    );
    lanczos_plane(
        source->cr,
        horizontal,
        cr,
        horizontal_indices,
        horizontal_weights,
        vertical_indices,
        vertical_weights
    );
    ycbcr_to_rgb(source, cb, cr, rgb);
}

static void run_lanczos(
    const Frame420 *source,
    float *cb,
    float *cr,
    float *horizontal,
    const int *horizontal_indices,
    double horizontal_weights[][6],
    const int *vertical_indices,
    double vertical_weights[][6],
    uint8_t *rgb
) {
    method_lanczos(
        source,
        cb,
        cr,
        horizontal,
        horizontal_indices,
        horizontal_weights,
        vertical_indices,
        vertical_weights,
        rgb
    );
}

static int h_indices[C_WIDTH * 6];
static double h_weights[C_WIDTH][6];
static int v_indices[C_HEIGHT * 6];
static double v_weights[C_HEIGHT][6];

static void measure(const char *method, Buffers *buffers, int iterations, int last) {
    Samples samples = {malloc((size_t)iterations * sizeof(double)), (size_t)iterations};
    if (samples.samples == NULL) {
        fprintf(stderr, "allocation failed\n");
        exit(2);
    }
    for (int warmup = 0; warmup < 5; ++warmup) {
        if (strcmp(method, "nearest") == 0) {
            method_nearest(
                &buffers->source, buffers->cb, buffers->cr, buffers->rgb
            );
        } else if (strcmp(method, "bilinear") == 0) {
            method_bilinear(
                &buffers->source,
                buffers->cb,
                buffers->cr,
                buffers->horizontal,
                buffers->rgb
            );
        } else {
            run_lanczos(
                &buffers->source,
                buffers->cb,
                buffers->cr,
                buffers->horizontal,
                h_indices,
                h_weights,
                v_indices,
                v_weights,
                buffers->rgb
            );
        }
    }
    for (int iteration = 0; iteration < iterations; ++iteration) {
        double start = now_ms();
        if (strcmp(method, "nearest") == 0) {
            method_nearest(
                &buffers->source, buffers->cb, buffers->cr, buffers->rgb
            );
        } else if (strcmp(method, "bilinear") == 0) {
            method_bilinear(
                &buffers->source,
                buffers->cb,
                buffers->cr,
                buffers->horizontal,
                buffers->rgb
            );
        } else {
            run_lanczos(
                &buffers->source,
                buffers->cb,
                buffers->cr,
                buffers->horizontal,
                h_indices,
                h_weights,
                v_indices,
                v_weights,
                buffers->rgb
            );
        }
        samples.samples[iteration] = now_ms() - start;
    }
    print_stats(method, samples, last);
    free(samples.samples);
}

static unsigned long checksum(const uint8_t *data, size_t length) {
    unsigned long value = 1469598103934665603UL;
    for (size_t i = 0; i < length; ++i) {
        value ^= data[i];
        value *= 1099511628211UL;
    }
    return value;
}

static int parse_iterations(int argc, char **argv) {
    int iterations = 100;
    if (argc > 1) {
        iterations = atoi(argv[1]);
    }
    if (iterations < 1) {
        fprintf(stderr, "iterations must be positive\n");
        exit(2);
    }
    return iterations;
}

int main(int argc, char **argv) {
    int iterations = parse_iterations(argc, argv);
    Buffers buffers = {0};
    if (!allocate_all(&buffers)) {
        fprintf(stderr, "allocation failed\n");
        return 2;
    }
    build_source(&buffers.source);
    odd_lanczos_taps(C_WIDTH, h_indices, h_weights);
    odd_lanczos_taps(C_HEIGHT, v_indices, v_weights);

    printf("{\n");
    printf(
        "  \"frame\": {\"width\": %d, \"height\": %d, \"format\": "
        "\"4:2:0 -> 4:4:4 + BT.601 RGB\"},\n",
        WIDTH,
        HEIGHT
    );
    printf(
        "  \"schedule\": {\"iterations\": %d, \"warmups\": 5, "
        "\"threads\": 1, \"buffers\": \"preallocated\"},\n",
        iterations
    );
    printf("  \"methods\": {\n");
    measure("nearest", &buffers, iterations, 0);
    measure("bilinear", &buffers, iterations, 0);
    measure("lanczos3", &buffers, iterations, 1);
    printf("  },\n");
    printf(
        "  \"output_checksum_fnv1a64\": \"%016lx\",\n",
        checksum(buffers.rgb, PIXELS * 3)
    );
    printf("  \"denoising_tested\": false\n");
    printf("}\n");

    free_all(&buffers);
    return 0;
}
