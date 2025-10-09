from ctypes import Structure, c_int, c_long, c_size_t, c_uint32, c_uint64, c_ulong

# Based on https://github.com/Dadoum/Provision/blob/main/lib/std_edit/linux_stat.d (aarch64)
# FIXME: These must be changed to fixed size types
c_dev_t = c_uint32
c_off_t = c_size_t
c_ino_t = c_uint64
c_mode_t = c_uint32  # c_ushort
c_nlink_t = c_uint32
c_uid_t = c_uint32
c_gid_t = c_uint32
c_blksize_t = c_ulong
c_blkcnt_t = c_uint64
c_time_t = c_uint64
c_suseconds_t = c_long


class c_timeval(Structure):  # noqa: N801
    _fields_ = (
        ("tv_sec", c_time_t),  # /* seconds since Jan. 1, 1970 */
        ("tv_usec", c_suseconds_t),  # /* and microseconds */
    )


class c_stat(Structure):  # noqa: N801
    _fields_ = (
        ("st_dev", c_dev_t),  # /* ID of device containing file */
        ("st_ino", c_ino_t),  # /* inode number */
        ("st_mode", c_mode_t),  # /* protection */
        ("st_nlink", c_nlink_t),  # /* number of hard links */
        ("st_uid", c_uid_t),  # /* user ID of owner */
        ("st_gid", c_gid_t),  # /* group ID of owner */
        ("st_rdev", c_dev_t),  # /* device ID (if special file) */
        ("__pad1", c_dev_t),  # ???
        ("st_size", c_off_t),  # /* total size, in bytes */
        ("st_blksize", c_blksize_t),  # /* blocksize for file system I/O */
        ("__pad2", c_int),  # ???
        ("st_blocks", c_blkcnt_t),  # /* number of 512B blocks allocated */
        ("st_atime", c_time_t),  # /* time of last access */
        ("st_atimensec", c_ulong),  # ?!?!
        ("st_mtime", c_time_t),  # /* time of last modification */
        ("st_mtimensec", c_ulong),  # ?!?!
        ("st_ctime", c_time_t),  # /* time of last status change */
        ("st_ctimensec", c_ulong),  # ?!?!
        ("__unused_0", c_int),  # ???
        ("__unused_1", c_int),  # ???
    )


# From https://chromium.googlesource.com/android_tools/+/20ee6d20/ndk/platforms/android-21/arch-arm64/usr/include/sys/stat.h
"""
  unsigned long st_dev; \
  unsigned long st_ino; \
  unsigned int st_mode; \
  unsigned int st_nlink; \
  uid_t st_uid; \
  gid_t st_gid; \
  unsigned long st_rdev; \
  unsigned long __pad1; \
  long st_size; \
  int st_blksize; \
  int __pad2; \
  long st_blocks; \
  long st_atime; \
  unsigned long st_atime_nsec; \
  long st_mtime; \
  unsigned long st_mtime_nsec; \
  long st_ctime; \
  unsigned long st_ctime_nsec; \
  unsigned int __unused4; \
  unsigned int __unused5; \
"""
