from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from unicorn import UC_MEM_FETCH_UNMAPPED, UC_MEM_WRITE_UNMAPPED
from unicorn.arm64_const import (
    UC_ARM64_REG_FP,
    UC_ARM64_REG_W13,
    UC_ARM64_REG_W14,
    UC_ARM64_REG_W15,
    UC_ARM64_REG_X0,
    UC_ARM64_REG_X1,
    UC_ARM64_REG_X2,
)

from ._structs import c_stat, c_timeval
from ._util import s_to_u64

if TYPE_CHECKING:
    from ._fs import VirtualFileSystem
    from ._vm import VM

logger = logging.getLogger(__name__)

IMPORT_ADDRESS = 0xA0000000
IMPORT_SIZE = 0x1000


@dataclass()
class HookContext:
    vm: VM
    fs: VirtualFileSystem


def _hook_empty_stub(ctx: HookContext, orig_name: str | None = None) -> None:
    if orig_name is not None:
        logger.debug("%s(???) - (empty stubbed)", orig_name)
    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_malloc(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    logger.debug("malloc(0x%X)", x0)
    x0 = ctx.vm.malloc(x0)
    ctx.vm.reg_write(UC_ARM64_REG_X0, x0)


def _hook_free(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    logger.debug("free(0x%X)", x0)

    ctx.vm.free(x0)

    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_strncpy(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    x2 = ctx.vm.reg_read(UC_ARM64_REG_X2)

    p_dst = x0
    p_src = x1
    _len = x2

    src = ctx.vm.read_cstr(p_src)
    if len(src) > _len:
        msg = f"Read data too long: {len(src)} > {_len}"
        raise RuntimeError(msg)

    padding_size = _len - len(src)
    data = src + b"\x00" * padding_size

    ctx.vm.mem_write(p_dst, data)

    ctx.vm.reg_write(UC_ARM64_REG_X0, p_dst)


def _hook_mkdir(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    path = ctx.vm.read_cstr(x0).decode("utf-8")
    mode = x1

    logger.debug("mkdir('%s', %s)", path, oct(mode))

    assert path in [
        "./anisette",
    ]
    assert mode == 0o777
    ctx.fs.mkdir(path)  # FIXME: mode?

    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_umask(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)

    cmask = x0

    cmask = 0o777

    ctx.vm.reg_write(UC_ARM64_REG_X0, cmask)


def _hook_chmod(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    path = ctx.vm.read_cstr(x0).decode("utf-8")
    mode = x1

    logger.debug("chmod('%s', %s)", path, oct(mode))

    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _handle_stat(ctx: HookContext, path_or_fd: str | int, buf: int) -> None:
    try:
        stat_result = ctx.fs.stat(path_or_fd)
        # print(statResult)
    except FileNotFoundError:
        logger.debug("Unable to stat '%s'", path_or_fd)
        ctx.vm.reg_write(UC_ARM64_REG_X0, s_to_u64(-1))
        ctx.vm.set_errno(2)  # ENOENT
        return

    stat = c_stat(
        st_dev=0,
        st_ino=0,
        st_mode=stat_result.st_mode,
        # ...
        st_size=stat_result.st_size,
        st_blksize=512,
        st_blocks=(stat_result.st_size + 511) // 512,
        # ...s
    )
    stat.__byte = stat_result.st_size  # noqa: SLF001
    stat_bytes = bytes(stat)
    # print(statBytes.hex(), len(statBytes))

    # logger.debug("%s %s %s", stat_result.st_size, stat_result.st_blksize, stat_result.st_blocks)
    logger.debug("%s %s %s", stat.st_size, stat.st_blksize, stat.st_blocks)

    logger.debug("0x%X = %d", stat_result.st_mode, stat_result.st_mode)
    stat_bytes = b"".join(
        [
            bytes.fromhex(
                "00000000"
                "00000000"  # st_dev
                "00000000"
                "00000000",
            )  # st_ino
            + int.to_bytes(stat_result.st_mode, 4, "little")  # st_mode
            + bytes.fromhex(
                "00000000"  # st_nlink
                "a4810000"  # st_uid
                "00000000"  # st_gid
                "00000000"
                "00000000"  # st_rdev
                "00000000"
                "00000000",
            ),  # __pad1
            int.to_bytes(stat_result.st_size, 8, "little"),  # st_size
            bytes.fromhex(
                "00000000"  # st_blksize
                "00000000"  # __pad2
                "00000000"
                "00000000"  # st_blocks
                "00000000"
                "00000000"  # st_atime
                "00000000"
                "00000000" +  # st_atime_nsec
                # "00" * 4 +
                "00" * 2
                + "01" * 2
                + "00000000"  # st_mtime [This must have a valid value]
                "00000000"
                "00000000"  # st_mtime_nsec
                "00000000"
                "00000000"  # st_ctime
                "00000000"
                "00000000"  # st_ctime_nsec
                "00000000"  # __unused4
                "00000000",  # __unused5
            ),
        ],
    )
    logger.debug(len(stat_bytes))
    assert len(stat_bytes) in [104, 128]

    ctx.vm.mem_write(buf, stat_bytes)

    # Return success
    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_lstat(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    p_path = x0
    path = ctx.vm.read_cstr(p_path).decode("utf-8")
    buf = x1

    logger.debug("lstat(0x%X:'%s', [...])", p_path, path)

    return _handle_stat(ctx, path, buf)


def _hook_fstat(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    fildes = x0
    buf = x1

    return _handle_stat(ctx, fildes, buf)


def _hook_open(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    x2 = ctx.vm.reg_read(UC_ARM64_REG_X2)

    path = ctx.vm.read_cstr(x0).decode("utf-8")
    oflag = x1
    mode = x2

    logger.debug("open('%s', %s, %s)", path, oct(oflag), oct(mode))
    # time.sleep(2.0)
    # assert(False)

    # Return fildes
    fildes = ctx.fs.open(path, oflag)
    ctx.vm.reg_write(UC_ARM64_REG_X0, fildes)


def _hook_ftruncate(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    fildes = x0
    length = x1

    logger.debug("ftruncate(%d, %d)", fildes, length)

    ctx.fs.truncate(fildes, length)

    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_read(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    x2 = ctx.vm.reg_read(UC_ARM64_REG_X2)

    fildes = x0
    buf = x1
    nbyte = x2

    logger.debug("read(%d, 0x%X, %d)", fildes, buf, nbyte)

    buf_bytes = ctx.fs.read(fildes, nbyte)
    ctx.vm.mem_write(buf, buf_bytes)

    ctx.vm.reg_write(UC_ARM64_REG_X0, nbyte)


def _hook_write(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    x2 = ctx.vm.reg_read(UC_ARM64_REG_X2)

    fildes = x0
    buf = x1
    nbyte = x2

    logger.debug("write(%d, 0x%X, %d)", fildes, buf, nbyte)

    buf_bytes = ctx.vm.mem_read(buf, nbyte)
    ctx.fs.write(fildes, buf_bytes)

    ctx.vm.reg_write(UC_ARM64_REG_X0, nbyte)


def _hook_close(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)

    fildes = x0

    ctx.fs.close(fildes)

    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_dlopen_wrapper(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    path = ctx.vm.read_cstr(x0).decode("utf-8")
    library_name = path.rpartition("/")[2]

    logger.debug("dlopen('%s' (%s))", path, library_name)

    assert library_name in [
        "libCoreADI.so",
    ]

    library = ctx.vm.load_library(library_name)
    x0 = library.index
    ctx.vm.reg_write(UC_ARM64_REG_X0, 1 + x0)

    # assert(False)


def _hook_dlsym_wrapper(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    handle = x0
    symbol = ctx.vm.read_cstr(x1).decode("utf-8")

    library_index = handle - 1
    library = ctx.vm.get_library(library_index)

    logger.debug("dlsym(%X (%s), '%s')", handle, library.name, symbol)

    symbol_address = library.resolve_symbol_by_name(symbol)
    logger.debug("Found at 0x%X", symbol_address)

    ctx.vm.reg_write(UC_ARM64_REG_X0, symbol_address)


def _hook_gettimeofday(ctx: HookContext) -> None:
    timestamp = time.time()

    # FIXME: why need caching??
    cache_time = False
    cache_path = "cache/time.bin"

    if cache_time:
        t_bytes = open(cache_path, "rb").read()  # noqa: PTH123, SIM115
        logger.debug("Loaded time from cache!")
        t = c_timeval.from_buffer_copy(t_bytes)
        logger.debug("ok: %s", t)

    t = c_timeval(
        tv_sec=math.floor(timestamp // 1),
        tv_usec=math.floor((timestamp % 1.0) * 1000 * 1000),
    )
    t_bytes = bytes(t)

    if cache_time:
        open(cache_path, "wb").write(t_bytes)  # noqa: PTH123, SIM115

    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)

    tp = x0
    tzp = x1

    logger.debug("gettimeofday(0x%X, 0x%X)", tp, tzp)

    # We don't need timezone support
    assert tzp == 0
    """
    struct timezone {
             int     tz_minuteswest; /* of Greenwich */
             int     tz_dsttime;     /* type of dst correction to apply */
    };
    """

    # Write the time
    logger.debug("%s %s %s", t.__dict__, t_bytes.hex(), len(t_bytes))
    ctx.vm.mem_write(tp, t_bytes)

    # Return success
    ctx.vm.reg_write(UC_ARM64_REG_X0, 0)


def _hook_errno_location(ctx: HookContext) -> None:
    if ctx.vm.errno_address is None:
        logger.debug("Checking errno before first error (!)")
        ctx.vm.set_errno(0)
        assert ctx.vm.errno_address is not None

    ctx.vm.reg_write(UC_ARM64_REG_X0, ctx.vm.errno_address)


def _hook_system_property_get_impl(ctx: HookContext) -> None:
    x0 = ctx.vm.reg_read(UC_ARM64_REG_X0)
    x1 = ctx.vm.reg_read(UC_ARM64_REG_X1)
    name = ctx.vm.read_cstr(x0).decode("utf-8")
    logger.debug("__system_property_get(%s, [...])", name)
    value = b"no s/n number"
    ctx.vm.mem_write(x1, value)
    ctx.vm.reg_write(UC_ARM64_REG_X0, len(value))


def _hook_arc4random_impl(ctx: HookContext) -> None:
    value = 0xDEADBEEF  # "Random number, chosen by fair dice roll"
    ctx.vm.reg_write(UC_ARM64_REG_X0, value)


STUBBED_FUNCTIONS = {
    # memory management
    "malloc": _hook_malloc,
    "free": _hook_free,
    # string
    "strncpy": _hook_strncpy,
    # fs
    "mkdir": _hook_mkdir,
    "umask": _hook_umask,
    "chmod": _hook_chmod,
    "lstat": _hook_lstat,
    "fstat": _hook_fstat,
    # io
    "open": _hook_open,
    "ftruncate": _hook_ftruncate,
    "read": _hook_read,
    "write": _hook_write,
    "close": _hook_close,
    # dynamic symbol stuff
    "dlsym": _hook_dlsym_wrapper,
    "dlopen": _hook_dlopen_wrapper,
    "dlclose": lambda ctx: _hook_empty_stub(ctx, "dlclose"),
    # pthreads
    "pthread_once": lambda ctx: _hook_empty_stub(ctx, "pthread_once"),
    "pthread_create": lambda ctx: _hook_empty_stub(ctx, "pthread_create"),
    "pthread_mutex_lock": lambda ctx: _hook_empty_stub(ctx, "pthread_mutex_lock"),
    "pthread_rwlock_unlock": lambda ctx: _hook_empty_stub(ctx, "pthread_rwlock_unlock"),
    "pthread_rwlock_destroy": lambda ctx: _hook_empty_stub(ctx, "pthread_rwlock_destroy"),
    "pthread_rwlock_wrlock": lambda ctx: _hook_empty_stub(ctx, "pthread_rwlock_wrlock"),
    "pthread_rwlock_init": lambda ctx: _hook_empty_stub(ctx, "pthread_rwlock_init"),
    "pthread_mutex_unlock": lambda ctx: _hook_empty_stub(ctx, "pthread_mutex_unlock"),
    "pthread_rwlock_rdlock": lambda ctx: _hook_empty_stub(ctx, "pthread_rwlock_rdlock"),
    # date and time
    "gettimeofday": _hook_gettimeofday,
    # misc
    "__errno": _hook_errno_location,
    "__system_property_get": _hook_system_property_get_impl,
    "arc4random": _hook_arc4random_impl,
}


def hook_mem_invalid(_ctx: HookContext, access: int, address: int, size: int, value: int) -> None:
    if access == UC_MEM_WRITE_UNMAPPED:
        logger.error(
            ">>> Missing memory is being WRITE at 0x%x, data size = %u, data value = 0x%x",
            address,
            size,
            value,
        )
        # return True to indicate we want to continue emulation
        # return False
    elif access == UC_MEM_FETCH_UNMAPPED:
        logger.error(
            ">>> Missing memory is being FETCH at 0x%x, data size = %u, data value = 0x%x",
            address,
            size,
            value,
        )
    else:
        # return False to indicate we want to stop emulation
        # return False
        msg = f"Unsupported access mode: {access}"
        raise RuntimeError(msg)


def hook_code(ctx: HookContext, address: int, size: int) -> None:
    logs = ""
    logs += f">>> Tracing at 0x{address:X}:"
    # read this instruction code from memory
    for i in ctx.vm.mem_read(address, size):
        logs += f" {i:02X}"
    for i in [3, 8, 9, 10, 11, 20]:
        value = ctx.vm.reg_read(UC_ARM64_REG_X0 + i)
        logs += f"; X{i:d}: 0x{value:08X}"
    logs += f"; W13=0x{ctx.vm.reg_read(UC_ARM64_REG_W13):X}"
    logs += f"; W14=0x{ctx.vm.reg_read(UC_ARM64_REG_W14):X}"
    logs += f"; W15=0x{ctx.vm.reg_read(UC_ARM64_REG_W15):X}"
    logs += f"; FP/X29=0x{ctx.vm.reg_read(UC_ARM64_REG_FP):X}"
    # print("; *347c40=0x%08X" % int.from_bytes(uc.mem_read(0x347c40, 4), 'little'), end="")
    logger.debug(logs)


def hook_block(ctx: HookContext, _address: int, _size: int) -> None:
    pass
    # print("         >>> Tracing basic block at 0x%x, block size = 0x%x" %(address, size))


def hook_stub(ctx: HookContext, address: int, _size: int) -> bool:
    assert address >= IMPORT_ADDRESS
    assert address < IMPORT_ADDRESS + 0x01000000 * 10

    offset = address - IMPORT_ADDRESS
    library_index = offset // 0x01000000
    symbol_index = (offset % 0x01000000) // 4

    # assert(libraryIndex == 0)
    library = ctx.vm.get_library(library_index)

    symbol_name = library.symbol_name_by_index(symbol_index)

    # lr = uc.reg_read(UC_ARM64_REG_LR)

    # print("stub", "0x%X" % lr, uc, address, size, user_data, end=" :: ")
    # print(libraryIndex, library.name, symbolIndex, symbolName)

    if symbol_name in STUBBED_FUNCTIONS:
        STUBBED_FUNCTIONS[symbol_name](ctx)
        # assert(False)
    else:
        msg = f"Symbol not in stubbed functions: {symbol_name}"
        raise RuntimeError(msg)

    # time.sleep(0.1)
    return True
