/* SPDX-License-Identifier: MIT */
#ifndef PIANO_PHOTO_TIMING_H
#define PIANO_PHOTO_TIMING_H

#include <stdbool.h>
#include <stdint.h>
struct piano_photo_frame {
	uint64_t now_ns, sof_ns, eof_ns, exposure_start_ns, exposure_end_ns;
};
#include <time.h>
#include <linux/videodev2.h>

#define PIANO_PHOTO_SOF_HISTORY 16

/* One hardware capture queue, reset when its stream is rebuilt. */
struct piano_photo_timing {
	struct { uint32_t sequence; uint64_t ns; bool valid; } sof[PIANO_PHOTO_SOF_HISTORY];
	uint32_t last_sequence;
	bool have_sequence;
	uint64_t readout_ns;
};

void piano_photo_timing_reset(struct piano_photo_timing *timing);
int piano_photo_timing_event(struct piano_photo_timing *timing, const struct v4l2_event *event);
int piano_photo_timing_frame(struct piano_photo_timing *timing, const struct v4l2_buffer *buffer,
			     uint64_t exposure_lead_ns, struct piano_photo_frame *frame);

#endif
