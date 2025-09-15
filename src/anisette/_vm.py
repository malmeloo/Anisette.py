from __future__ import annotations

import io as _io
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from elftools.elf.sections import SymbolTableSection
from unicorn import (
    UC_ARCH_ARM,
    UC_ARCH_ARM64,
    UC_ARCH_X86,
    UC_HOOK_BLOCK,
    UC_HOOK_CODE,
    UC_HOOK_MEM_FETCH_UNMAPPED,
    UC_HOOK_MEM_READ_UNMAPPED,
    UC_HOOK_MEM_WRITE_UNMAPPED,
    UC_MODE_32,
    UC_MODE_64,
    UC_MODE_ARM,
)
from unicorn.arm64_const import (
    UC_ARM64_REG_LR,
    UC_ARM64_REG_SP,
    UC_ARM64_REG_X0,
)
from unicorn.unicorn import Uc

from ._allocator import Allocator
from ._arch import Architecture
from ._hooks import HookContext, hook_block, hook_mem_invalid, hook_stub
from ._library import (
    R_AARCH64_ABS64,
    R_AARCH64_GLOB_DAT,
    R_AARCH64_JUMP_SLOT,
    R_AARCH64_RELATIVE,
    Library,
    LibraryStore,
)

if TYPE_CHECKING:
    from ._fs import VirtualFileSystem

logger = logging.getLogger(__name__)

RETURN_ADDRESS = 0xDEAD0000
STACK_ADDRESS = 0xF0000000
STACK_SIZE = 0x100000

MALLOC_ADDRESS = 0x60000000
MALLOC_SIZE = 0x1000000

TEMP_ADDRESS = 0x800000000
TEMP_SIZE = 0x100000

IMPORT_ADDRESS = 0xA0000000
IMPORT_SIZE = 0x1000


class VM:
    def __init__(self, uc: Uc, fs: VirtualFileSystem, lib_store: LibraryStore) -> None:
        self._uc = uc
        self._fs = fs

        self._lib_store = lib_store
        self._loaded_libs: dict[str, Library] = OrderedDict()

        self._temp_allocator = Allocator(TEMP_ADDRESS, TEMP_SIZE)
        self._malloc_allocator = Allocator(MALLOC_ADDRESS, MALLOC_SIZE)
        self._lib_allocator = Allocator(0x00100000, 0x90000000)

        self._errno_address: int | None = None

    @property
    def alloc_stats(self) -> tuple[float, float, float]:
        return (
            self._temp_allocator.alloc_perc,
            self._malloc_allocator.alloc_perc,
            self._lib_allocator.alloc_perc,
        )

    @property
    def errno_address(self) -> int | None:
        return self._errno_address

    def wrap_hook(self, hook: Callable) -> Callable:
        def _new_hook(_uc: Uc, *args: Any) -> None:  # noqa: ANN401
            ctx = HookContext(vm=self, fs=self._fs)
            return hook(ctx, *(args[:-1]))

        return _new_hook

    @classmethod
    def create(cls, fs: VirtualFileSystem, lib_store: LibraryStore, arch: Architecture) -> VM:
        # Startup a unicorn-engine instance as VM backend
        if arch == Architecture.X86:
            uc = Uc(UC_ARCH_X86, UC_MODE_32)
        elif arch == Architecture.X86_64:
            uc = Uc(UC_ARCH_X86, UC_MODE_64)
        elif arch == Architecture.ARM:
            uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        elif arch == Architecture.ARM64:
            uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        else:
            msg = "Invalid architecture: %s"
            raise ValueError(msg, arch)

        # Register a fake return address
        uc.mem_map(RETURN_ADDRESS, 0x1000)

        # Register some memory for malloc
        uc.mem_map(MALLOC_ADDRESS, MALLOC_SIZE)

        # Register memory for temp data
        uc.mem_map(TEMP_ADDRESS, TEMP_SIZE)

        # Register a fake stack
        uc.mem_map(STACK_ADDRESS, STACK_SIZE)

        vm = cls(uc, fs, lib_store)

        # Debug hooks
        uc.hook_add(UC_HOOK_BLOCK, vm.wrap_hook(hook_block))
        # uc.hook_add(UC_HOOK_CODE, hook_code, vm)
        uc.hook_add(
            UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED | UC_HOOK_MEM_FETCH_UNMAPPED,
            vm.wrap_hook(hook_mem_invalid),
        )

        # Add a region for imports
        import_count = IMPORT_SIZE // 4
        for i in range(10):
            library_import_address = IMPORT_ADDRESS + i * 0x01000000
            uc.mem_map(library_import_address, IMPORT_SIZE)
            uc.mem_write(library_import_address, b"\xc0\x03\x5f\xd6" * import_count)  # RET instruction
            uc.hook_add(
                UC_HOOK_CODE,
                vm.wrap_hook(hook_stub),
                None,
                library_import_address,
                library_import_address + IMPORT_SIZE - 1,
            )

        return vm

    def malloc(self, length: int) -> int:
        return self._malloc_allocator.alloc(length)[0]

    def free(self, address: int) -> None:
        return self._malloc_allocator.free(address)

    def mem_write(self, address: int, data: bytes) -> None:
        self._uc.mem_write(address, data)

    def mem_read(self, address: int, length: int) -> bytes:
        return bytes(self._uc.mem_read(address, length))

    def reg_write(self, reg_id: int, value: int) -> None:
        return self._uc.reg_write(reg_id, value)

    def reg_read(self, reg_id: int) -> int:
        return self._uc.reg_read(reg_id)

    def write_u64(self, address: int, value: int) -> None:
        return self.mem_write(
            address,
            int.to_bytes(value, 8, "little", signed=False),
        )

    def read_u64(self, address: int) -> int:
        return int.from_bytes(
            self.mem_read(address, 8),
            "little",
            signed=False,
        )

    def write_u32(self, address: int, value: int) -> None:
        return self.mem_write(
            address,
            int.to_bytes(value, 4, "little", signed=False),
        )

    def read_u32(self, address: int) -> int:
        return int.from_bytes(
            self.mem_read(address, 4),
            "little",
            signed=False,
        )

    def read_cstr(self, address: int) -> bytes:
        max_length = 0x1000
        s = self.mem_read(address, max_length)
        s, terminator, _ = s.partition(b"\x00")
        assert terminator == b"\x00"
        return s

    def set_errno(self, value: int) -> None:
        if self._errno_address is None:
            self._errno_address = self.temp_alloc(4)
        self.write_u32(self._errno_address, value)

    def temp_alloc_data(self, data: bytes) -> int:
        data_size = len(data)
        address, alloc_size = self._temp_allocator.alloc(data_size + 1)

        logger.debug("Allocating at 0x%X; bytes 0x%X/0x%X", address, data_size, alloc_size)
        self.mem_write(address, data + b"\xcc" * alloc_size)

        return address

    def temp_alloc(self, size: int) -> int:
        return self.temp_alloc_data(b"\xaa" * size)

    def temp_free(self, address: int) -> None:
        return self._temp_allocator.free(address)

    def invoke_cdecl(self, address: int, args: list[int]) -> int:
        lr = RETURN_ADDRESS
        for i, value in enumerate(args):
            assert i <= 28
            self.reg_write(UC_ARM64_REG_X0 + i, value)
            logger.debug("X%d: 0x%08X", i, value)
        logger.debug("Calling 0x%X", address)
        self.reg_write(UC_ARM64_REG_SP, STACK_ADDRESS + STACK_SIZE)
        self.reg_write(UC_ARM64_REG_LR, lr)
        # uc.reg_write(UC_ARM64_REG_FP, stackAddress + stackSize)
        self._uc.emu_start(address, lr)
        return self.reg_read(UC_ARM64_REG_X0)

    def relocate_section(self, library: Library, section_name: str, base: int) -> None:
        reladyn = library.elf.get_section_by_name(section_name)
        assert isinstance(reladyn, RelocationSection)

        for reloc in reladyn.iter_relocations():
            # print('    Relocation (%s)' % 'RELA' if reloc.is_RELA() else 'REL', end="")
            # Relocation entry attributes are available through item lookup
            # print('      offset = 0x%X' % reloc['r_offset'])
            # print("%s" % reloc.__dict__, end="")
            # print("")

            info_type = reloc["r_info_type"]
            address = base + reloc["r_offset"]

            if info_type in (R_AARCH64_ABS64, R_AARCH64_GLOB_DAT):
                symbol_index = reloc["r_info_sym"]
                symbol_address = library.resolve_symbol_by_index(symbol_index)
                self.mem_write(
                    address,
                    int.to_bytes(symbol_address + reloc["r_addend"], 8, "little"),
                )  # b'\x12\x34\x22\x78\xAB\xCD\xEF\xFF')
            elif info_type == R_AARCH64_JUMP_SLOT:
                symbol_index = reloc["r_info_sym"]
                symbol_address = library.resolve_symbol_by_index(symbol_index)
                self.mem_write(
                    address,
                    int.to_bytes(symbol_address, 8, "little"),
                )  # b'\x12\x34\x11\x78\xAB\xCD\xEF\xFF')
            elif info_type == R_AARCH64_RELATIVE:
                self.mem_write(
                    address,
                    int.to_bytes(base + reloc["r_addend"], 8, "little"),
                )  # b'\x12\x34\x22\x78\xAB\xCD\xEF\xFF')
            else:
                msg = "Invalid reloc info type: %d"
                raise RuntimeError(msg, info_type)

    def load_library(self, name: str) -> Library:
        if name in self._loaded_libs:
            return self._loaded_libs[name]

        library_index = len(self._loaded_libs)
        with self._lib_store.open_library(name) as f:
            elf_data = f.read()
            # Construct ELF from an in-memory buffer to avoid lifecycle issues of the context-managed stream
            elf = ELFFile(_io.BytesIO(elf_data))

        chosen_base = self._lib_allocator.alloc(0x10000000)[0]

        library = Library(name, elf, chosen_base, library_index)

        # Stub all imports
        section = library.elf.get_section_by_name(".dynsym")
        assert isinstance(section, SymbolTableSection)
        num_symbols = section.num_symbols()
        for i in range(num_symbols):
            sym = section.get_symbol(i)
            # print(sym.name)

            # print(sym.__dict__)
            # print(sym['st_shndx'])
            if sym["st_shndx"] == "SHN_UNDEF":
                library.symbols[i] = IMPORT_ADDRESS + library.index * 0x01000000 + i * 4
                # print("Registering 0x%X: %s" % (library.symbols[i], sym.name))

                # print("%s: 0x%X" % (sym.name, resolveSymbolByIndex(library, i)))

        for segment in library.elf.iter_segments():
            address = library.base + segment["p_vaddr"]
            size = segment["p_memsz"]

            address_start = address
            address_end = address + size

            alignment = segment["p_align"]

            # Align the start
            address_start &= ~(alignment - 1)

            # Align the end
            address_end += alignment - 1
            address_end &= ~(alignment - 1)

            # Fix size for new alignment
            size = address_end - address

            data_offset = segment["p_offset"]
            data_size = segment["p_filesz"]
            padding_before_size = address - address_start
            padding_after_size = size - data_size

            logger.debug(
                "Mapping at 0x%X-0x%X (0x%X-0x%X); bytes 0x%X",
                address_start,
                address_end,
                address,
                address + size - 1,
                size,
            )

            if segment["p_type"] == "PT_LOAD":
                data = (
                    b"\x00" * padding_before_size
                    + elf_data[data_offset : data_offset + data_size]
                    + b"\x00" * padding_after_size
                )
                self._uc.mem_map(address_start, len(data))
                self.mem_write(address_start, data)
            else:
                logger.debug("- Skipping %s", segment.__dict__)

        self.relocate_section(library, ".rela.dyn", library.base)
        self.relocate_section(library, ".rela.plt", library.base)

        self._loaded_libs[name] = library

        return library

    def get_library(self, index: int) -> Library:
        return list(self._loaded_libs.values())[index]
