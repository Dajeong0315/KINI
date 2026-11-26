"""Local simulated device state for offline integration exercises."""
from dataclasses import dataclass
from typing import Literal

@dataclass
class VirtualFridge:
    state: Literal["offline", "synced", "interrupted"] = "offline"
    version: int = 0

    def sync(self, *, connected: bool):
        if connected:
            self.version += 1
            self.state = "synced"
        else:
            self.state = "interrupted"
        return self.state, self.version
