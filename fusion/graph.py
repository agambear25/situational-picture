"""Union-find + connected components. Deterministic component ordering."""
from __future__ import annotations


class UnionFind:
    def __init__(self, items):
        self._parent = {x: x for x in items}
        self._rank = {x: 0 for x in items}

    def add(self, x):
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0

    def find(self, x):
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # path compression
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def components(self) -> list[list]:
        """Return components, each sorted, the list sorted by first member. Deterministic."""
        groups: dict = {}
        for x in self._parent:
            r = self.find(x)
            groups.setdefault(r, []).append(x)
        comps = [sorted(members) for members in groups.values()]
        comps.sort(key=lambda m: m[0])
        return comps
