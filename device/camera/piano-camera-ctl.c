// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define CONTROL_PATH "/run/piano-camerad/control.sock"

static void usage(FILE *stream, const char *program)
{
	fprintf(stream, "Usage: %s caps|get rear|front\n"
			"       %s set rear|front ae|awb|af auto|manual\n"
			"       %s set rear|front exposure|analog-gain|digital-gain|red-balance|blue-balance|focus INTEGER\n"
			"       %s set rear|front exposure-time-ns NANOSECONDS\n"
			"The camera must already be streaming. Exposure time is quantized to sensor lines; caps reports the active mode and live ranges. Other values use driver-native units.\n",
			program, program, program, program);
}

int main(int argc, char **argv)
{
	struct sockaddr_un sa = { .sun_family = AF_UNIX, .sun_path = CONTROL_PATH };
	char packet[256], reply[8192];
	size_t used = 0;
	int fd;
	ssize_t n;

	if (argc == 2 && !strcmp(argv[1], "--help")) {
		usage(stdout, argv[0]);
		return 0;
	}

	if (argc != 3 && argc != 5) {
		usage(stderr, argv[0]);
		return 2;
	}
	for (int i = 1; i < argc; i++) {
		if (!argv[i][0] || strpbrk(argv[i], " \t\r\n"))
			return 2;
		int bytes = snprintf(packet + used, sizeof(packet) - used, "%s%s", i == 1 ? "" : " ", argv[i]);
		if (bytes < 0 || (size_t)bytes >= sizeof(packet) - used)
			return 2;
		used += (size_t)bytes;
	}
	fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
	if (fd < 0 || connect(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
		perror("camera control connect");
		if (fd >= 0)
			close(fd);
		return 1;
	}
	if (send(fd, packet, used, MSG_NOSIGNAL) != (ssize_t)used) {
		perror("camera control send");
		close(fd);
		return 1;
	}
	struct pollfd pfd = { .fd = fd, .events = POLLIN };
	int ready;
	do { ready = poll(&pfd, 1, 3000); } while (ready < 0 && errno == EINTR);
	if (ready <= 0) {
		fprintf(stderr, "No control reply; query current state before retrying a write.\n");
		close(fd);
		return 1;
	}
	do { n = recv(fd, reply, sizeof(reply) - 1, MSG_TRUNC); } while (n < 0 && errno == EINTR);
	close(fd);
	if (n <= 0 || n >= (ssize_t)sizeof(reply) || memchr(reply, 0, (size_t)n)) {
		fprintf(stderr, "Invalid or missing control reply.\n");
		return 1;
	}
	reply[n] = 0;
	fputs(reply, stdout);
	return strstr(reply, "\"ok\":true") ? 0 : 1;
}
