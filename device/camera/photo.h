/* SPDX-License-Identifier: MIT */
/* Photo requests share the real capture/AE loop; no second sensor owner.
 * A readable loopback fd is the request's capability: the caller must already
 * have camera access under the normal udev/logind policy. No paths are accepted.
 */
#include <time.h>
#define PHOTO_MARGIN_NS UINT64_C(5000000)
#define PHOTO_CONTROL_BUDGET_NS UINT64_C(5000000)

static struct {
	int listener, client;
	struct cam *cam;
	unsigned int drain;
	bool requested, illuminated, frozen, previous_manual, previous_wb, previous_af;
	unsigned int settle;
	uint64_t pulse_ns, light_start_ns, light_end_ns;
	uint64_t frozen_ns, exposure_lead_ns;
	struct timespec deadline;
} photo = { .listener = -1, .client = -1 };

static void photo_end(const char *error)
{
	if (photo.frozen) {
		photo.cam->ae_manual = photo.previous_manual;
		photo.cam->wb_manual = photo.previous_wb;
		photo.cam->af_manual = photo.previous_af;
		photo.frozen = false;
	}
	if (photo.cam && photo.cam->flash_count) {
		uint64_t stopped; uint32_t fault;
		if (camera_flash_stop(photo.cam, &stopped, &fault) < 0 || fault)
			error = "ERR flash stop or hardware fault";
	}
	photo.light_start_ns = photo.light_end_ns = 0;
	if (photo.client >= 0) {
		if (error)
			send(photo.client, error, strlen(error), MSG_NOSIGNAL | MSG_DONTWAIT);
		close(photo.client);
	}
	photo.client = -1;
	photo.cam = NULL;
	photo.requested = photo.illuminated = false;
}

static int photo_listen(void)
{
	struct sockaddr_un addr = { .sun_family = AF_UNIX,
		.sun_path = "/run/piano-camera/photo.sock" };
	int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -1;
	unlink(addr.sun_path);
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) ||
	    chmod(addr.sun_path, 0666) || listen(fd, 1)) {
		close(fd);
		return -1;
	}
	return fd;
}

static void photo_accept(void)
{
	int fd = accept4(photo.listener, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
	if (fd < 0)
		return;
	if (photo.client >= 0) {
		send(fd, "ERR busy", 8, MSG_NOSIGNAL | MSG_DONTWAIT);
		close(fd);
		return;
	}
	photo.client = fd;
	clock_gettime(CLOCK_BOOTTIME, &photo.deadline);
	photo.deadline.tv_sec += 5; /* cancellation limit, never a capture delay */
}

static void photo_request(void)
{
	unsigned char request[8];
	union { struct cmsghdr align; char bytes[CMSG_SPACE(16 * sizeof(int))]; } control;
	struct iovec iov = { .iov_base = request, .iov_len = sizeof(request) };
	struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
		.msg_control = control.bytes, .msg_controllen = sizeof(control.bytes) };
	int proof = -1, count = 0;
	ssize_t n = recvmsg(photo.client, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
	if (n < 0 && (errno == EAGAIN || errno == EINTR))
		return;
	if (n < 0) {
		photo_end("ERR request");
		return;
	}
	for (struct cmsghdr *h = CMSG_FIRSTHDR(&msg); h; h = CMSG_NXTHDR(&msg, h)) {
		if (h->cmsg_level != SOL_SOCKET || h->cmsg_type != SCM_RIGHTS)
			continue;
		unsigned int nr = (h->cmsg_len - CMSG_LEN(0)) / sizeof(int);
		const int *fds = (const int *)CMSG_DATA(h);
		for (unsigned int i = 0; i < nr; i++) {
			if (!count++) proof = fds[i];
			else close(fds[i]);
		}
	}
	bool valid = n == (ssize_t)sizeof(request) && !memcmp(request, "PC01", 4) &&
		request[4] < NUM_CAMS && request[5] <= 1 && !request[6] && !request[7] &&
		!(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) && count == 1;
	struct cam *c = valid ? &cams[request[4]] : NULL;
	struct stat a, b;
	int flags = proof >= 0 ? fcntl(proof, F_GETFL) : -1;
	valid = valid && flags >= 0 && !(flags & O_PATH) &&
		(flags & O_ACCMODE) != O_WRONLY &&
		!fstat(proof, &a) && !fstat(c->loop_fd, &b) && S_ISCHR(a.st_mode) &&
		a.st_rdev == b.st_rdev && c->streaming && !photo.requested;
	if (proof >= 0) close(proof);
	if (!valid) {
		photo_end("ERR camera access or stream unavailable");
		return;
	}
	photo.cam = c;
	photo.requested = true;
	if (request[5]) {
		/* The front camera has no associated light. */
		if (strcmp(c->cfg->sensor, "s5kjn1") || !c->frame_sync || camera_flash_open(c) < 0) {
			photo_end("ERR rear light unavailable");
			return;
		}
		photo.illuminated = true;
	}
	/* Ambient captures drain buffers predating the caller's control writes. */
	photo.drain = photo.illuminated ? 0 : c->nbufs + 1;
}

static void photo_events(short events)
{
	struct timespec now;
	if (photo.client < 0)
		return;
	clock_gettime(CLOCK_BOOTTIME, &now);
	if (events & (POLLHUP | POLLERR | POLLNVAL) ||
	    now.tv_sec > photo.deadline.tv_sec ||
	    (now.tv_sec == photo.deadline.tv_sec && now.tv_nsec >= photo.deadline.tv_nsec)) {
		photo_end("ERR capture cancelled or exposure timeout");
		return;
	}
	if (events & POLLIN)
		photo_request();
}

static uint64_t photo_monotonic_ns(void)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static void photo_sync_events(struct cam *c)
{
	struct v4l2_event event;
	while (!xioctl(c->vid_fd, VIDIOC_DQEVENT, &event)) {
		if (event.type == V4L2_EVENT_FRAME_SYNC &&
		    piano_photo_timing_event(&c->timing, &event) < 0 &&
		    photo.cam == c && photo.illuminated)
			photo_end("ERR discontinuous physical frames");
	}
}

static void photo_frame(struct cam *c, const uint8_t *y, const uint8_t *uv,
			const struct v4l2_buffer *buffer)
{
	unsigned int sequence = buffer->sequence;
	if (photo.cam != c || !photo.requested)
		return;
	if (photo.drain) {
		if (!--photo.drain) {
			c->ae_updates = c->ae_stable = c->ae_far = 0;
			c->ae_acquiring = true;
			c->ae_ready = false;
		}
		return;
	}
	if (!c->ae_ready && !c->ae_manual)
		return;
	if (photo.illuminated) {
		struct piano_photo_frame frame = { .now_ns = photo_monotonic_ns() };
		uint64_t lead = photo.frozen ? photo.exposure_lead_ns : 0;
		if (piano_photo_timing_frame(&c->timing, buffer, lead, &frame) < 0) {
			photo_end("ERR missing physical frame timestamps");
			return;
		}
		if (!photo.frozen) {
			if (prepare_strobe(c, &photo.exposure_lead_ns, &photo.pulse_ns) < 0) {
				photo_end("ERR exposure exceeds flash window");
				return;
			}
			photo.previous_manual = c->ae_manual;
			photo.previous_wb = c->wb_manual;
			photo.previous_af = c->af_manual;
			c->ae_manual = c->wb_manual = c->af_manual = true;
			photo.frozen = true;
			photo.frozen_ns = photo_monotonic_ns();
			photo.settle = 2;
			return;
		}
		if (!photo.light_start_ns) {
			if (frame.sof_ns <= photo.frozen_ns || --photo.settle) return;
			uint64_t call = 0, done = 0;
			if (camera_flash_start(c, photo.pulse_ns, &call, &done) < 0 ||
			    !call || done < call || done + 2 * PHOTO_MARGIN_NS >= call + photo.pulse_ns) {
				photo_end("ERR flash strobe");
				return;
			}
			photo.light_start_ns = done + PHOTO_MARGIN_NS;
			photo.light_end_ns = call + photo.pulse_ns - PHOTO_MARGIN_NS;
			return;
		}
		if (frame.exposure_end_ns > photo.light_end_ns) {
			photo_end("ERR no complete exposure inside flash window");
			return;
		}
		if (frame.exposure_start_ns < photo.light_start_ns)
			return;
		uint64_t stopped; uint32_t fault;
		if (camera_flash_stop(c, &stopped, &fault) < 0 || fault ||
		    stopped < frame.exposure_end_ns) {
			photo_end("ERR flash hardware fault");
			return;
		}
	}
	unsigned int w = c->cfg->out_w, h = c->cfg->out_h;
	size_t size = (size_t)w * h * 4;
	int fd = memfd_create("camera-photo", MFD_CLOEXEC | MFD_ALLOW_SEALING);
	if (fd < 0 || ftruncate(fd, size)) {
		if (fd >= 0) close(fd);
		photo_end("ERR photo allocation");
		return;
	}
	void *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (pixels == MAP_FAILED) {
		close(fd);
		photo_end("ERR photo mapping");
		return;
	}
	nv12_to_bgrx(y, uv, c->bpl, pixels, w, h);
	munmap(pixels, size);
	if (fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL)) {
		close(fd);
		photo_end("ERR photo sealing");
		return;
	}
	char header[80];
	int length = snprintf(header, sizeof(header), "OK %u %u %u %u", w, h, sequence,
			      photo.illuminated);
	union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
	struct iovec iov = { .iov_base = header, .iov_len = length };
	struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1,
		.msg_control = control.bytes, .msg_controllen = sizeof(control.bytes) };
	struct cmsghdr *cm = CMSG_FIRSTHDR(&msg);
	cm->cmsg_level = SOL_SOCKET;
	cm->cmsg_type = SCM_RIGHTS;
	cm->cmsg_len = CMSG_LEN(sizeof(int));
	memcpy(CMSG_DATA(cm), &fd, sizeof(fd));
	sendmsg(photo.client, &msg, MSG_NOSIGNAL | MSG_DONTWAIT);
	close(fd);
	photo_end(NULL);
}
