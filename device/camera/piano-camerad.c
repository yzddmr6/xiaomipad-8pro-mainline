// SPDX-License-Identifier: MIT
/*
 * piano-camerad: runs the Xiaomi Pad 8 Pro cameras through the SM8750 TFE
 * hardware ISP (the CAMSS pixel path, NV12 out) and feeds the frames into
 * v4l2loopback devices, which applications and PipeWire see as plain
 * webcams.
 *
 * A camera only streams while a reader streams from its loopback device
 * (v4l2loopback client usage events).  The daemon closes the control loop
 * the ISP lacks: auto exposure on the sensor (exposure lines, then analogue
 * gain) plus the ISP digital gain, and gray-world auto white balance on the
 * ISP white balance gains, both from a sparse sample of the output frames,
 * and contrast-detect autofocus where the camera has a focus actuator.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#include <linux/media.h>
#include <linux/v4l2-subdev.h>
#include <linux/videodev2.h>

#include "nv12-bgrx.h"

/* v4l2loopback private event: capture side streaming started/stopped */
#define V4L2_EVENT_PRI_CLIENT_USAGE (V4L2_EVENT_PRIVATE_START + 0x08E00000 + 1)
struct v4l2_event_client_usage {
	uint32_t count;
};

#define NUM_BUFS	4
#define AE_TARGET	112.0	/* mean luma, gamma encoded */
#define AE_PERIOD	3	/* frames between control updates */
#define AE_STARTUP_UPDATES 10	/* bounded acquisition, then steady tracking */
#define AE_REACQUIRE	0.35	/* lasting luma error (log) to acquire again */
#define DGAIN_UNITY	1024
#define DGAIN_MAX	(8 * DGAIN_UNITY)
#define WB_UNITY	1024
#define SAT_UNITY	256

/* rendering defaults, overridable from the environment (see apply_look) */
#define DEF_SATURATION	100	/* percent */
#define DEF_CONTRAST	96	/* S curve share of the tone curve, of 256 */

struct cam_cfg {
	const char *name;
	const char *label;	/* v4l2loopback card label */
	const char *sensor;	/* entity name prefix */
	const char *phy;
	const char *csid;
	const char *vfe;	/* PIX line subdev */
	const char *video;	/* PIX line video node */
	const char *vcm;	/* focus actuator entity prefix, or NULL */
	unsigned int in_w, in_h;	/* sensor mode */
	unsigned int out_w, out_h;	/* ISP output (MN downscaler) */
	bool hflip, vflip;	/* sensor readout flips for an upright image */
	int again_unit;		/* analogue gain control value for 1x */
	double fps;
	int ccm[9];		/* camera RGB to sRGB, Q10, row-major */
};

/* qcom-camss PIX line control: colour correction matrix, 3x3 s32, Q10 */
#define V4L2_CID_CAMSS_CCM	(V4L2_CID_USER_BASE | 0x1ff0)

/*
 * TODO: configurable output size (per camera, e.g. from /etc/piano/camera.conf,
 * or several sizes offered on the loopback device); fixed at 1920x1440 now.
 */
static const struct cam_cfg cam_cfgs[] = {
	{
		.name = "rear", .label = "Rear Camera",
		.sensor = "s5kjn1", .phy = "msm_csiphy1",
		.csid = "msm_csid0", .vfe = "msm_vfe0_pix",
		.video = "msm_vfe0_video3", .vcm = "dw9768",
		.in_w = 4080, .in_h = 3072, .out_w = 1920, .out_h = 1440,
		.vflip = true, .again_unit = 32, .fps = 30,
		/* mean of the stock tuning matrices */
		.ccm = { 1732, -667, -40, -230, 1337, -82, -93, -989, 2106 },
	},
	{
		.name = "front", .label = "Front Camera",
		.sensor = "ov32d40", .phy = "msm_csiphy4",
		.csid = "msm_csid1", .vfe = "msm_vfe1_pix",
		.video = "msm_vfe1_video3",
		.in_w = 3264, .in_h = 2448, .out_w = 1920, .out_h = 1440,
		.again_unit = 256, .fps = 30,
		.ccm = { 1649, -375, -251, -245, 1525, -255, -169, -308, 1501 },
	},
};

#define NUM_CAMS (sizeof(cam_cfgs) / sizeof(cam_cfgs[0]))

struct buf {
	void *ptr;
	size_t len;
};

#define AF_HIST		64	/* sharpness samples kept per scan */

struct cam {
	const struct cam_cfg *cfg;
	int loop_fd;
	bool streaming;
	int vid_fd, sensor_fd, vfe_fd, vcm_fd;
	struct buf bufs[NUM_BUFS];
	unsigned int bpl, sizeimage, nbufs;
	size_t uv_offset, frame_span;
	uint8_t *stage;		/* converted BGRx or repacked NV12 output */
	unsigned long frames;
	/* sensor control ranges */
	int exp_min, exp_max, ag_min, ag_max;
	/* auto exposure / white balance state, kept across sessions */
	int exposure, again, dgain;
	double rgain, bgain;
	bool have_state;
	/* exposure statistics are fresh for each stream */
	double ae_mean, ae_bright;
	unsigned int ae_updates, ae_stable, ae_far;
	bool ae_acquiring, ae_ready;
	/* autofocus: lens position range, scan state */
	int af_min, af_max, af_pos;
	enum { AF_OFF, AF_WAIT, AF_SCAN, AF_FINE, AF_VERIFY, AF_LOCKED } af_state;
	int af_step, af_dir, af_origin, af_drops, af_best_pos, af_settle;
	int af_lost, af_n;
	bool af_reversed;
	double af_best, af_floor, af_lock;
	int af_fine[3];			/* fine pass positions */
	int af_hist_pos[AF_HIST];	/* samples of the current scan */
	double af_hist_val[AF_HIST];
};

static struct cam cams[NUM_CAMS];
static volatile sig_atomic_t quit;
static double srgb_lin[256];
static int saturation = DEF_SATURATION * SAT_UNITY / 100;
static int contrast = DEF_CONTRAST;
static int af_settle_frames;
/* Local publication workaround; ISP capture and control statistics stay NV12. */
static bool output_bgrx = false;

static void logmsg(const struct cam *c, const char *fmt, ...)
{
	va_list ap;

	fprintf(stderr, "%s%s", c ? c->cfg->name : "piano-camerad",
		c ? ": " : ": ");
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
}

static int xioctl(int fd, unsigned long req, void *arg)
{
	int ret;

	do {
		ret = ioctl(fd, req, arg);
	} while (ret < 0 && errno == EINTR);

	return ret;
}

/* ------------------------------------------------------------------ */
/* media controller                                                   */

struct topo {
	int fd;
	struct media_v2_topology t;
	struct media_v2_entity *ents;
	struct media_v2_interface *ifs;
	struct media_v2_pad *pads;
	struct media_v2_link *links;
};

static void topo_free(struct topo *tp)
{
	free(tp->ents);
	free(tp->ifs);
	free(tp->pads);
	free(tp->links);
	memset(tp, 0, sizeof(*tp));
}

static int topo_load(struct topo *tp, int fd)
{
	memset(tp, 0, sizeof(*tp));
	tp->fd = fd;
	if (xioctl(fd, MEDIA_IOC_G_TOPOLOGY, &tp->t) < 0)
		return -1;

	tp->ents = calloc(tp->t.num_entities, sizeof(*tp->ents));
	tp->ifs = calloc(tp->t.num_interfaces, sizeof(*tp->ifs));
	tp->pads = calloc(tp->t.num_pads, sizeof(*tp->pads));
	tp->links = calloc(tp->t.num_links, sizeof(*tp->links));
	tp->t.ptr_entities = (uintptr_t)tp->ents;
	tp->t.ptr_interfaces = (uintptr_t)tp->ifs;
	tp->t.ptr_pads = (uintptr_t)tp->pads;
	tp->t.ptr_links = (uintptr_t)tp->links;

	if (xioctl(fd, MEDIA_IOC_G_TOPOLOGY, &tp->t) < 0) {
		topo_free(tp);
		return -1;
	}

	return 0;
}

static const struct media_v2_entity *topo_entity(const struct topo *tp,
						 const char *prefix)
{
	size_t n = strlen(prefix);

	for (unsigned int i = 0; i < tp->t.num_entities; i++)
		if (!strncmp(tp->ents[i].name, prefix, n) &&
		    (tp->ents[i].name[n] == '\0' || tp->ents[i].name[n] == ' '))
			return &tp->ents[i];

	return NULL;
}

static const struct media_v2_pad *topo_pad(const struct topo *tp,
					   uint32_t id)
{
	for (unsigned int i = 0; i < tp->t.num_pads; i++)
		if (tp->pads[i].id == id)
			return &tp->pads[i];

	return NULL;
}

static int topo_open_devnode(const struct topo *tp, uint32_t entity_id)
{
	for (unsigned int i = 0; i < tp->t.num_links; i++) {
		const struct media_v2_link *l = &tp->links[i];
		char path[64], line[256], dev[256] = "";
		FILE *f;

		if ((l->flags & MEDIA_LNK_FL_LINK_TYPE) !=
		    MEDIA_LNK_FL_INTERFACE_LINK || l->sink_id != entity_id)
			continue;

		for (unsigned int j = 0; j < tp->t.num_interfaces; j++) {
			const struct media_v2_interface *in = &tp->ifs[j];

			if (in->id != l->source_id)
				continue;

			snprintf(path, sizeof(path), "/sys/dev/char/%u:%u/uevent",
				 in->devnode.major, in->devnode.minor);
			f = fopen(path, "r");
			if (!f)
				return -1;
			while (fgets(line, sizeof(line), f))
				if (!strncmp(line, "DEVNAME=", 8)) {
					line[strcspn(line, "\n")] = '\0';
					snprintf(dev, sizeof(dev), "/dev/%s",
						 line + 8);
				}
			fclose(f);
			if (!dev[0])
				return -1;

			return open(dev, O_RDWR | O_CLOEXEC);
		}
	}

	errno = ENOENT;
	return -1;
}

static int setup_link(struct topo *tp, uint32_t src_ent, uint16_t src_pad,
		      uint32_t sink_ent, uint16_t sink_pad, bool enable)
{
	struct media_link_desc ld = { 0 };

	ld.source.entity = src_ent;
	ld.source.index = src_pad;
	ld.sink.entity = sink_ent;
	ld.sink.index = sink_pad;
	ld.flags = enable ? MEDIA_LNK_FL_ENABLED : 0;

	return xioctl(tp->fd, MEDIA_IOC_SETUP_LINK, &ld);
}

/*
 * Enable src:src_pad -> sink:sink_pad, disabling other enabled links on
 * either pad first (a CSID sink takes one PHY, a PHY feeds one CSID).
 */
static int route(struct topo *tp, const struct media_v2_entity *src,
		 uint16_t src_pad, const struct media_v2_entity *sink,
		 uint16_t sink_pad)
{
	for (unsigned int i = 0; i < tp->t.num_links; i++) {
		const struct media_v2_link *l = &tp->links[i];
		const struct media_v2_pad *ps, *pk;
		bool on_src, on_sink;

		if ((l->flags & MEDIA_LNK_FL_LINK_TYPE) !=
		    MEDIA_LNK_FL_DATA_LINK ||
		    !(l->flags & MEDIA_LNK_FL_ENABLED) ||
		    (l->flags & MEDIA_LNK_FL_IMMUTABLE))
			continue;

		ps = topo_pad(tp, l->source_id);
		pk = topo_pad(tp, l->sink_id);
		if (!ps || !pk)
			continue;

		on_src = ps->entity_id == src->id && ps->index == src_pad;
		on_sink = pk->entity_id == sink->id && pk->index == sink_pad;
		if (on_src && on_sink)
			return 0;
		if (on_src || on_sink)
			setup_link(tp, ps->entity_id, ps->index, pk->entity_id,
				   pk->index, false);
	}

	return setup_link(tp, src->id, src_pad, sink->id, sink_pad, true);
}

static int find_media(void)
{
	char path[32];

	for (int i = 0; i < 16; i++) {
		struct media_device_info info;
		int fd;

		snprintf(path, sizeof(path), "/dev/media%d", i);
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!xioctl(fd, MEDIA_IOC_DEVICE_INFO, &info) &&
		    !strcmp(info.driver, "qcom-camss"))
			return fd;
		close(fd);
	}

	errno = ENODEV;
	return -1;
}

/* ------------------------------------------------------------------ */
/* V4L2 helpers                                                       */

static int subdev_fmt(int fd, unsigned int pad, unsigned int w,
		      unsigned int h, uint32_t *code)
{
	struct v4l2_subdev_format f = {
		.which = V4L2_SUBDEV_FORMAT_ACTIVE,
		.pad = pad,
	};

	if (xioctl(fd, VIDIOC_SUBDEV_G_FMT, &f) < 0)
		return -1;
	f.format.width = w;
	f.format.height = h;
	if (*code)
		f.format.code = *code;
	if (xioctl(fd, VIDIOC_SUBDEV_S_FMT, &f) < 0)
		return -1;
	*code = f.format.code;
	/* Verify ACTIVE state, not just the S_FMT response. */
	if (xioctl(fd, VIDIOC_SUBDEV_G_FMT, &f) < 0)
		return -1;
	if (f.format.width != w || f.format.height != h || f.format.code != *code) {
		errno = EPIPE;
		return -1;
	}
	return 0;
}

static int ctrl_set(int fd, uint32_t id, int val)
{
	struct v4l2_control c = { .id = id, .value = val };

	return xioctl(fd, VIDIOC_S_CTRL, &c);
}

static int ctrl_range(int fd, uint32_t id, int *min, int *max)
{
	struct v4l2_queryctrl q = { .id = id };

	if (xioctl(fd, VIDIOC_QUERYCTRL, &q) < 0)
		return -1;
	*min = q.minimum;
	*max = q.maximum;

	return 0;
}

static int open_loopback(const char *label)
{
	DIR *d = opendir("/sys/class/video4linux");
	struct dirent *e;
	int fd = -1;

	if (!d)
		return -1;

	while ((e = readdir(d))) {
		char path[300], name[64] = "";
		FILE *f;

		if (strncmp(e->d_name, "video", 5))
			continue;
		snprintf(path, sizeof(path), "/sys/class/video4linux/%s/name",
			 e->d_name);
		f = fopen(path, "r");
		if (!f)
			continue;
		if (fgets(name, sizeof(name), f))
			name[strcspn(name, "\n")] = '\0';
		fclose(f);
		if (strcmp(name, label))
			continue;

		snprintf(path, sizeof(path), "/dev/%s", e->d_name);
		fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
		break;
	}
	closedir(d);

	return fd;
}

/* make udev (and so PipeWire) look at the device again */
static void loopback_uevent(int fd)
{
	struct stat st;
	char path[64];
	int ufd;

	if (fstat(fd, &st) < 0)
		return;
	snprintf(path, sizeof(path), "/sys/dev/char/%u:%u/uevent",
		 major(st.st_rdev), minor(st.st_rdev));
	ufd = open(path, O_WRONLY | O_CLOEXEC);
	if (ufd < 0)
		return;
	if (write(ufd, "change", 6) != 6)
		logmsg(NULL, "camera change uevent failed: %s", strerror(errno));
	close(ufd);
}

static size_t frame_size(const struct cam_cfg *cfg)
{
	size_t pixels = (size_t)cfg->out_w * cfg->out_h;
	return output_bgrx ? pixels * 4 : pixels * 3 / 2;
}

static int loopback_write_frame(struct cam *c, const uint8_t *frame)
{
	size_t size = frame_size(c->cfg);
	ssize_t written = write(c->loop_fd, frame, size);

	if (written < 0)
		return -1;
	if ((size_t)written != size) {
		logmsg(c, "short loopback frame: wrote %zd of %zu bytes", written, size);
		errno = EIO;
		return -1;
	}
	return 0;
}

static int loopback_black(struct cam *c)
{
	const struct cam_cfg *cfg = c->cfg;
	size_t size = frame_size(cfg), luma = (size_t)cfg->out_w * cfg->out_h;
	uint8_t *black = malloc(size);
	int ret;

	if (!black)
		return -1;
	memset(black, 0, size);
	if (!output_bgrx)
		memset(black + luma, 128, size - luma);
	ret = loopback_write_frame(c, black);
	free(black);

	return ret;
}

static int loopback_init(struct cam *c)
{
	const struct cam_cfg *cfg = c->cfg;
	struct v4l2_format f = { .type = V4L2_BUF_TYPE_VIDEO_OUTPUT };
	struct v4l2_streamparm p = { .type = V4L2_BUF_TYPE_VIDEO_OUTPUT };
	struct v4l2_event_subscription sub = {
		.type = V4L2_EVENT_PRI_CLIENT_USAGE,
	};
	size_t size = frame_size(cfg);

	c->loop_fd = open_loopback(cfg->label);
	if (c->loop_fd < 0) {
		logmsg(c, "no v4l2loopback device \"%s\"", cfg->label);
		return -1;
	}

	f.fmt.pix.width = cfg->out_w;
	f.fmt.pix.height = cfg->out_h;
	f.fmt.pix.pixelformat = output_bgrx ? V4L2_PIX_FMT_XBGR32 : V4L2_PIX_FMT_NV12;
	f.fmt.pix.field = V4L2_FIELD_NONE;
	f.fmt.pix.bytesperline = cfg->out_w * (output_bgrx ? 4 : 1);
	f.fmt.pix.sizeimage = size;
	/* full range BT.601 YCbCr, sRGB transfer */
	f.fmt.pix.colorspace = output_bgrx ? V4L2_COLORSPACE_SRGB : V4L2_COLORSPACE_JPEG;
	if (xioctl(c->loop_fd, VIDIOC_S_FMT, &f) < 0 ||
	    xioctl(c->loop_fd, VIDIOC_G_FMT, &f) < 0) {
		logmsg(c, "loopback format: %s", strerror(errno));
		return -1;
	}
	if (f.fmt.pix.pixelformat != (output_bgrx ? V4L2_PIX_FMT_XBGR32 : V4L2_PIX_FMT_NV12) ||
	    f.fmt.pix.width != cfg->out_w || f.fmt.pix.height != cfg->out_h ||
	    f.fmt.pix.bytesperline != cfg->out_w * (output_bgrx ? 4 : 1) ||
	    f.fmt.pix.sizeimage != size) {
		logmsg(c, "loopback did not accept tightly packed %s %ux%u",
		       output_bgrx ? "BGRx" : "NV12", cfg->out_w, cfg->out_h);
		return -1;
	}

	p.parm.output.timeperframe.numerator = 1;
	p.parm.output.timeperframe.denominator = (unsigned int)cfg->fps;
	xioctl(c->loop_fd, VIDIOC_S_PARM, &p);

	if (xioctl(c->loop_fd, VIDIOC_SUBSCRIBE_EVENT, &sub) < 0) {
		logmsg(c, "loopback client usage events: %s", strerror(errno));
		return -1;
	}

	/* one black frame: the device turns into a capture device */
	if (loopback_black(c) < 0)
		logmsg(c, "loopback write: %s", strerror(errno));

	loopback_uevent(c->loop_fd);

	return 0;
}

/* ------------------------------------------------------------------ */
/* camera pipeline                                                    */

static void apply_exposure(struct cam *c)
{
	ctrl_set(c->sensor_fd, V4L2_CID_EXPOSURE, c->exposure);
	ctrl_set(c->sensor_fd, V4L2_CID_ANALOGUE_GAIN, c->again);
	ctrl_set(c->vfe_fd, V4L2_CID_DIGITAL_GAIN, c->dgain);
}

static void apply_wb(struct cam *c)
{
	ctrl_set(c->vfe_fd, V4L2_CID_RED_BALANCE,
		 (int)lround(c->rgain * WB_UNITY));
	ctrl_set(c->vfe_fd, V4L2_CID_BLUE_BALANCE,
		 (int)lround(c->bgain * WB_UNITY));
}

#include "photo.h"

static void cam_stop(struct cam *c)
{
	if (photo.cam == c) photo_end("ERR camera stopped");
	struct v4l2_requestbuffers rb = {
		.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
		.memory = V4L2_MEMORY_MMAP,
	};
	int type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;

	if (c->vid_fd >= 0) {
		xioctl(c->vid_fd, VIDIOC_STREAMOFF, &type);
		for (int i = 0; i < NUM_BUFS; i++)
			if (c->bufs[i].ptr)
				munmap(c->bufs[i].ptr, c->bufs[i].len);
		xioctl(c->vid_fd, VIDIOC_REQBUFS, &rb);
		close(c->vid_fd);
	}
	if (c->sensor_fd >= 0)
		close(c->sensor_fd);
	if (c->vcm_fd >= 0)
		close(c->vcm_fd);
	if (c->vfe_fd >= 0)
		close(c->vfe_fd);
	free(c->stage);

	memset(c->bufs, 0, sizeof(c->bufs));
	c->stage = NULL;
	c->nbufs = c->bpl = c->sizeimage = 0;
	c->uv_offset = c->frame_span = 0;
	c->vid_fd = c->sensor_fd = c->vfe_fd = c->vcm_fd = -1;
	if (c->streaming)
		logmsg(c, "stopped after %lu frames", c->frames);
	c->streaming = false;
}

static int cam_start(struct cam *c)
{
	const struct cam_cfg *cfg = c->cfg;
	const struct media_v2_entity *sen, *phy, *csid, *vfe, *vid;
	struct topo tp;
	int mfd, phy_fd = -1, csid_fd = -1, ret = -1;
	uint32_t code = 0, yuv = MEDIA_BUS_FMT_YUYV8_1_5X8;
	struct v4l2_subdev_selection sel = {
		.which = V4L2_SUBDEV_FORMAT_ACTIVE,
		.pad = 0,
		.target = V4L2_SEL_TGT_COMPOSE,
	};
	struct v4l2_format f = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE };
	struct v4l2_requestbuffers rb = {
		.count = NUM_BUFS,
		.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
		.memory = V4L2_MEMORY_MMAP,
	};
	int type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;

	c->vid_fd = c->sensor_fd = c->vfe_fd = c->vcm_fd = -1;

	mfd = find_media();
	if (mfd < 0) {
		logmsg(c, "no CAMSS media device");
		return -1;
	}
	if (topo_load(&tp, mfd) < 0) {
		logmsg(c, "media topology: %s", strerror(errno));
		close(mfd);
		return -1;
	}

	sen = topo_entity(&tp, cfg->sensor);
	phy = topo_entity(&tp, cfg->phy);
	csid = topo_entity(&tp, cfg->csid);
	vfe = topo_entity(&tp, cfg->vfe);
	vid = topo_entity(&tp, cfg->video);
	if (!sen || !phy || !csid || !vfe || !vid) {
		logmsg(c, "media entities missing");
		goto out;
	}

	c->sensor_fd = topo_open_devnode(&tp, sen->id);
	phy_fd = topo_open_devnode(&tp, phy->id);
	csid_fd = topo_open_devnode(&tp, csid->id);
	c->vfe_fd = topo_open_devnode(&tp, vfe->id);
	c->vid_fd = topo_open_devnode(&tp, vid->id);
	if (c->vid_fd >= 0)
		fcntl(c->vid_fd, F_SETFL, O_NONBLOCK);
	if (c->sensor_fd < 0 || phy_fd < 0 || csid_fd < 0 || c->vfe_fd < 0 ||
	    c->vid_fd < 0) {
		logmsg(c, "cannot open device nodes: %s", strerror(errno));
		goto out;
	}

	/* optional: without the actuator the lens stays where it is */
	c->af_state = AF_OFF;
	if (cfg->vcm) {
		const struct media_v2_entity *lens = topo_entity(&tp, cfg->vcm);

		if (lens)
			c->vcm_fd = topo_open_devnode(&tp, lens->id);
		if (c->vcm_fd >= 0 &&
		    ctrl_range(c->vcm_fd, V4L2_CID_FOCUS_ABSOLUTE,
			       &c->af_min, &c->af_max) == 0) {
			struct v4l2_control ctl = {
				.id = V4L2_CID_FOCUS_ABSOLUTE,
			};

			/* the climb starts where the lens rests */
			if (xioctl(c->vcm_fd, VIDIOC_G_CTRL, &ctl) == 0) {
				c->af_pos = ctl.value;
				c->af_settle = 0;
				c->af_state = AF_WAIT;
			} else {
				logmsg(c, "focus position: %s", strerror(errno));
			}
		}
	}

	if (route(&tp, phy, 1, csid, 0) < 0 || route(&tp, csid, 4, vfe, 0) < 0) {
		logmsg(c, "media links: %s", strerror(errno));
		goto out;
	}

	/* the flips change the Bayer order the sensor reports */
	/* OV32D40 has no flip controls; S5KJN1 needs VFLIP for BGGR. */
	if (!strcmp(cfg->sensor, "s5kjn1") &&
	    (ctrl_set(c->sensor_fd, V4L2_CID_HFLIP, cfg->hflip) < 0 ||
	     ctrl_set(c->sensor_fd, V4L2_CID_VFLIP, cfg->vflip) < 0)) {
		logmsg(c, "sensor flips: %s", strerror(errno));
		goto out;
	}

	if (subdev_fmt(c->sensor_fd, 0, cfg->in_w, cfg->in_h, &code) < 0 ||
	    subdev_fmt(phy_fd, 0, cfg->in_w, cfg->in_h, &code) < 0 ||
	    subdev_fmt(csid_fd, 0, cfg->in_w, cfg->in_h, &code) < 0 ||
	    subdev_fmt(csid_fd, 4, cfg->in_w, cfg->in_h, &code) < 0 ||
	    subdev_fmt(c->vfe_fd, 0, cfg->in_w, cfg->in_h, &code) < 0) {
		logmsg(c, "sensor side formats: %s", strerror(errno));
		goto out;
	}

	sel.r.width = cfg->out_w;
	sel.r.height = cfg->out_h;
	if (xioctl(c->vfe_fd, VIDIOC_SUBDEV_S_SELECTION, &sel) < 0 ||
	    subdev_fmt(c->vfe_fd, 1, cfg->out_w, cfg->out_h, &yuv) < 0) {
		logmsg(c, "ISP output size: %s", strerror(errno));
		goto out;
	}

	f.fmt.pix_mp.width = cfg->out_w;
	f.fmt.pix_mp.height = cfg->out_h;
	f.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
	f.fmt.pix_mp.field = V4L2_FIELD_NONE;
	f.fmt.pix_mp.num_planes = 1;
	if (xioctl(c->vid_fd, VIDIOC_S_FMT, &f) < 0 ||
	    xioctl(c->vid_fd, VIDIOC_G_FMT, &f) < 0) {
		logmsg(c, "video format: %s", strerror(errno));
		goto out;
	}
	if (f.fmt.pix_mp.pixelformat != V4L2_PIX_FMT_NV12 ||
	    f.fmt.pix_mp.num_planes != 1 ||
	    f.fmt.pix_mp.width != cfg->out_w ||
	    f.fmt.pix_mp.height != cfg->out_h ||
	    (cfg->out_w & 1) || (cfg->out_h & 1)) {
		logmsg(c, "capture did not accept single-plane NV12 %ux%u", cfg->out_w,
		       cfg->out_h);
		goto out;
	}
	c->bpl = f.fmt.pix_mp.plane_fmt[0].bytesperline;
	c->sizeimage = f.fmt.pix_mp.plane_fmt[0].sizeimage;
	c->uv_offset = (size_t)c->bpl * cfg->out_h;
	c->frame_span = c->uv_offset + (size_t)(cfg->out_h / 2 - 1) * c->bpl +
			cfg->out_w;
	if (c->bpl < cfg->out_w || c->sizeimage < c->frame_span) {
		logmsg(c, "invalid NV12 layout: stride %u sizeimage %u span %zu", c->bpl,
		       c->sizeimage, c->frame_span);
		goto out;
	}
	if (output_bgrx || c->bpl != cfg->out_w) {
		c->stage = malloc(frame_size(cfg));
		if (!c->stage)
			goto out;
	}

	if (xioctl(c->vid_fd, VIDIOC_REQBUFS, &rb) < 0 || rb.count < 2 ||
	    rb.count > NUM_BUFS) {
		logmsg(c, "REQBUFS: count %u, %s", rb.count, strerror(errno));
		goto out;
	}
	c->nbufs = rb.count;
	for (unsigned int i = 0; i < c->nbufs; i++) {
		struct v4l2_plane pl[VIDEO_MAX_PLANES] = { 0 };
		struct v4l2_buffer b = {
			.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
			.memory = V4L2_MEMORY_MMAP,
			.index = i,
			.length = VIDEO_MAX_PLANES,
			.m.planes = pl,
		};

		if (xioctl(c->vid_fd, VIDIOC_QUERYBUF, &b) < 0)
			goto out;
		if (b.length != 1 || pl[0].length < c->sizeimage) {
			logmsg(c, "invalid capture buffer %u: planes %u length %u", i,
			       b.length, pl[0].length);
			goto out;
		}
		c->bufs[i].len = pl[0].length;
		c->bufs[i].ptr = mmap(NULL, pl[0].length, PROT_READ,
				      MAP_SHARED, c->vid_fd,
				      pl[0].m.mem_offset);
		if (c->bufs[i].ptr == MAP_FAILED) {
			c->bufs[i].ptr = NULL;
			goto out;
		}
		if (xioctl(c->vid_fd, VIDIOC_QBUF, &b) < 0)
			goto out;
	}

	ctrl_range(c->sensor_fd, V4L2_CID_EXPOSURE, &c->exp_min, &c->exp_max);
	ctrl_range(c->sensor_fd, V4L2_CID_ANALOGUE_GAIN, &c->ag_min,
		   &c->ag_max);
	if (!c->have_state) {
		c->exposure = c->exp_max / 2;
		c->again = cfg->again_unit * 2;
		c->dgain = DGAIN_UNITY;
		c->rgain = 1.8;
		c->bgain = 1.6;
		c->have_state = true;
	}
	c->exposure = c->exposure < c->exp_min ? c->exp_min :
		      c->exposure > c->exp_max ? c->exp_max : c->exposure;
	c->again = c->again < c->ag_min ? c->ag_min :
		   c->again > c->ag_max ? c->ag_max : c->again;
	apply_exposure(c);
	apply_wb(c);
	{
		/* S_EXT_CTRLS copies the accepted matrix back to userspace. */
		int32_t matrix[9];
		struct v4l2_ext_control ec = {
			.id = V4L2_CID_CAMSS_CCM,
			.size = sizeof(matrix),
			.p_s32 = matrix,
		};
		struct v4l2_ext_controls ecs = { .count = 1, .controls = &ec };

		memcpy(matrix, cfg->ccm, sizeof(matrix));
		if (xioctl(c->vfe_fd, VIDIOC_S_EXT_CTRLS, &ecs) < 0)
			logmsg(c, "colour matrix: %s", strerror(errno));
	}
	ctrl_set(c->vfe_fd, V4L2_CID_SATURATION, saturation);
	ctrl_set(c->vfe_fd, V4L2_CID_CONTRAST, contrast);

	/* Retain evidence of the actual source/sink contract at STREAMON. */
	{
		struct v4l2_subdev_format source = {
			.which = V4L2_SUBDEV_FORMAT_ACTIVE, .pad = 0,
		};
		struct v4l2_subdev_format sink = source;

		if (xioctl(c->sensor_fd, VIDIOC_SUBDEV_G_FMT, &source) < 0 ||
		    xioctl(phy_fd, VIDIOC_SUBDEV_G_FMT, &sink) < 0) {
			logmsg(c, "link format readback: %s", strerror(errno));
			goto out;
		}
		logmsg(c, "sensor %ux%u 0x%04x -> PHY %ux%u 0x%04x",
		       source.format.width, source.format.height, source.format.code,
		       sink.format.width, sink.format.height, sink.format.code);
		if (source.format.width != cfg->in_w || source.format.height != cfg->in_h ||
		    source.format.code != code || sink.format.width != source.format.width ||
		    sink.format.height != source.format.height || sink.format.code != code) {
			logmsg(c, "ACTIVE formats changed before STREAMON; check other CAMSS owners");
			goto out;
		}
	}

	if (xioctl(c->vid_fd, VIDIOC_STREAMON, &type) < 0) {
		logmsg(c, "STREAMON: %s", strerror(errno));
		goto out;
	}

	c->streaming = true;
	c->frames = 0;
	c->ae_updates = c->ae_stable = c->ae_far = 0;
	c->ae_acquiring = true;
	c->ae_ready = false;
	logmsg(c, "streaming %ux%u -> %ux%u NV12 (bayer 0x%04x)", cfg->in_w,
	       cfg->in_h, cfg->out_w, cfg->out_h, code);
	ret = 0;
out:
	if (phy_fd >= 0)
		close(phy_fd);
	if (csid_fd >= 0)
		close(csid_fd);
	topo_free(&tp);
	close(mfd);
	if (ret < 0)
		cam_stop(c);

	return ret;
}

/* ------------------------------------------------------------------ */
/* auto exposure / white balance                                      */

struct stats {
	double mean;		/* weighted mean luma */
	double bright;		/* fraction of near-white samples */
	double r, g, b;		/* linear sums over mid-tone samples */
	unsigned int n_rgb;
};

static void frame_stats(const struct cam *c, const uint8_t *y,
			const uint8_t *uv, unsigned int stride,
			struct stats *s)
{
	const struct cam_cfg *cfg = c->cfg;
	unsigned int w = cfg->out_w, h = cfg->out_h;
	double sum = 0, wsum = 0;
	unsigned int bright = 0, n = 0;

	memset(s, 0, sizeof(*s));

	for (unsigned int row = h / 32; row < h; row += h / 32) {
		for (unsigned int col = w / 64; col < w; col += w / 64) {
			unsigned int r = row & ~1u, cl = col & ~1u;
			int Y = y[r * stride + cl];
			int U = uv[(r / 2) * stride + cl] - 128;
			int V = uv[(r / 2) * stride + cl + 1] - 128;
			bool centre = row > h / 4 && row < 3 * h / 4 &&
				      col > w / 4 && col < 3 * w / 4;
			double wt = centre ? 2.0 : 1.0;

			sum += wt * Y;
			wsum += wt;
			n++;
			if (Y >= 250)
				bright++;

			if (Y > 24 && Y < 230) {
				int R = Y + (int)lround(1.402 * V);
				int G = Y - (int)lround(0.344 * U + 0.714 * V);
				int B = Y + (int)lround(1.772 * U);

				if (R < 0 || R > 254 || G < 0 || G > 254 ||
				    B < 0 || B > 254)
					continue;
				s->r += srgb_lin[R];
				s->g += srgb_lin[G];
				s->b += srgb_lin[B];
				s->n_rgb++;
			}
		}
	}

	s->mean = sum / wsum;
	s->bright = (double)bright / n;
}

static double clampd(double v, double lo, double hi)
{
	return v < lo ? lo : v > hi ? hi : v;
}

static void control_update(struct cam *c, const struct stats *s)
{
	const struct cam_cfg *cfg = c->cfg;
	double k, total, rem, target, error, lo, hi;
	int exp, ag;
	bool acquire = c->ae_acquiring;
	int old_exp = c->exposure, old_ag = c->again, old_dg = c->dgain;

	/* Acquisition uses the current valid frame, not stale filtered luma. */
	if (!c->ae_updates || acquire) {
		c->ae_mean = s->mean;
		c->ae_bright = s->bright;
	} else {
		c->ae_mean += 0.25 * (s->mean - c->ae_mean);
		c->ae_bright += 0.25 * (s->bright - c->ae_bright);
	}
	/* A hard clipping cutoff alternated between gain-up and gain-down.
	 * Reduce the luma target continuously instead of reversing the step.
	 */
	target = fmax(AE_TARGET * 0.65, AE_TARGET / (1.0 + 2.0 * c->ae_bright));
	error = log(target / fmax(c->ae_mean, 1.0));
	if (fabs(error) > 0.05) {
		/* Only startup may take a larger step. The existing highlight target
		 * remains, and the startup upper bound falls with clipped pixels.
		 */
		lo = acquire ? 0.75 : 0.94;
		hi = acquire ? clampd(2.0 / (1.0 + 4.0 * s->bright), 1.06, 2.0) : 1.06;
		k = clampd(pow(target / fmax(c->ae_mean, 1.0), 2.2 * 0.3), lo, hi);
		total = (double)c->exposure * c->again / cfg->again_unit *
			c->dgain / DGAIN_UNITY * k;

		exp = (int)fmin(total, c->exp_max);
		if (exp < c->exp_min)
			exp = c->exp_min;
		rem = total / exp;

		ag = (int)floor(rem * cfg->again_unit);
		ag = ag < c->ag_min ? c->ag_min : ag > c->ag_max ? c->ag_max : ag;
		rem /= (double)ag / cfg->again_unit;

		c->exposure = exp;
		c->again = ag;
		c->dgain = (int)clampd(lround(rem * DGAIN_UNITY), DGAIN_UNITY,
				       DGAIN_MAX);
		apply_exposure(c);
	}

	/* A bounded acquisition cannot declare readiness merely on timeout.
	 * A target match, or unchanged controls at their limits, must persist
	 * for two observations before the rear lens scan is allowed.
	 */
	if (fabs(error) <= 0.10 ||
	    (c->ae_mean > 1.0 && old_exp == c->exposure &&
	     old_ag == c->again && old_dg == c->dgain))
		c->ae_stable++;
	else
		c->ae_stable = 0;
	c->ae_ready = c->ae_stable >= 2;
	if (c->ae_updates < AE_STARTUP_UPDATES)
		c->ae_updates++;
	if (c->ae_ready || c->ae_updates >= AE_STARTUP_UPDATES)
		c->ae_acquiring = false;

	/* A scene change leaves a large error that the steady rate needs
	 * seconds to close: acquire again once the filtered error stays large
	 * for two updates. Controls held at their limits cannot do better.
	 */
	if (!c->ae_acquiring && fabs(error) > AE_REACQUIRE &&
	    (old_exp != c->exposure || old_ag != c->again ||
	     old_dg != c->dgain)) {
		if (++c->ae_far >= 2) {
			c->ae_acquiring = true;
			c->ae_ready = false;
			c->ae_updates = c->ae_stable = c->ae_far = 0;
		}
	} else {
		c->ae_far = 0;
	}

	/* gray world white balance on mid tones */
	if (s->n_rgb > 200 && s->r > 0 && s->b > 0) {
		double rr = s->g / s->r, bb = s->g / s->b;

		if (fabs(log(rr)) > 0.02 || fabs(log(bb)) > 0.02) {
			c->rgain = clampd(c->rgain * pow(rr, 0.4), 0.5, 4.0);
			c->bgain = clampd(c->bgain * pow(bb, 0.4), 0.5, 4.0);
			apply_wb(c);
		}
	}

	if (c->frames % (AE_PERIOD * 30) == 0)
		logmsg(c, "mean %.0f bright %.2f ae %.0f target %.0f exp %d again %d dgain %d wb r %.2f b %.2f",
		       s->mean, s->bright, c->ae_mean, target, c->exposure, c->again, c->dgain,
		       c->rgain, c->bgain);
}

/* ------------------------------------------------------------------ */
/* contrast-detect autofocus                                          */

/*
 * Hill climb in the manner of the stock fine search: start where the lens
 * is, step towards the larger part of the range, keep going while the
 * sharpness rises and stop once it has fallen twice below the peak (or
 * turn round once if the first direction only ever fell). The peak is
 * then refined by a parabola through the best sample and its neighbours,
 * so no second, finer sweep is needed.
 */
#define AF_START	15	/* frames for auto exposure to settle first */
#define AF_SETTLE	2	/* frames to drop after a lens move (default) */
#define AF_STEPS	16	/* climb step: the range in this many steps */
#define AF_DROP		0.85	/* below this share of the peak: past it */
#define AF_RISE		1.6	/* a peak rises this far above the scan floor */
#define AF_LOST		0.6	/* relock below this share of the lock sharpness */
#define AF_LOST_FRAMES	20

/* horizontal gradient energy of the centre third, per unit of luma */
static double af_sharpness(const struct cam *c, const uint8_t *y,
			   unsigned int stride)
{
	unsigned int w = c->cfg->out_w, h = c->cfg->out_h;
	uint64_t grad = 0, luma = 0;

	for (unsigned int r = h / 3; r < 2 * h / 3; r += 4) {
		const uint8_t *p = y + (size_t)r * stride;

		for (unsigned int x = w / 3; x < 2 * w / 3; x += 2) {
			int d = p[x + 2] - p[x];

			grad += (uint64_t)(d * d);
			luma += p[x];
		}
	}

	return luma ? (double)grad / luma : 0;
}

static int af_clamp(const struct cam *c, int pos)
{
	return pos < c->af_min ? c->af_min : pos > c->af_max ? c->af_max : pos;
}

static int af_move(struct cam *c, int pos)
{
	pos = af_clamp(c, pos);
	if (pos != c->af_pos) {
		if (ctrl_set(c->vcm_fd, V4L2_CID_FOCUS_ABSOLUTE, pos) < 0) {
			logmsg(c, "focus control %d: %s", pos, strerror(errno));
			c->af_state = AF_OFF;
			return -1;
		}
		c->af_pos = pos;
		c->af_settle = af_settle_frames;
	}
	return 0;
}

static void af_scan(struct cam *c)
{
	int mid = (c->af_min + c->af_max) / 2;

	c->af_step = (c->af_max - c->af_min) / AF_STEPS;
	if (c->af_step < 1)
		c->af_step = 1;
	c->af_dir = c->af_pos <= mid ? 1 : -1;
	c->af_origin = c->af_pos;
	c->af_reversed = false;
	c->af_drops = 0;
	c->af_n = 0;
	c->af_best = -1;
	c->af_floor = -1;
	c->af_state = AF_SCAN;
	/* the first sample is where the lens already is */
	af_move(c, c->af_pos);
}

/* vertex of the parabola through the best sample and its neighbours */
static int af_peak(const struct cam *c)
{
	int b = c->af_best_pos, l = -1, r = -1;
	double x0, x1 = b, x2, y0, y1 = c->af_best, y2, d;

	for (int i = 0; i < c->af_n; i++) {
		if (c->af_hist_pos[i] < b &&
		    (l < 0 || c->af_hist_pos[i] > c->af_hist_pos[l]))
			l = i;
		if (c->af_hist_pos[i] > b &&
		    (r < 0 || c->af_hist_pos[i] < c->af_hist_pos[r]))
			r = i;
	}
	if (l < 0 || r < 0)
		return b;
	x0 = c->af_hist_pos[l];
	y0 = c->af_hist_val[l];
	x2 = c->af_hist_pos[r];
	y2 = c->af_hist_val[r];
	d = (x0 - x1) * (x0 - x2) * (x1 - x2);
	if (d == 0)
		return b;
	{
		double A = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / d;
		double B = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) +
			    x0 * x0 * (y1 - y2)) / d;
		double v;

		if (A >= 0)	/* not a maximum */
			return b;
		v = -B / (2 * A);
		if (v < x0)
			v = x0;
		if (v > x2)
			v = x2;
		return (int)lround(v);
	}
}

/* one sharpness sample at the current lens position */
static void af_feed(struct cam *c, double sh)
{
	int next;

	switch (c->af_state) {
	case AF_LOCKED:
		c->af_lost = sh < AF_LOST * c->af_lock ? c->af_lost + 1 : 0;
		if (c->af_lost >= AF_LOST_FRAMES)
			af_scan(c);
		return;
	case AF_VERIFY:
		/* a parabola that missed: fall back to the best sample */
		if (sh < AF_DROP * c->af_best && c->af_pos != c->af_best_pos) {
			af_move(c, c->af_best_pos);
			return;
		}
		c->af_lock = sh;
		c->af_lost = 0;
		c->af_state = AF_LOCKED;
		logmsg(c, "focus %d after %d steps (sharpness %.2f)",
		       c->af_pos, c->af_n, sh);
		return;
	case AF_SCAN:
	case AF_FINE:
		break;
	default:
		return;
	}

	if (c->af_n < AF_HIST) {
		c->af_hist_pos[c->af_n] = c->af_pos;
		c->af_hist_val[c->af_n] = sh;
		c->af_n++;
	}
	if (sh > c->af_best) {
		c->af_best = sh;
		c->af_best_pos = c->af_pos;
		c->af_drops = 0;
	} else if (sh < AF_DROP * c->af_best) {
		c->af_drops++;
	}
	if (c->af_floor < 0 || sh < c->af_floor)
		c->af_floor = sh;

	if (c->af_state == AF_FINE) {
		/* three samples around the coarse estimate */
		for (int i = 0; i < 3; i++) {
			if (c->af_fine[i] < 0)
				continue;
			next = c->af_fine[i];
			c->af_fine[i] = -1;
			if (af_move(c, next) < 0)
				return;
			if (c->af_pos != next || c->af_settle)
				return;
			/* already there (clamped): sampled */
		}
		if (af_move(c, af_peak(c)) < 0)
			return;
		c->af_state = AF_VERIFY;
		return;
	}

	next = c->af_pos + c->af_dir * c->af_step;
	if (next == af_clamp(c, next) && c->af_n < AF_HIST &&
	    !(c->af_drops >= 2 && c->af_best > AF_RISE * c->af_floor)) {
		af_move(c, next);
		return;
	}
	/*
	 * Past a peak, or at the end of the range. Turn round once if the
	 * first direction never rose above the start (the peak lies the
	 * other way) or found nothing but a flat curve.
	 */
	if (!c->af_reversed && (c->af_best_pos == c->af_origin ||
				c->af_best <= AF_RISE * c->af_floor)) {
		next = c->af_origin - c->af_dir * c->af_step;
		c->af_dir = -c->af_dir;
		c->af_reversed = true;
		c->af_drops = 0;
		if (next == af_clamp(c, next)) {
			af_move(c, next);
			return;
		}
	}
	/* refine: a parabola, then a fine pass at a quarter step around it */
	next = af_peak(c);
	c->af_fine[0] = af_clamp(c, next - c->af_step / 4);
	c->af_fine[1] = af_clamp(c, next + c->af_step / 4);
	c->af_fine[2] = -1;
	c->af_state = AF_FINE;
	af_move(c, next);
}

static void af_update(struct cam *c, const uint8_t *y, unsigned int stride)
{
	if (c->af_state == AF_OFF)
		return;
	if (c->af_state == AF_WAIT) {
		if (c->frames >= AF_START && c->ae_ready)
			af_scan(c);
		return;
	}
	if (c->af_settle > 0) {
		c->af_settle--;
		return;
	}
	if (!c->ae_ready)
		return;
	af_feed(c, af_sharpness(c, y, stride));
}

static void cam_frame(struct cam *c)
{
	const struct cam_cfg *cfg = c->cfg;
	struct v4l2_plane pl[VIDEO_MAX_PLANES] = { 0 };
	struct v4l2_buffer b = {
		.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE,
		.memory = V4L2_MEMORY_MMAP,
		.length = VIDEO_MAX_PLANES,
		.m.planes = pl,
	};
	const uint8_t *y, *uv, *out;

	if (xioctl(c->vid_fd, VIDIOC_DQBUF, &b) < 0) {
		if (errno != EAGAIN)
			logmsg(c, "DQBUF: %s", strerror(errno));
		return;
	}

	if (!(b.flags & V4L2_BUF_FLAG_ERROR)) {
		const struct buf *mapped;
		size_t offset = pl[0].data_offset, used = pl[0].bytesused;

		if (b.index >= c->nbufs || b.length != 1) {
			logmsg(c, "invalid capture frame: index %u planes %u", b.index,
			       b.length);
			goto requeue;
		}
		mapped = &c->bufs[b.index];
		if (!mapped->ptr || pl[0].length > mapped->len ||
		    pl[0].length < c->sizeimage || used > pl[0].length ||
		    offset > used || c->frame_span > used - offset) {
			logmsg(c, "incomplete capture frame: sequence %u index %u used %zu "
			       "offset %zu length %u mapped %zu span %zu", b.sequence,
			       b.index, used, offset, pl[0].length, mapped->len, c->frame_span);
			goto requeue;
		}
		y = (const uint8_t *)mapped->ptr + offset;
		uv = y + c->uv_offset;

		if (++c->frames % AE_PERIOD == 0) {
			struct stats s;

			frame_stats(c, y, uv, c->bpl, &s);
			control_update(c, &s);
		}
		af_update(c, y, c->bpl);
		photo_frame(c, y, uv, b.sequence);

		out = y;
		if (output_bgrx) {
			nv12_to_bgrx(y, uv, c->bpl, c->stage, cfg->out_w, cfg->out_h);
			out = c->stage;
		} else if (c->stage) {
			uint8_t *d = c->stage;

			for (unsigned int r = 0; r < cfg->out_h * 3 / 2; r++)
				memcpy(d + (size_t)r * cfg->out_w,
				       y + (size_t)r * c->bpl, cfg->out_w);
			out = c->stage;
		}
		if (loopback_write_frame(c, out) < 0)
			logmsg(c, "loopback write: %s", strerror(errno));
	}

requeue:
	if (xioctl(c->vid_fd, VIDIOC_QBUF, &b) < 0)
		logmsg(c, "QBUF after frame %u: %s", b.sequence, strerror(errno));
}

static void loopback_event(struct cam *c)
{
	struct v4l2_event ev;

	while (!xioctl(c->loop_fd, VIDIOC_DQEVENT, &ev)) {
		const struct v4l2_event_client_usage *u = (const void *)&ev.u;

		if (ev.type != V4L2_EVENT_PRI_CLIENT_USAGE)
			continue;
		if (u->count && !c->streaming) {
			/* a current frame until the camera delivers */
			loopback_black(c);
			cam_start(c);
		}
		else if (!u->count && c->streaming)
			cam_stop(c);
	}
}

/*
 * systemd readiness (Type=notify): the loopback devices are capture
 * devices from here on, so a session started afterwards finds them.
 */
static void notify_ready(void)
{
	const char *path = getenv("NOTIFY_SOCKET");
	struct sockaddr_un sa = { .sun_family = AF_UNIX };
	static const char msg[] = "READY=1";
	socklen_t len;
	int fd;

	if (!path || (path[0] != '/' && path[0] != '@') ||
	    strlen(path) >= sizeof(sa.sun_path))
		return;
	strcpy(sa.sun_path, path);
	if (path[0] == '@')
		sa.sun_path[0] = '\0';
	len = offsetof(struct sockaddr_un, sun_path) + strlen(path);
	fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return;
	sendto(fd, msg, sizeof(msg) - 1, 0, (struct sockaddr *)&sa, len);
	close(fd);
}

static void on_signal(int sig)
{
	(void)sig;
	quit = 1;
}

int main(void)
{
	struct sigaction sa = { .sa_handler = on_signal };
	unsigned int ncams = 0;
	const char *output = getenv("PIANO_CAMERA_OUTPUT");

	if (output) {
		if (!strcmp(output, "nv12"))
			output_bgrx = false;
		else if (!strcmp(output, "bgrx"))
			output_bgrx = true;
		else {
			logmsg(NULL, "PIANO_CAMERA_OUTPUT must be nv12 or bgrx");
			return EXIT_FAILURE;
		}
	}
	logmsg(NULL, "loopback output: %s", output_bgrx ? "BGRx" : "NV12");

	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);
	setvbuf(stderr, NULL, _IOLBF, 0);

	if (getenv("PIANO_CAMERA_SATURATION"))
		saturation = atoi(getenv("PIANO_CAMERA_SATURATION")) *
			     SAT_UNITY / 100;
	if (getenv("PIANO_CAMERA_CONTRAST"))
		contrast = atoi(getenv("PIANO_CAMERA_CONTRAST"));
	af_settle_frames = AF_SETTLE;
	if (getenv("PIANO_CAMERA_AF_SETTLE"))
		af_settle_frames = atoi(getenv("PIANO_CAMERA_AF_SETTLE"));

	for (int i = 0; i < 256; i++) {
		double v = i / 255.0;

		srgb_lin[i] = v <= 0.04045 ? v / 12.92 :
			      pow((v + 0.055) / 1.055, 2.4);
	}

	for (unsigned int i = 0; i < NUM_CAMS; i++) {
		cams[i].cfg = &cam_cfgs[i];
		cams[i].vid_fd = cams[i].sensor_fd = cams[i].vfe_fd =
			cams[i].vcm_fd = -1;
		if (loopback_init(&cams[i]) == 0)
			ncams++;
		else if (cams[i].loop_fd >= 0) {
			close(cams[i].loop_fd);
			cams[i].loop_fd = -1;
		}
	}
	if (!ncams) {
		logmsg(NULL, "no camera outputs");
		return 1;
	}
	logmsg(NULL, "ready, %u camera(s)", ncams);
	photo.listener = photo_listen();
	if (photo.listener < 0)
		logmsg(NULL, "photo socket unavailable: %s", strerror(errno));
	notify_ready();

	while (!quit) {
		struct pollfd pfd[2 * NUM_CAMS + 2];
		struct cam *owner[2 * NUM_CAMS];
		bool is_vid[2 * NUM_CAMS];
		unsigned int n = 0;

		for (unsigned int i = 0; i < NUM_CAMS; i++) {
			struct cam *c = &cams[i];

			if (c->loop_fd < 0)
				continue;
			pfd[n] = (struct pollfd){ .fd = c->loop_fd,
						  .events = POLLPRI };
			owner[n] = c;
			is_vid[n++] = false;
			if (c->streaming) {
				pfd[n] = (struct pollfd){ .fd = c->vid_fd,
							  .events = POLLIN };
				owner[n] = c;
				is_vid[n++] = true;
			}
		}

		pfd[n] = (struct pollfd){ .fd = photo.listener, .events = POLLIN };
		pfd[n + 1] = (struct pollfd){ .fd = photo.client, .events = POLLIN };
		if (poll(pfd, n + 2, 1000) < 0) {
			if (errno == EINTR)
				continue;
			logmsg(NULL, "poll: %s", strerror(errno));
			break;
		}

		/* Disconnect/cancel before consuming a ready capture buffer. */
		photo_events(pfd[n + 1].revents);
		if (pfd[n].revents & POLLIN) photo_accept();
		for (unsigned int i = 0; i < n; i++) {
			if (!pfd[i].revents)
				continue;
			if (is_vid[i]) {
				if (owner[i]->streaming)
					cam_frame(owner[i]);
			} else if (pfd[i].revents & POLLPRI) {
				loopback_event(owner[i]);
			}
		}
	}

	for (unsigned int i = 0; i < NUM_CAMS; i++)
		cam_stop(&cams[i]);
	photo_end(NULL);
	if (photo.listener >= 0) close(photo.listener);
	logmsg(NULL, "exit");

	return 0;
}
