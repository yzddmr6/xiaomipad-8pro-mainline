/* SPDX-License-Identifier: MIT */
#include "photo-timing.h"

#include <errno.h>
#include <string.h>

void piano_photo_timing_reset(struct piano_photo_timing *timing)
{
	memset(timing, 0, sizeof(*timing));
}

int piano_photo_timing_event(struct piano_photo_timing *timing, const struct v4l2_event *event)
{
	uint32_t sequence = event->u.frame_sync.frame_sequence;
	unsigned int index = sequence % PIANO_PHOTO_SOF_HISTORY;
	bool gap;

	if (event->type != V4L2_EVENT_FRAME_SYNC)
		return -EINVAL;
	if (event->timestamp.tv_sec < 0 || event->timestamp.tv_nsec < 0 ||
	    event->timestamp.tv_nsec >= 1000000000)
		return -EPROTO;
	gap = timing->have_sequence && sequence != timing->last_sequence + 1;
	timing->last_sequence = sequence;
	timing->have_sequence = true;
	timing->sof[index].sequence = sequence;
	timing->sof[index].ns = (uint64_t)event->timestamp.tv_sec * 1000000000 + event->timestamp.tv_nsec;
	timing->sof[index].valid = timing->sof[index].ns != 0;
	return gap ? -EPIPE : 0;
}

int piano_photo_timing_frame(struct piano_photo_timing *timing, const struct v4l2_buffer *buffer,
			     uint64_t exposure_lead_ns, struct piano_photo_frame *frame)
{
	unsigned int index = buffer->sequence % PIANO_PHOTO_SOF_HISTORY;
	uint64_t sof, eof;

	if ((buffer->flags & V4L2_BUF_FLAG_TIMESTAMP_MASK) != V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC ||
	    (buffer->flags & V4L2_BUF_FLAG_TSTAMP_SRC_MASK) != V4L2_BUF_FLAG_TSTAMP_SRC_EOF ||
	    (buffer->flags & V4L2_BUF_FLAG_ERROR))
		return -EPROTO;
	if (!timing->sof[index].valid || timing->sof[index].sequence != buffer->sequence)
		return -ENODATA;
	if (buffer->timestamp.tv_sec < 0 || buffer->timestamp.tv_usec < 0 || buffer->timestamp.tv_usec >= 1000000)
		return -EPROTO;
	sof = timing->sof[index].ns;
	/* V4L2 DQBUF exposes timeval, truncating the driver's nanoseconds.
	 * Use the upper bound of that one-microsecond bin for the exposure end.
	 * This makes the lamp containment test conservative, never earlier.
	 */
	eof = (uint64_t)buffer->timestamp.tv_sec * 1000000000 + buffer->timestamp.tv_usec * 1000 + 999;
	if (eof <= sof || eof > frame->now_ns || exposure_lead_ns >= sof)
		return -EPROTO;
	frame->sof_ns = sof;
	frame->eof_ns = eof;
	if (exposure_lead_ns) {
		frame->exposure_start_ns = sof - exposure_lead_ns;
		frame->exposure_end_ns = eof;
	}
	if (eof - sof > timing->readout_ns)
		timing->readout_ns = eof - sof;
	return 0;
}
