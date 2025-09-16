from __future__ import annotations

import io
import logging
import tarfile
import zipfile
from typing import IO, TYPE_CHECKING, BinaryIO

from elftools.elf.sections import SymbolTableSection
from typing_extensions import Self

from ._arch import Architecture
from ._fs import VirtualFileSystem

if TYPE_CHECKING:
    from elftools.elf.elffile import ELFFile


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

    def __init__(self, fs: VirtualFileSystem | None) -> None:
        super().__init__(fs)

    def open_library(self, name: str) -> IO:
        return self.easy_open(name, "rb")

    def add_library(self, name: str, data: IO[bytes]) -> None:
        with self.easy_open(name, "wb+") as f:
            f.write(data.read())

    @staticmethod
    def _candidates_for(lib: str, arch: Architecture) -> tuple[str, str, str]:
        return (
            lib,
            f"libs/{lib}",
            f"lib/{arch.value}/{lib}",
        )

    @classmethod
    def _load_from_tar(cls, f: BinaryIO, lib_store: LibraryStore) -> bool:
        try:
            with tarfile.open(fileobj=f, mode="r:*") as tf:
                names = {m.name for m in tf.getmembers() if m.isfile()}
                for lib in cls._LIBRARIES:
                    for path in cls._candidates_for(lib, cls._ARCH):
                        if path in names:
                            member = tf.getmember(path)
                            data = tf.extractfile(member)
                            if data is None:
                                continue
                            with data:
                                lib_store.add_library(lib, data)
                            break
                    else:
                        msg = "Archive is missing library file: %s"
                        raise RuntimeError(msg % lib)
        except tarfile.ReadError:
            return False
        else:
            return True

    @classmethod
    def _load_from_zip(cls, f: BinaryIO, lib_store: LibraryStore) -> bool:
        try:
            with zipfile.ZipFile(f) as zf:
                names = set(zf.namelist())
                for lib in cls._LIBRARIES:
                    for path in cls._candidates_for(lib, cls._ARCH):
                        if path in names:
                            with zf.open(path, "r") as data:
                                lib_store.add_library(lib, data)
                            break
                    else:
                        msg = "Archive is missing library file: %s"
                        raise RuntimeError(msg % lib)
        except zipfile.BadZipFile:
            return False
        else:
            return True

    @classmethod
    def from_file(cls, file: BinaryIO) -> Self:
        """Load libraries from a tar or zip archive using only stdlib."""
        lib_store = cls(None)

        # Buffer the file so we can attempt multiple formats without relying on seekability.
        data = file.read()
        buf1 = io.BytesIO(data)
        if cls._load_from_tar(buf1, lib_store):
            return lib_store

        buf2 = io.BytesIO(data)
        if cls._load_from_zip(buf2, lib_store):
            return lib_store

        msg = "Unknown file format"
        raise TypeError(msg)
