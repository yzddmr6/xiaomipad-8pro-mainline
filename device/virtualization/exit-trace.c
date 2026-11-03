/* SPDX-License-Identifier: MIT */
/* Gunyah exit diagnostic interposer. Never reads guest RAM; does not alter requests.
 * Bionic exports __errno(), glibc exports __errno_location(). No NDK dependency:
 * the only ABI types below are LP64 scalar types and POSIX timespec.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
extern void *dlsym(void *, const char *);
extern int snprintf(char *, size_t, const char *, ...);
extern long write(int, const void *, size_t);
extern int getpid(void), gettid(void);
extern int *__errno(void);
struct ts { long sec, nsec; };
extern int clock_gettime(int, struct ts *);
static int (*real_ioctl)(int, unsigned long, ...);
static void *(*real_mmap)(void *, size_t, int, int, int, long);
static void *(*real_mmap64)(void *, size_t, int, int, int, long);
struct vcpu { int fd; void *run; unsigned long calls; };
static struct vcpu vcpus[32];
static unsigned nvcpus;
__attribute__((constructor)) static void setup(void) {
    real_ioctl = dlsym((void *)-1L, "ioctl");
    real_mmap = dlsym((void *)-1L, "mmap");
    real_mmap64 = dlsym((void *)-1L, "mmap64");
    write(2, "GH-IOCTL interposer-loaded\n", 27);
}
static void emit(const char *phase, int fd, unsigned long req, uintptr_t arg,
                 int ret, int err, const void *data, size_t size) {
    char buf[2048]; struct ts t = {0}; clock_gettime(1, &t);
    int n = snprintf(buf, sizeof(buf), "GH-IOCTL %ld.%09ld pid=%d tid=%d %s fd=%d req=%#lx arg=%#lx ret=%d errno=%d bytes=",
                     t.sec, t.nsec, getpid(), gettid(), phase, fd, req, (unsigned long)arg, ret, err);
    const unsigned char *b = data;
    if (size > 256) size = 256;
    for (size_t i=0; i<size; i++) n += snprintf(buf+n, sizeof(buf)-(size_t)n, "%02x", b[i]);
    buf[n++]='\n'; write(2, buf, (size_t)n);
}
int ioctl(int fd, unsigned long request, ...) {
    va_list ap; va_start(ap, request); uintptr_t arg = va_arg(ap, uintptr_t); va_end(ap);
    unsigned long req = (unsigned int)request;
    unsigned type = (req >> 8) & 255, size = (req >> 16) & 16383;
    int gh = type == 'G' || type == 'A';
    if (gh && req != 0x4705) {
        emit("enter", fd, req, arg, 0, 0, (void *)arg, arg ? size : 0);
        if ((req == 0x40104704 || req == 0x40104707 || req == 0x40104116) && arg) {
            const uint32_t *desc = (void *)arg;
            uintptr_t nested = *(const uint64_t *)(arg+8);
            emit("nested", fd, req, nested, 0, 0, (void *)nested, nested ? desc[1] : 0);
        }
    }
    int result = real_ioctl(fd, request, arg);
    int saved = *__errno();
    if (gh && req != 0x4705) emit("return", fd, req, arg, result, result < 0 ? saved : 0, (void *)arg, arg ? size : 0);
    if (req == 0x40104704 && result >= 0 && arg && *(uint32_t *)arg == 1 && nvcpus < 32)
        vcpus[nvcpus++] = (struct vcpu){result, 0, 0};
    if (req == 0x4705) {
        for (unsigned i=0;i<nvcpus;i++) if (vcpus[i].fd == fd) {
            unsigned reason = vcpus[i].run ? *(uint32_t *)((char *)vcpus[i].run+8) : 0;
            if (++vcpus[i].calls <= 2 || result < 0 || reason >= 2)
                emit("vcpu-return", fd, req, arg, result, result < 0 ? saved : 0, vcpus[i].run, vcpus[i].run ? 40 : 0);
            break;
        }
    }
    *__errno() = saved;
    return result;
}
static void track_map(void *r, size_t size, int fd, long off) {
    for (unsigned i=0;i<nvcpus;i++) if (vcpus[i].fd==fd && r!=(void *)-1L && off==0 && size>=40) {
        vcpus[i].run=r; emit("vcpu-mmap", fd, 0, (uintptr_t)r, 0, 0, 0, 0);
    }
}
void *mmap(void *a,size_t s,int p,int f,int fd,long o) {
    void *r=real_mmap(a,s,p,f,fd,o); int saved=*__errno(); track_map(r,s,fd,o); *__errno()=saved; return r;
}
void *mmap64(void *a,size_t s,int p,int f,int fd,long o) {
    void *r=real_mmap64(a,s,p,f,fd,o); int saved=*__errno(); track_map(r,s,fd,o); *__errno()=saved; return r;
}
