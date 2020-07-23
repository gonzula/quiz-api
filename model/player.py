#!/usr/bin/env python3

from uuid import uuid4


class Player:
    def __init__(self, name, points=0, id=None):
        self.name = name.strip()
        self.points = points
        self.id = id or str(uuid4())

    def to_json(self):
        return vars(self)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if type(self) is type(other):
            return self.id == other.id
        else:
            return False
