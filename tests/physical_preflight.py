"""Explicit synthetic admission for semantic tests; never a native token proof."""


class SyntheticPhysicalPreflight:
    async def preflight_generation(self, instruction, payload, schema):
        return True
