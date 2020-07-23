#!/usr/bin/env python3

from uuid import uuid4

from pdf2image import convert_from_bytes


class Game:
    def __init__(self, host, id=None):
        self.host = host
        self.id = id or str(uuid4()).replace('-', '')
        self.players = {}
        self.current_slide = None
        self.slide_paths = []

    def load_presentation(self, file_obj):
        self.slide_paths = convert_from_bytes(
            file_obj.read(),
            output_folder='/tmp',
            fmt='png',
            paths_only=True)
        self.current_slide = 0

    def path_for_slide(self, n):
        return self.slide_paths[n]

    def to_json(self):
        players = sorted(self.players.values(),
                         key=lambda p: p.id)
        return {
            'id': self.id,
            'number_of_slides': len(self.slide_paths),
            'current_slide': self.current_slide,
            'players': [p.to_json() for p in players],
        }

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if type(self) is type(other):
            return self.id == other.id
        else:
            return False
