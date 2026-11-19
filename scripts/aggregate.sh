#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R="$ROOT/results"

printf 'layout,tile_count,file_bytes,bitrate_mbps,frame_y_psnr_mean,'\
'boundary_psnr_mean,control_psnr_mean,seam_ratio_mean,seam_ratio_max,'\
'step_excess_max,visible_seams\n' > "$R/summary.csv"
for layout in 1x1 2x1 1x2 4x1 2x2 4x2; do
  tiles=$(awk -F, 'NR>1 && $1==-1{n++} END{print n+0}' \
    "$R/tiles_${layout}.tiles.csv")
  bytes=$(stat -c %s "$R/tiles_${layout}.hevc")
  bitrate=$(rg -o 'Bitrate: [0-9.]+' "$ROOT/work/encode_${layout}.log" |
    tail -1 | awk '{print $2}')
  metrics=$(awk -F, '
    FNR==NR {if(FNR>1){fp+=$2;fn++} next}
    FNR>1 {bp+=$4;cp+=$5;r+=$11;if($11>rmax)rmax=$11;
           if($10>emax)emax=$10;v+=$12;n++}
    END{if(n) printf "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d",
          fp/fn,bp/n,cp/n,r/n,rmax,emax,v;
        else printf "%.4f,n/a,n/a,n/a,n/a,n/a,0",fp/fn}' \
    "$R/tiles_${layout}.frames.csv" "$R/tiles_${layout}.seams.csv")
  printf '%s,%s,%s,%s,%s\n' \
    "$layout" "$tiles" "$bytes" "$bitrate" "$metrics" >> "$R/summary.csv"
done

printf 'layout,tile_id,tile_col,tile_row,x0,y0,x1,y1,width,height,'\
'ctu_x0,ctu_y0,ctu_x1,ctu_y1,ctu_count\n' > "$R/tile_boundaries.csv"
printf 'layout,frame,tile_id,tile_col,tile_row,x0,y0,x1,y1,ctu_count,'\
'ctu_bits,ctu_bytes,mean_bits,var_bits2,stddev_bits,min_bits,max_bits,'\
'intra_cu,inter_cu,skip_cu,pcm_cu\n' > "$R/tile_stats.csv"
for layout in 1x1 2x1 1x2 4x1 2x2 4x2; do
  awk -F, -v layout="$layout" 'NR>1 && $1==0 {
    print layout","$3","$4","$5","$6","$7","$8","$9","$10","$11","$12","$13","$14","$15","$16
  }' "$R/tiles_${layout}.boundaries.csv" >> "$R/tile_boundaries.csv"
  awk -F, -v layout="$layout" 'NR>1 && $1==-1 {
    print layout","$0
  }' "$R/tiles_${layout}.tiles.csv" >> "$R/tile_stats.csv"
done

printf 'qp,bitrate_mbps,lowband_rms_mean,lowband_rms_max,'\
'lowband_p999_max,lowband_max_abs_max,lowband_mean_abs_error,'\
'highband_rms_mean\n' > "$R/subband_qp_sweep.csv"
for qp in 18 24 32; do
  if [[ "$qp" == 32 ]]; then
    csv="$R/high_subband_2x2.subband.csv"
    log="$ROOT/work/hs32.log"
  else
    csv="$ROOT/work/hs${qp}.csv"
    log="$ROOT/work/hs${qp}.log"
  fi
  bitrate=$(rg -o 'Bitrate: [0-9.]+' "$log" |
    tail -1 | awk '{print $2}')
  awk -F, -v qp="$qp" -v bitrate="$bitrate" 'NR>1 {
    r+=$2; if($2>rpeak)rpeak=$2; if($4>p)p=$4; if($3>m)m=$3;
    e=($5>512)?$5-512:512-$5; if(e>em)em=e; h+=$7; n++
  } END {
    printf "%d,%s,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
      qp,bitrate,r/n,rpeak,p,m,em,h/n
  }' "$csv" >> "$R/subband_qp_sweep.csv"
done
