import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Content:
    text: str
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        fingerprint = hashlib.sha256(self.text.encode("utf-8")).hexdigest()

        object.__setattr__(self, "sha256", fingerprint)
