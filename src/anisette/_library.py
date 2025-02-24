from __future__ import annotations

import logging
from typing import IO, TYPE_CHECKING, BinaryIO

from elftools.elf.sections import SymbolTableSection
from fs.errors import CreateFailed, ResourceNotFound
from fs.tarfs import TarFS
from fs.zipfs import ZipFS
from typing_extensions import Self

from ._arch import Architecture
from ._fs import VirtualFileSystem

if TYPE_CHECKING:
    from elftools.elf.elffile import ELFFile
    from fs.base import FS


logging.getLogger(__name__)

R_AARCH64_ABS64 = 257
R_AARCH64_GLOB_DAT = 1025
R_AARCH64_JUMP_SLOT = 1026
R_AARCH64_RELATIVE = 1027


class Library:
    def __init__(self, name: str, elf: ELFFile, base: int, index: int) -> None:
        self.name = name
        self.elf = elf
        self.base = base
        self.symbols = {}
        self.index = index

    def resolve_symbol_by_index(self, symbol_index: int) -> int:
        # for section in elf.iter_sections():
        #   print(section)
        if symbol_index in self.symbols:
            # print("Resolving symbol 0x%X from symbols dict" % symbolIndex)
            return self.symbols[symbol_index]

        section = self.elf.get_section_by_name(".dynsym")
        assert isinstance(section, SymbolTableSection)

        sym = section.get_symbol(symbol_index)
        # print("Resolving symbol 0x%X relative to base" % symbolIndex, sym.__dict__)

        # if sym['st_shndx'] == 11:
        #    section = library.elf.get_section(sym['st_shndx'])
        #    print("Fixing section", section.__dict__)
        #    print("0x%X" % (section['sh_addr'] + sym['st_value']))
        #    assert(False)

        return self.base + sym["st_value"]

    def resolve_symbol_by_name(self, symbol_name: str) -> int:
        section = self.elf.get_section_by_name(".dynsym")
        assert isinstance(section, SymbolTableSection)

        num_symbols = section.num_symbols()
        for i in range(num_symbols):
            sym = section.get_symbol(i)
            if sym.name == symbol_name:
                # print(sym.__dict__)
                return self.resolve_symbol_by_index(i)

        msg = f"Symbol '{symbol_name}' not found"
        raise ValueError(msg)

    def symbol_name_by_index(self, symbol_index: int) -> str:
        section = self.elf.get_section_by_name(".dynsym")
        assert isinstance(section, SymbolTableSection)

        sym = section.get_symbol(symbol_index)
        return sym.name


class LibraryStore(VirtualFileSystem):
    _LIBRARIES = (
        "libstoreservicescore.so",
        "libCoreADI.so",
    )
    _ARCH = Architecture.ARM64

    def __init__(self, fs: FS | None) -> None:
        super().__init__(fs)

    def open_library(self, name: str) -> IO:
        return self.easy_open(name, "rb")

    def add_library(self, name: str, data: IO[bytes]) -> None:
        with self.easy_open(name, "wb+") as f:
            f.write(data.read())

    @classmethod
    def from_virtfs(cls, fs: VirtualFileSystem) -> Self:
        return cls(fs.fs)

    @classmethod
    def from_fs(cls, fs: FS) -> Self:
        lib_store = cls(None)

        for lib in cls._LIBRARIES:
            possible_paths = (
                lib,
                f"libs/{lib}",
                f"lib/{cls._ARCH.value}/{lib}",
            )

            for path in possible_paths:
                try:
                    with fs.open(path, "rb") as f:
                        lib_store.add_library(lib, f)
                    break
                except ResourceNotFound:
                    pass
            else:
                msg = "FS is missing library file: %s"
                raise RuntimeError(msg, lib)

        return lib_store

    @classmethod
    def from_file(cls, file: BinaryIO) -> Self:
        try:
            with TarFS(file) as f:
                return cls.from_fs(f)
        except CreateFailed:
            pass

        try:
            with ZipFS(file) as f:
                return cls.from_fs(f)
        except CreateFailed:
            pass

        msg = "Unknown file format"
        raise TypeError(msg)
