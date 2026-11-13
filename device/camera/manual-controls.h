/* SPDX-License-Identifier: MIT */
#include <grp.h>
#include <limits.h>
/* Fixed native controls, serialized with 3A by the existing owner loop. */
#define CONTROL_PATH "/run/piano-camerad/control.sock"
#define CONTROL_CLIENTS 4
static int control_fd = -1;
static int control_clients[CONTROL_CLIENTS] = { -1, -1, -1, -1 };

enum control_domain { DOMAIN_AE, DOMAIN_WB, DOMAIN_AF };
struct named_control {
	const char *name, *unit;
	uint32_t id;
	enum control_domain domain;
};
static const struct named_control named_controls[] = {
	{ "exposure", "sensor-lines", V4L2_CID_EXPOSURE, DOMAIN_AE },
	{ "analog-gain", "driver-native", V4L2_CID_ANALOGUE_GAIN, DOMAIN_AE },
	{ "digital-gain", "q10", V4L2_CID_DIGITAL_GAIN, DOMAIN_AE },
	{ "red-balance", "q10", V4L2_CID_RED_BALANCE, DOMAIN_WB },
	{ "blue-balance", "q10", V4L2_CID_BLUE_BALANCE, DOMAIN_WB },
	{ "focus", "driver-native-dac", V4L2_CID_FOCUS_ABSOLUTE, DOMAIN_AF },
};
#define CONTROL_COUNT (sizeof(named_controls) / sizeof(named_controls[0]))

static int control_node(const struct cam *c, unsigned int i)
{
	if (i < 2)
		return c->sensor_fd;
	return i == 5 ? c->vcm_fd : c->vfe_fd;
}

static int control_query(struct cam *c, unsigned int i, struct v4l2_query_ext_ctrl *q)
{
	return xioctl(control_node(c, i), VIDIOC_QUERY_EXT_CTRL, q);
}

static int control_get(struct cam *c, unsigned int i, int *value)
{
	struct v4l2_control ctl = { .id = named_controls[i].id };
	int fd = control_node(c, i);

	if (fd < 0) {
		errno = EOPNOTSUPP;
		return -1;
	}
	if (xioctl(fd, VIDIOC_G_CTRL, &ctl) < 0)
		return -1;
	*value = ctl.value;
	return 0;
}

static void control_cache(struct cam *c, unsigned int i, int value)
{
	switch (i) {
	case 0: c->exposure = value; break;
	case 1: c->again = value; break;
	case 2: c->dgain = value; break;
	case 3: c->rgain = (double)value / WB_UNITY; break;
	case 4: c->bgain = (double)value / WB_UNITY; break;
	case 5: c->af_pos = value; break;
	}
}

static int control_refresh(struct cam *c, enum control_domain domain)
{
	int values[CONTROL_COUNT];

	for (unsigned int i = 0; i < CONTROL_COUNT; i++)
		if (named_controls[i].domain == domain && control_get(c, i, &values[i]) < 0)
			return -1;
	for (unsigned int i = 0; i < CONTROL_COUNT; i++)
		if (named_controls[i].domain == domain)
			control_cache(c, i, values[i]);
	return 0;
}

static void control_error(FILE *out, const char *stage, int error, bool attempted)
{
	fprintf(out, "{\"version\":1,\"ok\":false,\"stage\":\"%s\",\"errno\":%d,"
		"\"write_attempted\":%s}\n", stage, error, attempted ? "true" : "false");
}

/* The active sensor format and blanking controls describe output pixel timing. */
struct exposure_timing { uint64_t line_ns_num, line_ns_den; };

static int exposure_timing(struct cam *c, const struct v4l2_query_ext_ctrl *q,
			  struct exposure_timing *timing)
{
	struct v4l2_subdev_format f = { .which = V4L2_SUBDEV_FORMAT_ACTIVE, .pad = 0 };

	if (q->type != V4L2_CTRL_TYPE_INTEGER || !q->step) {
		errno = EOPNOTSUPP;
		return -1;
	}
	struct v4l2_control blank = { .id = V4L2_CID_HBLANK };
	struct v4l2_ext_control rate = { .id = V4L2_CID_PIXEL_RATE };
	struct v4l2_ext_controls controls = {
		.count = 1, .controls = &rate,
	};

	if (xioctl(c->sensor_fd, VIDIOC_SUBDEV_G_FMT, &f) < 0 ||
	    xioctl(c->sensor_fd, VIDIOC_G_CTRL, &blank) < 0 ||
	    xioctl(c->sensor_fd, VIDIOC_G_EXT_CTRLS, &controls) < 0)
		return -1;
	if (blank.value < 0 || rate.value64 <= 0) {
		errno = EOPNOTSUPP;
		return -1;
	}
	/* OV32D40 integration uses native VTS units. Full resolution has two
	 * output lines per integration count, binned modes have one.
	 */
	uint64_t ratio = 1;
	if (!strcmp(c->cfg->sensor, "ov32d40")) {
		struct v4l2_control vblank = { .id = V4L2_CID_VBLANK };
		if (xioctl(c->sensor_fd, VIDIOC_G_CTRL, &vblank) < 0)
			return -1;
		uint64_t frame_lines = (uint64_t)f.format.height + vblank.value;
		uint64_t native_vts = (uint64_t)q->maximum + 12;
		if (vblank.value < 0 || !native_vts || frame_lines % native_vts ||
		    frame_lines / native_vts < 1 || frame_lines / native_vts > 2) {
			errno = ERANGE;
			return -1;
		}
		ratio = frame_lines / native_vts;
	}
	*timing = (struct exposure_timing){
		((uint64_t)f.format.width + blank.value) * ratio * UINT64_C(1000000000),
		(uint64_t)rate.value64,
	};
	return 0;
}

static uint64_t exposure_ns(const struct exposure_timing *timing, uint64_t lines)
{
	return (lines * timing->line_ns_num + timing->line_ns_den / 2) / timing->line_ns_den;
}

static int exposure_lines(const char *arg, const struct v4l2_query_ext_ctrl *q,
			  const struct exposure_timing *timing, uint64_t *ns, long *lines)
{
	char *end;
	uint64_t minimum = (uint64_t)q->minimum;
	uint64_t steps = (q->maximum - q->minimum) / q->step;
	uint64_t maximum = minimum + steps * q->step;
	uint64_t quantum = q->step * timing->line_ns_num;
	long long input;

	errno = 0;
	input = strtoll(arg, &end, 10);
	if (errno || !arg[0] || *end || input <= 0 ||
	    (uint64_t)input < exposure_ns(timing, minimum) ||
	    (uint64_t)input > exposure_ns(timing, maximum)) {
		errno = ERANGE;
		return -1;
	}
	*ns = (uint64_t)input;
	uint64_t scaled = *ns * timing->line_ns_den;
	uint64_t origin = minimum * timing->line_ns_num;
	uint64_t delta = scaled > origin ? scaled - origin : 0;
	uint64_t selected = (delta + quantum / 2) / quantum;
	if (selected > steps)
		selected = steps;
	*lines = (long)(minimum + selected * q->step);
	return 0;
}

static void control_report_exposure_time(FILE *out, struct cam *c)
{
	struct v4l2_query_ext_ctrl q = { .id = V4L2_CID_EXPOSURE };
	struct exposure_timing timing;
	int actual;

	fputs(",{\"name\":\"exposure-time-ns\",\"unit\":\"ns\"", out);
	if (xioctl(c->sensor_fd, VIDIOC_QUERY_EXT_CTRL, &q) < 0 ||
	    exposure_timing(c, &q, &timing) < 0) {
		fprintf(out, ",\"supported\":false,\"timing_errno\":%d}", errno);
		return;
	}
	uint64_t last = q.minimum + (q.maximum - q.minimum) / q.step * q.step;
	fprintf(out, ",\"supported\":true,\"min\":%llu,\"max\":%llu,\"default\":%llu,"
		"\"line_ns_num\":%llu,\"line_ns_den\":%llu,\"line_step\":%llu,\"flags\":%u",
		(unsigned long long)exposure_ns(&timing, (uint64_t)q.minimum),
		(unsigned long long)exposure_ns(&timing, last),
		(unsigned long long)exposure_ns(&timing, (uint64_t)q.default_value),
		(unsigned long long)timing.line_ns_num, (unsigned long long)timing.line_ns_den,
		(unsigned long long)q.step, q.flags);
	if (control_get(c, 0, &actual) < 0)
		fprintf(out, ",\"get_errno\":%d", errno);
	else if (actual < q.minimum || actual > q.maximum)
		fprintf(out, ",\"get_errno\":%d,\"lines\":%d", ERANGE, actual);
	else
		fprintf(out, ",\"lines\":%d,\"value\":%llu", actual,
			(unsigned long long)exposure_ns(&timing, (uint64_t)actual));
	fputc('}', out);
}

static void control_report(FILE *out, struct cam *c, bool values)
{
	struct v4l2_query_ext_ctrl query[CONTROL_COUNT];
	int current[CONTROL_COUNT] = {0}, query_error[CONTROL_COUNT] = {0};
	int get_error[CONTROL_COUNT] = {0};
	bool complete = true;

	for (unsigned int i = 0; i < CONTROL_COUNT; i++) {
		int fd = control_node(c, i);

		query[i] = (struct v4l2_query_ext_ctrl){ .id = named_controls[i].id };
		if (fd < 0)
			continue;
		if (control_query(c, i, &query[i]) < 0) {
			query_error[i] = errno;
			complete = false;
		} else if (values && control_get(c, i, &current[i]) < 0) {
			get_error[i] = errno;
			complete = false;
		}
	}
	fprintf(out, "{\"version\":1,\"ok\":%s,\"camera\":\"%s\",\"active\":true,"
		"\"ae\":\"%s\",\"awb\":\"%s\",\"af\":\"%s\",\"controls\":[",
		complete ? "true" : "false", c->cfg->name, c->ae_manual ? "manual" : "auto",
		c->wb_manual ? "manual" : "auto",
		c->vcm_fd < 0 ? "unsupported" : c->af_manual ? "manual" :
		c->af_state == AF_OFF ? "error" : "auto");
	for (unsigned int i = 0; i < CONTROL_COUNT; i++) {
		const struct named_control *n = &named_controls[i];
		const struct v4l2_query_ext_ctrl *q = &query[i];

		fprintf(out, "%s{\"name\":\"%s\",\"unit\":\"%s\"", i ? "," : "", n->name, n->unit);
		if (control_node(c, i) < 0) {
			fputs(",\"supported\":false}", out);
			continue;
		}
		if (query_error[i]) {
			fprintf(out, ",\"query_errno\":%d}", query_error[i]);
			continue;
		}
		fprintf(out, ",\"supported\":true,\"type\":%u,\"min\":%lld,\"max\":%lld,"
			"\"step\":%llu,\"default\":%lld,\"flags\":%u",
			q->type, (long long)q->minimum, (long long)q->maximum,
			(unsigned long long)q->step, (long long)q->default_value, q->flags);
		if (values) {
			if (get_error[i])
				fprintf(out, ",\"get_errno\":%d", get_error[i]);
			else
				fprintf(out, ",\"value\":%d", current[i]);
		}
		fputc('}', out);
	}
	control_report_exposure_time(out, c);
	fputs("]}\n", out);
}

static void control_request(FILE *out, const char *packet)
{
	char verb[16], camera[16], name[32], arg[32], extra[2];
	struct cam *c = NULL;
	int count = sscanf(packet, "%15s %15s %31s %31s %1s", verb, camera, name, arg, extra);

	if (count < 2 || count > 4) {
		control_error(out, "syntax", EINVAL, false);
		return;
	}
	for (unsigned int i = 0; i < NUM_CAMS; i++)
		if (!strcmp(camera, cams[i].cfg->name))
			c = &cams[i];
	if (!c) {
		control_error(out, "camera", EINVAL, false);
		return;
	}
	if (!c->streaming) {
		control_error(out, "inactive", ENODEV, false);
		return;
	}
	if (!strcmp(verb, "set") && photo.cam == c) {
		control_error(out, "capture", EBUSY, false);
		return;
	}
	if ((!strcmp(verb, "caps") || !strcmp(verb, "get")) && count == 2) {
		control_report(out, c, !strcmp(verb, "get"));
		return;
	}
	if (strcmp(verb, "set") || count != 4) {
		control_error(out, "syntax", EINVAL, false);
		return;
	}
	if (!strcmp(name, "ae") || !strcmp(name, "awb") || !strcmp(name, "af")) {
		enum control_domain domain = !strcmp(name, "ae") ? DOMAIN_AE :
			!strcmp(name, "awb") ? DOMAIN_WB : DOMAIN_AF;
		bool manual = !strcmp(arg, "manual");

		if (!manual && strcmp(arg, "auto")) {
			control_error(out, "mode", EINVAL, false);
			return;
		}
		/* Confirm driver state before the software loop changes owner. */
		if (control_refresh(c, domain) < 0) {
			control_error(out, "get-before-mode", errno, false);
			return;
		}
		if (domain == DOMAIN_AE) {
			c->ae_manual = manual;
			c->ae_updates = c->ae_stable = 0;
			c->ae_acquiring = !manual;
			c->ae_ready = false;
		} else if (domain == DOMAIN_WB) {
			c->wb_manual = manual;
		} else {
			c->af_manual = manual;
			c->af_settle = 0;
			c->af_state = manual ? AF_OFF : AF_WAIT;
		}
		fprintf(out, "{\"version\":1,\"ok\":true,\"camera\":\"%s\","
			"\"domain\":\"%s\",\"mode\":\"%s\",\"write_attempted\":false}\n",
			camera, name, arg);
		return;
	}
	for (unsigned int i = 0; i < CONTROL_COUNT; i++) {
		const struct named_control *n = &named_controls[i];
		struct v4l2_query_ext_ctrl q = { .id = n->id };
		int fd = control_node(c, i), actual;
		char *end;
		long requested;
		uint64_t requested_ns = 0;
		struct exposure_timing timing;
		bool time_units = i == 0 && !strcmp(name, "exposure-time-ns");
		bool manual = n->domain == DOMAIN_AE ? c->ae_manual :
			n->domain == DOMAIN_WB ? c->wb_manual : c->af_manual;

		if (strcmp(name, n->name) && !time_units)
			continue;
		if (fd < 0) {
			control_error(out, "unsupported", EOPNOTSUPP, false);
			return;
		}
		if (!manual) {
			control_error(out, "auto-owner", EBUSY, false);
			return;
		}
		if (control_query(c, i, &q) < 0) {
			control_error(out, "query", errno, false);
			return;
		}
		if (time_units) {
			if (exposure_timing(c, &q, &timing) < 0) {
				control_error(out, "timing", errno, false);
				return;
			}
			if (exposure_lines(arg, &q, &timing, &requested_ns, &requested) < 0) {
				control_error(out, "value", errno, false);
				return;
			}
		} else {
			errno = 0;
			requested = strtol(arg, &end, 10);
			if (errno || !arg[0] || *end || requested < INT_MIN || requested > INT_MAX) {
				control_error(out, "value", ERANGE, false);
				return;
			}
		}
		if (q.type != V4L2_CTRL_TYPE_INTEGER || q.minimum < INT_MIN || q.maximum > INT_MAX ||
		    q.flags & (V4L2_CTRL_FLAG_DISABLED | V4L2_CTRL_FLAG_READ_ONLY | V4L2_CTRL_FLAG_GRABBED) ||
		    requested < q.minimum || requested > q.maximum || !q.step ||
		    (uint64_t)(requested - q.minimum) % q.step) {
			control_error(out, "range-or-flags", ERANGE, false);
			return;
		}
		if (ctrl_set(fd, n->id, (int)requested) < 0) {
			control_error(out, "set", errno, true);
			return;
		}
		/* Cache the accepted request if readback itself fails; never report it as a readback. */
		control_cache(c, i, (int)requested);
		if (n->domain == DOMAIN_AE) {
			c->ae_stable = 0;
			c->ae_ready = false;
		}
		if (control_get(c, i, &actual) < 0) {
			control_error(out, "readback", errno, true);
			return;
		}
		if (time_units) {
			if (actual < q.minimum || actual > q.maximum ||
			    (uint64_t)(actual - q.minimum) % q.step) {
				fprintf(out, "{\"version\":1,\"ok\":false,\"stage\":\"readback-range\","
					"\"lines\":%d,\"errno\":%d,\"write_attempted\":true}\n", actual, ERANGE);
				return;
			}
			control_cache(c, i, actual);
			fprintf(out, "{\"version\":1,\"ok\":%s,\"camera\":\"%s\","
				"\"control\":\"%s\",\"unit\":\"ns\",\"requested\":%llu,"
				"\"requested_lines\":%ld,\"lines\":%d,\"value\":%llu,"
				"\"errno\":%d,\"write_attempted\":true}\n",
				actual == requested ? "true" : "false", camera, name,
				(unsigned long long)requested_ns, requested, actual,
				(unsigned long long)exposure_ns(&timing, (uint64_t)actual),
				actual == requested ? 0 : EIO);
			return;
		}
		control_cache(c, i, actual);
		fprintf(out, "{\"version\":1,\"ok\":%s,\"camera\":\"%s\",\"control\":\"%s\","
			"\"requested\":%ld,\"value\":%d,\"errno\":%d,\"write_attempted\":true}\n",
			actual == requested ? "true" : "false", camera, name, requested, actual,
			actual == requested ? 0 : EIO);
		return;
	}
	control_error(out, "control", EINVAL, false);
}

/* Drain physical events before dequeueing the associated capture buffer. */
static int local_video_gid(gid_t *gid)
{
	FILE *file = fopen("/etc/group", "re");
	struct group *group;
	int found = 0;

	if (!file)
		return -1;
	while ((group = fgetgrent(file))) {
		if (!strcmp(group->gr_name, "video")) {
			*gid = group->gr_gid;
			found = 1;
			break;
		}
	}
	fclose(file);
	return found ? 0 : -1;
}

static int control_open(void)
{
	struct sockaddr_un sa = { .sun_family = AF_UNIX, .sun_path = CONTROL_PATH };
	gid_t video_gid;
	struct stat st;
	mode_t old;
	int fd, error;

	if (geteuid() != 0 || local_video_gid(&video_gid)) {
		errno = EPERM;
		return -1;
	}
	if (mkdir("/run/piano-camerad", 0750) < 0 && errno != EEXIST)
		return -1;
	if (lstat("/run/piano-camerad", &st) < 0 || !S_ISDIR(st.st_mode) || st.st_uid != 0) {
		errno = EPERM;
		return -1;
	}
	if (chown("/run/piano-camerad", 0, video_gid) < 0 || chmod("/run/piano-camerad", 0750) < 0)
		return -1;
	if (!lstat(CONTROL_PATH, &st)) {
		if (!S_ISSOCK(st.st_mode) || st.st_uid != 0) {
			errno = EEXIST;
			return -1;
		}
		if (unlink(CONTROL_PATH) < 0)
			return -1;
	} else if (errno != ENOENT) {
		return -1;
	}
	fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -1;
	old = umask(0077);
	int bound = bind(fd, (struct sockaddr *)&sa, sizeof(sa));
	umask(old);
	if (bound < 0)
		goto fail;
	if (chown(CONTROL_PATH, 0, video_gid) < 0 || chmod(CONTROL_PATH, 0660) < 0 ||
	    listen(fd, CONTROL_CLIENTS) < 0) {
		error = errno;
		unlink(CONTROL_PATH);
		errno = error;
		goto fail;
	}
	return fd;
fail:
	error = errno;
	close(fd);
	errno = error;
	return -1;
}

static void control_accept(void)
{
	int fd = accept4(control_fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);

	if (fd < 0)
		return;
	for (unsigned int i = 0; i < CONTROL_CLIENTS; i++)
		if (control_clients[i] < 0) {
			control_clients[i] = fd;
			return;
		}
	close(fd);
}

static void control_receive(unsigned int i)
{
	char packet[256], *reply = NULL;
	size_t bytes = 0;
	int fd = control_clients[i];
	ssize_t n = recv(fd, packet, sizeof(packet) - 1, MSG_TRUNC);

	if (n < 0 && (errno == EAGAIN || errno == EINTR))
		return;
	if (n > 0) {
		FILE *out = open_memstream(&reply, &bytes);

		if (out) {
			if (n >= (ssize_t)sizeof(packet) || memchr(packet, 0, (size_t)n))
				control_error(out, "packet", EMSGSIZE, false);
			else {
				packet[n] = 0;
				control_request(out, packet);
			}
			fclose(out);
			if (send(fd, reply, bytes, MSG_NOSIGNAL) < 0)
				logmsg(NULL, "control reply: %s", strerror(errno));
			free(reply);
		}
	}
	close(fd);
	control_clients[i] = -1;
}

static void control_close(void)
{
	for (unsigned int i = 0; i < CONTROL_CLIENTS; i++)
		if (control_clients[i] >= 0)
			close(control_clients[i]);
	if (control_fd >= 0) {
		close(control_fd);
		unlink(CONTROL_PATH);
	}
}
