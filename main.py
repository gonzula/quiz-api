#!/usr/bin/env python3

import base64
import pickle
from time import time

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_redis import FlaskRedis
from flask_socketio import SocketIO, emit, join_room, leave_room
from unidecode import unidecode

from model.game import Game
from model.player import Player

BLOCKED_HAND_TIMEOUT = 7000  # ms
COMMAND_DELTA = 1250  # ms

REDIS_URL = "redis://@localhost:6379/0"

app = Flask(__name__)
redis_client = FlaskRedis(app)
socketio = SocketIO(app, cors_allowed_origins="*")

cors = CORS(app, resources={r"/*": {"origins": "*"}})


@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    emit('joined', f'has entered the room {room}.', room=room)
    print(f'has entered the room {room}.')


@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)
    emit('left', f'has left the room {room}.', room=room)
    print(f'has left the room {room}.')


@socketio.on('change_points')
def on_change_points(data):
    host_id = data['host_id']
    player_id = data['player_id']
    game_id = data['game_id']
    points = data['points']

    game = retrieve_game(game_id)
    if game.host.id != host_id:
        return

    game.players[player_id].points = points
    save_game(game)
    players = sorted(game.players.values(),
                     key=lambda p: p.id)
    players = [p.to_json() for p in players]
    emit('changed_players',
         {'players': players},
         room=game_id)


@socketio.on('change_slide')
def on_change_slide(data):
    player_id = data['player_id']
    game_id = data['game_id']
    current_slide = data['current_slide']

    game = retrieve_game(game_id)
    allowed_range = range(0, len(game.slide_paths))
    if current_slide not in allowed_range:
        return
    if game.host.id != player_id:
        return

    print('change slide to', current_slide)

    game.current_slide = current_slide
    save_game(game)
    will_execute_at = epoch() + COMMAND_DELTA
    block_from_raising_hand(game_id, will_execute_at)
    emit('changed_slide',
         {'current_slide': current_slide, 'will_execute_at': will_execute_at},
         room=game_id)

    reset_hands(game, will_execute_at)


@socketio.on('reset_hands')
def ask_to_reset_hands(data):
    player_id = data['player_id']
    game_id = data['game_id']

    game = retrieve_game(game_id)

    if game.host.id != player_id:
        return

    reset_hands(game, epoch())


def reset_hands(game, time):
    redis_client.delete(f'hands.{game.id}')
    redis_client.set(f'stopwatch.{game.id}', pickle.dumps(time))

    notify_raised_hands(game)


@socketio.on('raise_hand')
def on_raise_hand(data):
    player_id = data['player_id']
    game_id = data['game_id']
    was_executed_at = data['was_executed_at']

    if is_blocked(player_id, was_executed_at):
        return

    block_from_raising_hand(player_id, epoch() + BLOCKED_HAND_TIMEOUT)

    game = retrieve_game(game_id)
    if game.host.id == player_id:
        will_execute_at = epoch() + COMMAND_DELTA
        reset_hands(game, will_execute_at)
        block_from_raising_hand(game_id, will_execute_at)
        emit('fire',
             {'will_execute_at': will_execute_at},
             room=game.id)
    elif not is_blocked(game_id, was_executed_at):
        key_name = f'hands.{game_id}'
        print(f'{player_id} raised hand')
        redis_client.zadd(key_name, {player_id: was_executed_at}, nx=True)
        notify_raised_hands(game)


def is_blocked(id, check_time):
    key = f'blocked.{id}'
    blocked_until = redis_client.get(key)
    if blocked_until is None:
        return False

    blocked_until = pickle.loads(blocked_until)

    return check_time <= blocked_until


def block_from_raising_hand(id, blocked_until):
    key = f'blocked.{id}'
    blocked_until = pickle.dumps(blocked_until)
    redis_client.set(key, blocked_until)


def notify_raised_hands(game):
    stopwatch_start = pickle.loads(redis_client.get(f'stopwatch.{game.id}'))

    key_name = f'hands.{game.id}'
    hands = redis_client.zrange(key_name, 0, -1, withscores=True)
    hands = [(h.decode('ascii'), t) for h, t in hands]
    hands = [(h, t - stopwatch_start) for h, t in hands]
    players = [{'player': game.players[p].name, 'delay': t} for p, t in hands]
    print('raised hands', players)
    emit('raised_hands', {'hands': players}, room=game.id)


@app.route('/new_game', methods=['POST'])
def new_game():
    print(request.form)
    host_name = 'Host'
    presentation_file = request.files['presentation_file']
    host = Player(host_name)
    game = Game(host)
    game.load_presentation(presentation_file)

    redis_client.set(f'stopwatch.{game.id}', pickle.dumps(epoch()))
    save_game(game)

    return jsonify(game_id=game.id, player_id=host.id)


@app.route('/new_player', methods=['POST'])
def new_player():
    game_id = request.form['game_id'].strip()
    player_name = request.form['player_name'].strip()[:20]
    if not player_name:
        return jsonify(ok=False)
    game = retrieve_game(game_id)

    existing_players = game.players.values()
    existing_players = [p.name for p in existing_players]
    existing_players = [unidecode(p).lower() for p in existing_players]

    if unidecode(player_name).lower() in existing_players:
        raise KeyError

    player = Player(player_name)
    print(f'new player named "{player.name}"')
    game.players[player.id] = player
    save_game(game)

    players = sorted(game.players.values(),
                     key=lambda p: p.id)
    players = [p.to_json() for p in players]
    socketio.emit('changed_players',
                  {'players': players},
                  room=game_id)

    return jsonify(game_id=game.id, player_id=player.id)


@app.route('/game')
def get_game():
    game_id = request.args['game_id']
    player_id = request.args['player_id']
    game = retrieve_game(game_id)

    return jsonify(game=game.to_json(),
                   is_host=player_id == game.host.id)


@app.route('/slide')
def get_slide():
    print(request.args)
    game_id = request.args['game_id']
    slide = int(request.args['slide'])
    game = retrieve_game(game_id)

    slide_path = game.path_for_slide(slide)
    return send_file(
            slide_path,
            mimetype='image/jpg',
            )


@app.route('/epoch')
def get_epoch():
    return jsonify(epoch=epoch())


def retrieve_game(game_id):
    return pickle.loads(redis_client.get(game_id))


def save_game(game):
    redis_client.set(game.id, pickle.dumps(game))


def epoch():
    return time() * 1000


if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0')
