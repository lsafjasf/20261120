#include <ass/ass.h>
#include <stdio.h>
#include <stdlib.h>

static void render_time(ASS_Renderer *renderer, ASS_Track *track, double seconds)
{
    long long now = (long long)(seconds * 1000.0 + (seconds >= 0 ? 0.5 : -0.5));
    int changed = 0;
    ASS_Image *images = ass_render_frame(renderer, track, now, &changed);
    int count = 0;
    int max_alpha = 0;
    unsigned int max_color_alpha = 0;
    unsigned int max_color = 0;
    int x0 = 1 << 30, y0 = 1 << 30, x1 = -1, y1 = -1;

    for (ASS_Image *image = images; image; image = image->next) {
        if (image->w <= 0 || image->h <= 0)
            continue;
        count++;
        if (getenv("ASS_DEBUG_IMAGES")) {
            int bitmap_max = 0;
            for (int y = 0; y < image->h; y++) {
                const unsigned char *row = image->bitmap + y * image->stride;
                for (int x = 0; x < image->w; x++)
                    if (row[x] > bitmap_max)
                        bitmap_max = row[x];
            }
            fprintf(stderr, "image %d color=%08x bitmap_max=%d at=%d,%d size=%dx%d\n",
                    count, image->color, bitmap_max,
                    image->dst_x, image->dst_y, image->w, image->h);
        }
        unsigned int color_alpha = image->color & 0xff;
        if (255 - color_alpha > max_color_alpha)
            max_color_alpha = 255 - color_alpha;
        if (image->color > max_color)
            max_color = image->color;
        for (int y = 0; y < image->h; y++) {
            const unsigned char *row = image->bitmap + y * image->stride;
            for (int x = 0; x < image->w; x++) {
                int alpha = (row[x] * (int)(255 - color_alpha) + 127) / 255;
                if (alpha > max_alpha)
                    max_alpha = alpha;
                if (alpha <= 0)
                    continue;
                int px = image->dst_x + x;
                int py = image->dst_y + y;
                if (px < x0) x0 = px;
                if (py < y0) y0 = py;
                if (px > x1) x1 = px;
                if (py > y1) y1 = py;
            }
        }
    }

    if (max_alpha <= 0)
        x0 = y0 = x1 = y1 = -1;
    printf("%.1f\t%lld\t%d\t%.4f\t%d\t%d\t%d\t%d\t%u\t%08x\n",
           seconds, now, count, max_alpha / 255.0, x0, y0, x1, y1,
           max_color_alpha, max_color);
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s subtitle.ass < times.txt\n", argv[0]);
        return 2;
    }
    ASS_Library *library = ass_library_init();
    ASS_Renderer *renderer = ass_renderer_init(library);
    if (!library || !renderer)
        return 3;
    ass_set_frame_size(renderer, 1920, 1080);
    ass_set_storage_size(renderer, 1920, 1080);
    ass_set_pixel_aspect(renderer, 1.0);
    ass_set_fonts(renderer, NULL, "DejaVu Sans", 1, NULL, 1);

    ASS_Track *track = ass_read_file(library, argv[1], NULL);
    if (!track)
        return 4;
    printf("t\tmilliseconds\timages\talpha\tx0\ty0\tx1\ty1\tcolor_alpha\tcolor\n");
    double seconds;
    while (scanf("%lf", &seconds) == 1)
        render_time(renderer, track, seconds);

    ass_free_track(track);
    ass_renderer_done(renderer);
    ass_library_done(library);
    return 0;
}
