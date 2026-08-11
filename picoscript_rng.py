"""Checkpointable deterministic RNG streams for PicoScript providers."""

from dataclasses import dataclass

MASK = (1 << 64) - 1


def _mix(x):
    x = (x + 0x9E3779B97F4A7C15) & MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK
    return (x ^ (x >> 31)) & MASK


@dataclass
class RandomStream:
    seed: int
    stream_id: int
    position: int = 0

    def next_u64(self):
        value = _mix((self.seed & MASK) ^ _mix(self.stream_id & MASK) ^ self.position)
        self.position += 1
        return value

    def below(self, upper: int):
        if upper <= 0 or upper > (1 << 64):
            raise ValueError("upper must be in 1..2^64")
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % upper

    def checkpoint(self):
        return (self.seed & MASK, self.stream_id & MASK, self.position & MASK)

    @classmethod
    def restore(cls, state):
        if len(state) != 3 or any(not 0 <= x <= MASK for x in state):
            raise ValueError("invalid RNG checkpoint")
        return cls(*state)
