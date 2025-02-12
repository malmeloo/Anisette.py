class Allocator:
    def __init__(self, base: int, size: int) -> None:
        self.__base = base
        self.__size = size
        self.__offset = 0

    def alloc(self, size: int) -> int:
        address = self.__base + self.__offset

        # Align to pagesize bytes
        length = size
        length += 0xFFF
        length &= ~0xFFF

        self.__offset += length
        assert self.__offset < self.__base + self.__size

        return address
