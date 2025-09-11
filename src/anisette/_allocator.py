from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Allocator:
    _PAGE_SIZE = 0xFF

    def __init__(self, base: int, size: int) -> None:
        self._base = base
        self._size = size

        # allocated blocks: addr -> size
        self._free_blocks: list[tuple[int, int]] = [(self._base, self._base + self._size)]
        self._alloc_size: dict[int, int] = {}

    @property
    def alloc_size(self) -> int:
        return sum(self._alloc_size.values())

    @property
    def alloc_perc(self) -> float:
        return self.alloc_size / self._size

    def _find_free_block(self, size: int) -> int | None:
        return next((i for i, block in enumerate(self._free_blocks) if (block[1] - block[0]) >= size), None)

    def _claim_block(self, block_i: int, size: int) -> int:
        block_start, block_end = self._free_blocks.pop(block_i)

        new_start = block_start + size
        assert new_start <= block_end
        if new_start != block_end:
            self._free_blocks.insert(0, (block_start + size, block_end))

        self._alloc_size[block_start] = size

        return block_start

    def _create_free_block(self, address: int, size: int) -> None:
        blocks = [
            (-1, self._base),
            *self._free_blocks,
            (self._base + self._size, -1),
        ]
        for i, ((_, start), (end, _)) in enumerate(zip(blocks, blocks[1:])):
            if start <= address <= end:  # addr lies in this allocated (non-free) block
                new_block = (address, address + size)

                if self._free_blocks[i - 1][1] == new_block[0]:
                    # can merge with previous free block
                    self._free_blocks[i - 1] = (self._free_blocks[i - 1][0], new_block[1])

                    if new_block[1] == self._free_blocks[i][0]:
                        # can *also* merge with next block
                        self._free_blocks[i - 1] = (self._free_blocks[i - 1][0], self._free_blocks[i][1])
                        del self._free_blocks[i]
                elif new_block[1] == self._free_blocks[i][0]:
                    # can *only* merge with next free block
                    self._free_blocks[i] = (new_block[0], self._free_blocks[i][1])
                else:
                    # cannot merge
                    self._free_blocks.insert(i, new_block)

                return

        logger.warning("Could not free 0x%x: address not allocated?", address)

    def alloc(self, size: int) -> tuple[int, int]:
        length = (size + self._PAGE_SIZE) & ~self._PAGE_SIZE  # Align to pagesize bytes
        block_i = self._find_free_block(length)
        if block_i is None:
            msg = "Cannot alloc more memory: allocator is full!"
            raise RuntimeError(msg)

        address = self._claim_block(block_i, length)

        logger.debug("Allocating %d bytes (align: %d) at 0x%x", size, length, address)
        logger.debug("Allocator base: 0x%x, size: 0x%x", self._base, self._size)
        logger.debug("New alloc size: %x", self.alloc_size)

        return address, length

    def free(self, address: int) -> None:
        size = self._alloc_size.pop(address, None)
        if size is None:
            logger.warning("Tried to free memory at 0x%X, but never allocated", address)
            return

        self._create_free_block(address, size)

        logger.debug("Freed %x bytes at %x, new alloc size: %x", size, address, self.alloc_size)
