// SPDX-License-Identifier: MIT
/* Link against the real Mesa device table; this does not access a GPU. */
#include "freedreno_dev_info.h"
#include <stdio.h>
#include <string.h>

int main(void)
{
	const unsigned bins[] = { 0, 190, 221, 232, 242, 0xffff };
	for (unsigned i = 0; i < sizeof(bins) / sizeof(bins[0]); i++) {
		struct fd_dev_id id = { .chip_id = ((uint64_t)bins[i] << 32) | 0x44050001 };
		const struct fd_dev_info *info = fd_dev_info_raw(&id);
		if (!info || info->chip != 8 || strcmp(fd_dev_name(&id), "Adreno (TM) 830v1")) {
			fprintf(stderr, "A830 revision 1 not matched for speed bin %u\n", bins[i]);
			return 1;
		}
	}
	struct fd_dev_id unknown = { .chip_id = 0xdeadbeef };
	if (fd_dev_info_raw(&unknown))
		return 1;
	puts("A830 revision 1: zero, known speed bins and wildcard match; unknown ID rejected");
	return 0;
}
