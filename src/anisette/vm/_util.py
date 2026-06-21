def u_to_s32(value: int) -> int:
    b = int.to_bytes(value, 4, "little", signed=False)
    return int.from_bytes(b, "little", signed=True)


def s_to_u64(value: int) -> int:
    b = int.to_bytes(value, 8, "little", signed=True)
    return int.from_bytes(b, "little", signed=False)
